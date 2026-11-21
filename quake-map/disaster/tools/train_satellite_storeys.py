"""Satellite-supervised storey model (MASTER_PLAN 1.5, revised after a label-bias finding).

Why not train on mapped `num_floors` labels: those labels are biased towards apartment blocks. A model trained on them predicted
3.0 storeys on average for Noida's ordinary buildings where Google's heights say 2.2, and importance weighting did not remove the
bias (docs/ml_attributes.md). Here the model instead learns from Google 2.5D heights where they are usable (>= 2 m, spread <= 6 m;
about 2/3 of buildings, a population-representative, label-free sample) using ONLY footprint shape and neighbourhood features (no height
features), and is then applied to the buildings where the height product fails (small, low buildings).

  python tools/train_satellite_storeys.py [--per-city 40000]

Evaluation: leave-one-city-out and held-out 2 km tiles; truth = Google storey bin (height / 2.8 m); baseline = the rule-based heuristic
(area-based guess) calibrated on the training cities; also the bias in mean storeys; also agreement with mapped floor labels on the
buildings it would be used for. Ship rule (written before running): beats the baseline on pooled held-out log-loss with a 90% tile-cluster
interval above zero, in at least 2/3 of cities, with |mean storey bias| <= 0.3, and not worse than the baseline on small footprints (< 60 m2).
"""
import argparse, json, pathlib, sys, time
import numpy as np, pandas as pd
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE / "tools"))
import attrs_ml as A, exposure, heights
import train_attributes as T

REP = np.array([1.0, 2.0, 3.4, 5.8, 10.0])      # representative storeys per bin (mean of the bin)

def build_city(city, per_city, seed=0):
    """Sample of usable-height buildings with features (no height features), Google storey bin, heuristic bin, plus every labelled building
    that the model would be used for (unusable height) for the label agreement check. Cached per city."""
    import geopandas as gpd
    d = HERE / "data" / "ml" / city
    f = d / "satellite.parquet"
    if f.exists():
        return pd.read_parquet(f)
    raw = gpd.read_parquet(d / "raw.parquet")
    raw = raw[raw.geometry.type.isin(["Polygon", "MultiPolygon"])].reset_index(drop=True)
    ht = heights.building_heights(raw.geometry.values, d)
    utm = raw.estimate_utm_crs(); u = raw.to_crs(utm); area = u.area.to_numpy()
    usable = ((ht.h_google >= 2) & (ht.h_spread <= 6)).to_numpy()
    nf = pd.to_numeric(raw.get("num_floors"), errors="coerce").to_numpy()
    labelled_unusable = np.flatnonzero(~usable & np.isfinite(nf) & (nf >= 1) & (nf <= 40))
    rng = np.random.default_rng(seed)
    pos = np.flatnonzero(usable)
    rows = np.sort(np.r_[rng.choice(pos, min(per_city, len(pos)), replace=False), labelled_unusable])
    X = A.features(raw.geometry.values, None, utm=utm, rows=rows)
    guess = exposure.attributes_full(raw[[]].copy(), area, None)["levels"].to_numpy()
    cen = u.geometry.centroid
    out = X.assign(city=city, usable=usable[rows], area_m2=area[rows], google_bin=np.where(usable[rows], A.storey_bin(np.clip(np.round(ht.h_google.to_numpy()[rows] / heights.STOREY_M), 1, 40)), -1),
                   rule_bin=A.storey_bin(guess[rows]), label_bin=np.where(np.isfinite(nf[rows]) & (nf[rows] >= 1) & (nf[rows] <= 40), A.storey_bin(np.clip(nf[rows], 1, 40)), -1),
                   tile=[f"{city}:{int(x // 2000)}:{int(y // 2000)}" for x, y in zip(cen.x.to_numpy()[rows], cen.y.to_numpy()[rows])])
    out.to_parquet(f)
    return out

def base_probs(train, test):
    ct = pd.crosstab(train.rule_bin.astype(str), train.google_bin).reindex(columns=range(5), fill_value=0)
    ov = (ct.sum() + 1) / (ct.sum() + 1).sum()
    sm = (ct + 1).div((ct + 1).sum(axis=1), axis=0)
    P = np.tile(ov.to_numpy(), (len(test), 1))
    k = test.rule_bin.astype(str).isin(sm.index).to_numpy()
    P[k] = sm.loc[test.rule_bin.astype(str)[k]].to_numpy()
    return P

def model_probs(train, test, feats):
    import lightgbm as lgb
    b = lgb.train(dict(T.PARAMS, num_class=5), lgb.Dataset(train[feats], train.google_bin.to_numpy()), T.ROUNDS)
    return b, b.predict(test[feats])

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--per-city", type=int, default=40000); ap.add_argument("--no-save", action="store_true")
    a = ap.parse_args()
    cities = [p.name for p in sorted((HERE / "data" / "ml").iterdir()) if (p / "raw.parquet").exists() and any(p.glob("h*_*.tif"))]
    data = pd.concat([build_city(c, a.per_city) for c in cities], ignore_index=True)
    feats = [c for c in data.columns if c not in ("city", "usable", "area_m2", "google_bin", "rule_bin", "label_bin", "tile")]
    tr_all = data[data.usable].reset_index(drop=True)
    rows = []
    for c in cities:                                          # leave one city out
        te = tr_all[tr_all.city == c]; tr = tr_all[tr_all.city != c]
        Pb = base_probs(tr, te); _, Pm = model_probs(tr, te, feats)
        y = te.google_bin.to_numpy()
        rows.append(pd.DataFrame({"city": c, "tile": te.tile.to_numpy(), "small": (te.area_m2 < 60).to_numpy(), "y": y, "ll_base": T.logloss_each(Pb, y), "ll_ml": T.logloss_each(Pm, y),
                                  "mean_true": REP[y], "mean_ml": Pm @ REP, "mean_base": Pb @ REP}))
    res = pd.concat(rows, ignore_index=True)
    res["rule_src"] = "guess"
    summ = {"all": T.summarize(res, "leave-city-out, usable-height buildings", np.ones(len(res), bool)),
            "small_footprints": T.summarize(res, "leave-city-out, footprints < 60 m2", res.small.to_numpy())}
    for k in ("mean_true", "mean_ml", "mean_base"):
        summ[k] = float(res[k].mean())
    summ["bias_ml"], summ["bias_base"] = summ["mean_ml"] - summ["mean_true"], summ["mean_base"] - summ["mean_true"]
    summ["bias_ml_small"] = float((res.mean_ml - res.mean_true)[res.small].mean()); summ["bias_base_small"] = float((res.mean_base - res.mean_true)[res.small].mean())
    # agreement with mapped floor labels on the buildings the model would be used for (height unusable)
    app = data[(~data.usable) & (data.label_bin >= 0)]
    agree = None
    if len(app):
        import lightgbm as lgb
        b = lgb.train(dict(T.PARAMS, num_class=5), lgb.Dataset(tr_all[feats], tr_all.google_bin.to_numpy()), T.ROUNDS)
        P = b.predict(app[feats]); Pb = base_probs(tr_all, app)
        y = app.label_bin.to_numpy()
        agree = {"n": int(len(app)), "ll_ml": float(T.logloss_each(P, y).mean()), "ll_base": float(T.logloss_each(Pb, y).mean()), "mean_label_storeys": float(REP[y].mean()),
                 "mean_ml": float((P @ REP).mean()), "mean_base": float((Pb @ REP).mean()), "within_one_bin_ml": float((np.abs(P.argmax(1) - y) <= 1).mean()), "within_one_bin_base": float((np.abs(Pb.argmax(1) - y) <= 1).mean())}
    summ["labelled_unusable"] = agree
    s, sm_ = summ["all"], summ["small_footprints"]
    ship = bool(s["improvement"] > 0 and s["ci90"][0] > 0 and s["cities_won"] >= (2 * s["cities"] + 2) // 3 and abs(summ["bias_ml"]) <= 0.3 and sm_["improvement"] >= 0)
    summ["ship"] = ship; summ["cities"] = cities; summ["n_train"] = int(len(tr_all))
    (HERE / "data" / "ml" / "satellite_results.json").write_text(json.dumps(summ, indent=1, default=float))
    print(json.dumps({k: summ[k] for k in summ if k not in ("all", "small_footprints", "cities")}, indent=1, default=float))
    for k in ("all", "small_footprints"):
        v = summ[k]; print(f"{k}: n={v['n']} base {v['ll_base']:.3f} ml {v['ll_ml']:.3f} improvement {v['improvement']:+.3f} CI90 [{v['ci90'][0]:+.3f},{v['ci90'][1]:+.3f}] cities won {v['cities_won']}/{v['cities']}")
    print("SHIP:", ship)
    if ship and not a.no_save:
        import lightgbm as lgb
        b = lgb.train(dict(T.PARAMS, num_class=5), lgb.Dataset(tr_all[feats], tr_all.google_bin.to_numpy()), T.ROUNDS)
        m = HERE / "data" / "models"; m.mkdir(parents=True, exist_ok=True)
        b.save_model(str(m / "storeys_satellite.txt"))
        (m / "storeys_satellite.json").write_text(json.dumps({"features": feats, "trained_on": cities, "n_train": int(len(tr_all)), "rep_storeys": REP.tolist(), "rounds": T.ROUNDS,
                                                              "ll_ml": s["ll_ml"], "ll_base": s["ll_base"], "bias_ml": summ["bias_ml"]}, default=float))
        print("saved model")

if __name__ == "__main__":
    main()
