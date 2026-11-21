"""Train and evaluate the building-attribute model (MASTER_PLAN 1.5). Needs tools/get_ml_cities.py first.

  python tools/train_attributes.py [--cities ...] [--no-save]

Target 1: storey bin (1, 2, 3-4, 5-7, 8+) from Overture `num_floors`.  Target 2: use group from Overture `class`.
Baseline = the rule-based prior, computed WITHOUT storey/class labels (the situation of the ~99% unlabelled buildings) and calibrated
on the training cities: P(label | rule class) with Laplace smoothing. The model must beat it on cities it has never seen
(leave-one-city-out) and on held-out 2 km tiles within cities. Decision rule (written before looking at results): ship only if, on the
buildings where the model would be used (storeys not measured), it beats the baseline on pooled held-out log-loss with a 90%
tile-cluster bootstrap interval above zero AND in at least two thirds of the cities. Writes docs/ml_attributes.md and, if shipped,
data/models/building_attrs.json (+ boosters).
"""
import argparse, json, pathlib, sys, time
import numpy as np, pandas as pd
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import attrs_ml as A, exposure, heights

PARAMS = dict(objective="multiclass", learning_rate=0.05, num_leaves=15, min_data_in_leaf=40, feature_fraction=0.8, bagging_fraction=0.8,
              bagging_freq=1, lambda_l2=5.0, verbose=-1, num_threads=8, seed=1)
ROUNDS = 250

def load_city(city, force=False):
    """Labelled rows of a city with features, labels, rule-based storey class and tile id. Cached as data/ml/<city>/features.parquet."""
    import geopandas as gpd
    d = HERE / "data" / "ml" / city
    f = d / "features.parquet"
    if f.exists() and not force:
        return pd.read_parquet(f)
    raw = gpd.read_parquet(d / "raw.parquet")
    raw = raw[raw.geometry.type.isin(["Polygon", "MultiPolygon"])].reset_index(drop=True)
    geoms = raw.geometry.values
    ht = heights.building_heights(geoms, d)
    utm = raw.estimate_utm_crs()
    u = raw.to_crs(utm)
    area = u.area.to_numpy()
    nf = pd.to_numeric(raw.get("num_floors"), errors="coerce")
    cls = raw.get("class").astype(str).str.lower().map(exposure.LABEL) if "class" in raw else pd.Series(np.nan, index=raw.index)
    lab = (nf.notna() & (nf >= 1) & (nf <= 40)) | cls.notna()
    rows = np.flatnonzero(lab.to_numpy())
    # rule-based storeys WITHOUT any label (no class, subtype, height or floors): Google height where usable, else the area heuristic
    nolab = raw[[]].copy()
    d_rule = exposure.attributes_full(nolab, area, ht)
    X = A.features(geoms, ht, utm=utm, rows=rows)
    cen = u.geometry.centroid
    out = X.assign(city=city, storey_bin=np.where(nf.iloc[rows].notna() & (nf.iloc[rows] >= 1) & (nf.iloc[rows] <= 40), A.storey_bin(nf.iloc[rows].clip(1, 40)), -1),
                   use_group=np.where(cls.iloc[rows].notna(), A.use_group(cls.iloc[rows]), ""),
                   rule_bin=A.storey_bin(d_rule["levels"].to_numpy()[rows]), rule_src=d_rule["levels_src"].to_numpy()[rows],
                   rule_use=np.where(area[rows] < 20, "outbuilding", np.where(area[rows] >= 1500, "large", "residential")),
                   tile=[f"{city}:{int(x // 2000)}:{int(y // 2000)}" for x, y in zip(cen.x.to_numpy()[rows], cen.y.to_numpy()[rows])],
                   n_city=len(raw))
    out.to_parquet(f)
    return out

def baseline_table(train, label, rule_cols, n_classes):
    """Calibrated rule-based prior: P(label | rule class) with Laplace smoothing; unseen rule classes fall back to the overall mix."""
    key = train[rule_cols].astype(str).agg("|".join, axis=1) if len(rule_cols) > 1 else train[rule_cols[0]].astype(str)
    ct = pd.crosstab(key, train[label]).reindex(columns=range(n_classes), fill_value=0) if label == "storey_bin" else pd.crosstab(key, train[label])
    return ct

def predict_baseline(ct, test, rule_cols, classes):
    key = test[rule_cols].astype(str).agg("|".join, axis=1) if len(rule_cols) > 1 else test[rule_cols[0]].astype(str)
    ct = ct.reindex(columns=classes, fill_value=0)
    overall = (ct.sum() + 1) / (ct.sum() + 1).sum()
    P = np.tile(overall.to_numpy(), (len(test), 1))
    sm = (ct + 1).div((ct + 1).sum(axis=1), axis=0)
    known = key.isin(sm.index).to_numpy()
    P[known] = sm.loc[key[known]].to_numpy()
    return P

def logloss_each(P, y):
    return -np.log(np.clip(P[np.arange(len(y)), y], 1e-9, 1))

def fit_predict(train, test, label, classes, feats):
    import lightgbm as lgb
    y = train[label].map({c: i for i, c in enumerate(classes)}).to_numpy()
    p = dict(PARAMS, num_class=len(classes))
    bst = lgb.train(p, lgb.Dataset(train[feats], y), ROUNDS)
    return bst, bst.predict(test[feats])

def evaluate(data, label, classes, rule_cols, feats, groups_cv=True):
    """Leave-one-city-out and within-city tile folds. Returns per-row DataFrame with baseline and model log-loss."""
    d = data[data[label].isin(classes)].copy() if label == "use_group" else data[data[label] >= 0].copy()
    d = d.reset_index(drop=True)
    yi = d[label].map({c: i for i, c in enumerate(classes)}).to_numpy()
    res = pd.DataFrame({"city": d.city, "tile": d.tile, "rule_src": d.rule_src, "y": yi, "ll_base": np.nan, "ll_ml": np.nan, "pred_ml": -1, "pred_base": -1})
    for city in d.city.unique():                                   # leave one city out
        te = (d.city == city).to_numpy(); tr = ~te
        ct = baseline_table(d[tr], label, rule_cols, len(classes))
        Pb = predict_baseline(ct, d[te], rule_cols, classes)
        _, Pm = fit_predict(d[tr], d[te], label, classes, feats)
        res.loc[te, "ll_base"], res.loc[te, "ll_ml"] = logloss_each(Pb, yi[te]), logloss_each(Pm, yi[te])
        res.loc[te, "pred_ml"], res.loc[te, "pred_base"] = Pm.argmax(1), Pb.argmax(1)
    return res

def tile_cv(data, label, classes, rule_cols, feats, folds=5):
    """Within-city: whole 2 km tiles held out (5 folds per city), pooled."""
    d = data[data[label].isin(classes)].copy() if label == "use_group" else data[data[label] >= 0].copy()
    d = d.reset_index(drop=True)
    yi = d[label].map({c: i for i, c in enumerate(classes)}).to_numpy()
    rng = np.random.default_rng(0)
    fold = np.zeros(len(d), int)
    for city in d.city.unique():
        m = (d.city == city).to_numpy()
        tiles = d.tile[m].unique(); rng.shuffle(tiles)
        tf = {t: i % folds for i, t in enumerate(tiles)}
        fold[m] = d.tile[m].map(tf).to_numpy()
    out = np.full((len(d), 2), np.nan)
    for k in range(folds):
        te = fold == k; tr = ~te
        ct = baseline_table(d[tr], label, rule_cols, len(classes))
        Pb = predict_baseline(ct, d[te], rule_cols, classes)
        _, Pm = fit_predict(d[tr], d[te], label, classes, feats)
        out[te, 0], out[te, 1] = logloss_each(Pb, yi[te]), logloss_each(Pm, yi[te])
    return pd.DataFrame({"city": d.city, "tile": d.tile, "rule_src": d.rule_src, "ll_base": out[:, 0], "ll_ml": out[:, 1]})

def summarize(res, name, subset_mask, rng_seed=0, boots=300):
    """Pooled and per-city numbers on a subset, with a tile-cluster bootstrap interval on (baseline - model) log-loss."""
    r = res[subset_mask].dropna(subset=["ll_base", "ll_ml"])
    diff = r.ll_base - r.ll_ml                              # positive = model better
    by_city = r.groupby("city").apply(lambda g: pd.Series({"n": len(g), "ll_base": g.ll_base.mean(), "ll_ml": g.ll_ml.mean()}), include_groups=False)
    tiles = r.groupby("tile").apply(lambda g: pd.Series({"s": (g.ll_base - g.ll_ml).sum(), "n": len(g)}), include_groups=False)
    rng = np.random.default_rng(rng_seed)
    idx = np.arange(len(tiles))
    bs = []
    for _ in range(boots):
        pick = rng.choice(idx, len(idx))
        bs.append(tiles.s.to_numpy()[pick].sum() / tiles.n.to_numpy()[pick].sum())
    return {"name": name, "n": int(len(r)), "ll_base": float(r.ll_base.mean()), "ll_ml": float(r.ll_ml.mean()), "improvement": float(diff.mean()),
            "ci90": [float(np.percentile(bs, 5)), float(np.percentile(bs, 95))], "cities_won": int((by_city.ll_ml < by_city.ll_base).sum()), "cities": int(len(by_city)),
            "by_city": by_city.round(4).reset_index().to_dict("records")}

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--cities", nargs="+", default=None); ap.add_argument("--no-save", action="store_true"); ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    cities = a.cities or [p.name for p in sorted((HERE / "data" / "ml").iterdir()) if (p / "raw.parquet").exists() and any(p.glob("h*_*.tif"))]
    frames = []
    for c in cities:
        t = time.time(); f = load_city(c, a.force); frames.append(f)
        print(f"{c}: {len(f):,} labelled ({(f.storey_bin >= 0).sum():,} storeys, {(f.use_group != '').sum():,} use) of {int(f.n_city.iloc[0]):,} ({time.time() - t:.0f}s)", flush=True)
    data = pd.concat(frames, ignore_index=True)
    feats = [c for c in data.columns if c not in ("city", "storey_bin", "use_group", "rule_bin", "rule_src", "rule_use", "tile", "n_city")]
    results = {"cities": cities, "n_features": len(feats), "features": feats}
    # ---- storeys ----
    S = list(range(5))
    r_ll = evaluate(data, "storey_bin", S, ["rule_bin", "rule_src"], feats)
    t_ll = tile_cv(data, "storey_bin", S, ["rule_bin", "rule_src"], feats)
    app = lambda r: r.rule_src.isin(["guess", "guess_unstable"]).to_numpy()
    results["storeys"] = {"leave_city_out_all": summarize(r_ll, "leave-city-out, all labelled", np.ones(len(r_ll), bool)),
                          "leave_city_out_applied": summarize(r_ll, "leave-city-out, buildings without usable satellite height (where the model would be used)", app(r_ll)),
                          "tile_cv_all": summarize(t_ll, "held-out tiles, all labelled", np.ones(len(t_ll), bool)),
                          "tile_cv_applied": summarize(t_ll, "held-out tiles, buildings without usable satellite height", app(t_ll)),
                          "accuracy": {"ml": float((r_ll.pred_ml.astype(float) == r_ll.y).mean()), "baseline": float((r_ll.pred_base.astype(float) == r_ll.y).mean()),
                                       "class_prior_majority": float((r_ll.y == r_ll.y.mode()[0]).mean())}}
    # ---- use ----
    U = list(A.USE_GROUPS)
    d_use = data[data.use_group.isin(U)]
    if len(d_use) > 500:
        u_ll = evaluate(data, "use_group", U, ["rule_use"], feats)
        results["use"] = {"leave_city_out_all": summarize(u_ll, "use group, leave-city-out", np.ones(len(u_ll), bool)), "n": int(len(d_use)),
                          "share_residential": float((d_use.use_group == "residential").mean())}
    s = results["storeys"]["leave_city_out_applied"]; t = results["storeys"]["tile_cv_applied"]
    ship = bool(s["improvement"] > 0 and s["ci90"][0] > 0 and s["cities_won"] >= (2 * s["cities"] + 2) // 3 and t["improvement"] > 0 and t["ci90"][0] > 0)
    results["ship"] = ship
    out = HERE / "data" / "ml"; out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(results, indent=1, default=float))
    print(json.dumps({k: results["storeys"][k] for k in ("leave_city_out_all", "leave_city_out_applied", "tile_cv_applied")}, indent=1, default=float)[:2500])
    print("SHIP:", ship)
    if ship and not a.no_save:
        import lightgbm as lgb
        d = data[data.storey_bin >= 0]
        y = d.storey_bin.to_numpy()
        bst = lgb.train(dict(PARAMS, num_class=5), lgb.Dataset(d[feats], y), ROUNDS)
        mdir = HERE / "data" / "models"; mdir.mkdir(parents=True, exist_ok=True)
        bst.save_model(str(mdir / "storeys.txt"))
        (mdir / "building_attrs.json").write_text(json.dumps({"features": feats, "trained_on": cities, "rounds": ROUNDS, "params": PARAMS, "n_train": int(len(d)),
                                                              "ll_ml_applied": s["ll_ml"], "ll_base_applied": s["ll_base"]}, default=float))
        print("saved model")

if __name__ == "__main__":
    main()
