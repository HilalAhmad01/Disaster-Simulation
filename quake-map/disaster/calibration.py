"""Fit lognormal fragility curves to observed damage counts (MASTER_PLAN 3.2-3.5).

Model per building class: P(DS >= k | x) = Phi((x - ln theta_k) / beta), k = 1..4 (slight..complete), x = ln PGA (g),
theta_1 < theta_2 < theta_3 < theta_4. Observations are grouped by (class, ward): counts per damage state with the ward's
mean ln PGA m_g and spread s_g (within-ward variation plus the ShakeMap's own sigma). Shaking error is integrated out:
for Gaussian x ~ N(m, s^2) the integral of Phi((x - mu)/beta) is Phi((m - mu)/sqrt(beta^2 + s^2)) exactly, which is what
Gauss-Hermite quadrature converges to (tests/test_calibration.py compares both). beta is therefore the capacity-only
dispersion, not inflated by shaking uncertainty.

Truncation: the Nepal registration lists DAMAGED houses only, so observations are conditional on DS >= 1. Pass
truncated=True (default) to condition on that; with undamaged counts in column 0 use truncated=False.
"""
import json, pathlib
import numpy as np, pandas as pd
from scipy.optimize import minimize
from scipy.special import ndtr

STATES = ("none", "slight", "moderate", "extensive", "complete")

def _unpack(p):
    """params -> (ln theta (4,), beta). ln theta_1 = p0; increments softplus(p1..p3) + 0.02 keep the medians ordered."""
    lt = np.empty(4)
    lt[0] = p[0]
    lt[1:] = p[0] + np.cumsum(np.logaddexp(0, p[1:4]) + 0.02)
    return lt, float(np.exp(p[4]))

def _pack(lt, beta):
    inc = np.maximum(np.diff(lt) - 0.02, 1e-6)
    return np.concatenate([[lt[0]], np.log(np.expm1(inc)), [np.log(beta)]])

def exceedance(x, lt, beta, s=0.0):
    """P(DS >= k) for k = 1..4, shape (len(x), 4); x = ln PGA means, s = spread of ln PGA inside each group."""
    x, s = np.asarray(x, float)[:, None], np.asarray(s, float)
    s = s[:, None] if s.ndim else s
    return ndtr((x - np.asarray(lt)[None, :]) / np.sqrt(beta ** 2 + s ** 2))

def state_probs(x, lt, beta, s=0.0):
    """P(DS = 0..4), shape (n, 5)."""
    E = exceedance(x, lt, beta, s)
    full = np.concatenate([np.ones((len(E), 1)), E, np.zeros((len(E), 1))], axis=1)
    return np.clip(full[:, :-1] - full[:, 1:], 1e-300, 1.0)

def neg_loglik(p, x, s, counts, truncated=True):
    lt, beta = _unpack(p)
    P = state_probs(x, lt, beta, s)
    if truncated:
        P = P[:, 1:] / np.clip(P[:, 1:].sum(1, keepdims=True), 1e-300, None)
        n = counts[:, 1:]
    else:
        n = counts
    return -float((n * np.log(np.clip(P, 1e-300, None))).sum())

def fit(x, s, counts, truncated=True, start=None):
    """Maximum-likelihood fit. x, s: (G,) ward mean/spread of ln PGA; counts: (G, 5) damage-state counts (col 0 = undamaged,
    ignored when truncated). Returns dict(theta (4,), beta, nll, success). Several starts guard against local minima."""
    x, s, counts = np.asarray(x, float), np.asarray(s, float), np.asarray(counts, float)
    best = None
    starts = [start] if start is not None else [_pack(np.log([a, b, c, d]), beta) for a, b, c, d, beta in
              [(0.1, 0.2, 0.35, 0.6, 0.6), (0.2, 0.4, 0.7, 1.2, 0.5), (0.05, 0.12, 0.25, 0.5, 0.8), (0.3, 0.5, 0.8, 1.2, 0.4)]]
    for p0 in starts:
        r = minimize(neg_loglik, p0, args=(x, s, counts, truncated), method="L-BFGS-B")
        if best is None or r.fun < best.fun:
            best = r
    lt, beta = _unpack(best.x)
    return {"theta": np.exp(lt), "beta": beta, "nll": float(best.fun), "success": bool(best.success), "p": best.x}

def bootstrap(group_ward, x, s, counts, mle, n=200, seed=0, truncated=True):
    """Resample WARDS with replacement (all classes' rows of a ward move together), refit from the MLE.
    group_ward: (G,) ward id per row. Returns array (n, 5) of theta_1..4, beta."""
    rng = np.random.default_rng(seed)
    wards = pd.unique(group_ward)
    idx_by = {w: np.where(group_ward == w)[0] for w in wards}
    out = []
    for _ in range(n):
        pick = rng.choice(wards, len(wards), replace=True)
        rows = np.concatenate([idx_by[w] for w in pick])
        r = fit(x[rows], s[rows], counts[rows], truncated, start=mle["p"])
        out.append(np.append(r["theta"], r["beta"]))
    return np.array(out)

def summarize(mle, boot):
    """JSON-friendly result with 90% bootstrap intervals and the CI-width rule used in MASTER_PLAN 3.5."""
    lo, hi = np.percentile(boot, [5, 95], axis=0)
    names = ["theta_slight", "theta_moderate", "theta_extensive", "theta_complete", "beta"]
    vals = list(mle["theta"]) + [mle["beta"]]
    d = {n: {"mle": float(v), "ci90": [float(a), float(b)]} for n, v, a, b in zip(names, vals, lo, hi)}
    d["ci_width_over_theta_extensive"] = float((hi[2] - lo[2]) / mle["theta"][2])
    d["narrow_ci"] = bool(d["ci_width_over_theta_extensive"] < 0.5)          # rule: CI width < 50% of theta_3
    d["bootstrap_samples"] = boot.round(5).tolist()
    return d

def empirical_curves(x, counts, bins=8, truncated=True):
    """Observed exceedance share by PGA bin (for plots): rows (pga_mid, n, share DS>=k for k=2..4 [and 1 if untruncated])."""
    x, counts = np.asarray(x), np.asarray(counts, float)
    edges = np.quantile(x, np.linspace(0, 1, bins + 1))
    edges[-1] += 1e-9
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        m = (x >= a) & (x < b)
        c = counts[m].sum(0)
        base = c[1:].sum() if truncated else c.sum()
        if base <= 0:
            continue
        ge = np.cumsum(c[::-1])[::-1]                              # counts with DS >= k
        out.append((float(np.exp(x[m].mean())), float(base), *[float(ge[k] / base) for k in (2, 3, 4)]))
    return pd.DataFrame(out, columns=["pga", "n", "ge2", "ge3", "ge4"])

def plot_fit(path, name, x, counts, res, truncated=True):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    emp = empirical_curves(x, counts, truncated=truncated)
    g = np.exp(np.linspace(np.log(0.03), np.log(1.5), 120))
    P = state_probs(np.log(g), np.log(res["theta"]), res["beta"])
    fig, ax = plt.subplots(figsize=(6, 4))
    for k, col in zip((2, 3, 4), ("#f2b705", "#e07a1f", "#d7263d")):
        cond = P[:, k:].sum(1) / P[:, 1:].sum(1) if truncated else P[:, k:].sum(1)
        ax.plot(g, cond, color=col, label=f"fitted P(DS>={STATES[k]}{' | damaged' if truncated else ''})")
        ax.scatter(emp.pga, emp[f"ge{k}"], color=col, s=18)
    ax.set_xscale("log"); ax.set_xlabel("PGA (g)"); ax.set_ylabel("share of buildings"); ax.set_title(name); ax.legend(fontsize=7)
    fig.tight_layout(); pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True); fig.savefig(path, dpi=130); plt.close(fig)

# ---------------- ward shaking from a ShakeMap (MASTER_PLAN 3.2) ----------------
def read_shakemap_grid(grid_xml, uncertainty_xml=None):
    """USGS ShakeMap grid.xml -> DataFrame(lon, lat, pga_g, [sigma_lnpga]). PGA in the file is %g."""
    import xml.etree.ElementTree as ET
    def parse(path):
        root = ET.parse(path).getroot()
        ns = {"s": root.tag.split("}")[0].strip("{")} if "}" in root.tag else {}
        q = lambda tag, el=root: el.findall(f"s:{tag}", ns) if ns else el.findall(tag)
        fields = {f.get("name").lower(): int(f.get("index")) - 1 for f in q("grid_field")}
        data = q("grid_data")[0].text.split()
        n = len(fields)
        arr = np.array(data, float).reshape(-1, n)
        return pd.DataFrame({k: arr[:, i] for k, i in fields.items()})
    g = parse(grid_xml)
    out = pd.DataFrame({"lon": g["lon"], "lat": g["lat"], "pga_g": g["pga"] / 100.0})
    if uncertainty_xml:
        u = parse(uncertainty_xml)
        col = next((c for c in u.columns if c.startswith("stdpga")), None)
        if col:
            out["sigma_lnpga"] = u[col].to_numpy()
    return out

def ward_pga(grid, wards, ward_col, pop_points=None):
    """Population-weighted mean and spread of ln PGA per ward. grid: DataFrame lon, lat, pga_g, [sigma_lnpga];
    wards: GeoDataFrame (EPSG:4326) with column ward_col; pop_points: optional (lon, lat, people) arrays: grid points are
    weighted by the population of the nearest pixel (plain equal weights otherwise). Returns DataFrame indexed by ward:
    mean_lnpga, sd_lnpga (population-weighted spread inside the ward), sigma_shakemap (mean ShakeMap sigma), n_points."""
    import geopandas as gpd
    pts = gpd.GeoDataFrame(grid.copy(), geometry=gpd.points_from_xy(grid.lon, grid.lat), crs=4326)
    j = gpd.sjoin(pts, wards[[ward_col, "geometry"]], predicate="within")
    if pop_points is not None:
        from scipy.spatial import cKDTree
        plon, plat, pv = pop_points
        d, i = cKDTree(np.column_stack([plon, plat])).query(np.column_stack([j.lon, j.lat]))
        j["_w"] = pv[i] + 1e-6
    else:
        j["_w"] = 1.0
    j["_x"] = np.log(j.pga_g.clip(lower=1e-6))
    has_sig = "sigma_lnpga" in j
    def agg(d):
        w = d["_w"] / d["_w"].sum()
        m = float((w * d["_x"]).sum())
        return pd.Series({"mean_lnpga": m, "sd_lnpga": float(np.sqrt((w * (d["_x"] - m) ** 2).sum())),
                          "sigma_shakemap": float(d["sigma_lnpga"].mean()) if has_sig else np.nan, "n_points": len(d)})
    return j.groupby(ward_col)[["_x", "_w"] + (["sigma_lnpga"] if has_sig else [])].apply(agg)

# ---------------- survey classes and damage grades (MASTER_PLAN 3.3) ----------------
GRADE_TO_STATE = {1: 1, 2: 2, 3: 3, 4: 4, 5: 4}              # slight, moderate, extensive, complete, complete
GRADE_TO_STATE_ALT = {1: 1, 2: 2, 3: 3, 4: 3, 5: 4}          # sensitivity: only grade 5 = complete

def classify_nepal(df, cols=None):
    """Map Nepal survey rows to model classes. Default column names follow the NSO/NPC household registration variables as
    they appear in the public derived tables (VERIFY against the real file's data dictionary):
      has_superstructure_cement_mortar_brick, has_superstructure_rc_non_engineered, has_superstructure_rc_engineered,
      count_floors_pre_eq. Returns a Series of 'URM' / 'RC_LOW' / 'RC_MID' / None (None = not used for fitting:
      mud-mortar stone, adobe, timber, bamboo are much weaker than Indian urban stock)."""
    c = {"brick": "has_superstructure_cement_mortar_brick", "rc_ne": "has_superstructure_rc_non_engineered",
         "rc_e": "has_superstructure_rc_engineered", "floors": "count_floors_pre_eq"}
    c.update(cols or {})
    fl = pd.to_numeric(df[c["floors"]], errors="coerce")
    rc = (df[c["rc_ne"]] == 1) | (df[c["rc_e"]] == 1)
    cls = pd.Series(None, index=df.index, dtype=object)
    cls[(df[c["brick"]] == 1) & ~rc & (fl <= 2)] = "URM"
    cls[rc & (fl <= 4)] = "RC_LOW"
    cls[rc & (fl >= 5)] = "RC_MID"
    return cls

def to_groups(df, ward_col, grade_col, wards_pga, cls_col="cls", grade_map=None):
    """Rows (one per building) -> per (class, ward) counts (G, 5) with the ward's mean/spread of ln PGA. Wards without a PGA
    are dropped. Returns DataFrame with columns cls, ward, x, s, n0..n4."""
    gm = grade_map or GRADE_TO_STATE
    d = df[[cls_col, ward_col, grade_col]].dropna().copy()
    d["state"] = d[grade_col].astype(int).map(gm)
    d = d.dropna(subset=["state"])
    t = d.groupby([cls_col, ward_col, "state"]).size().unstack("state", fill_value=0).reindex(columns=range(5), fill_value=0)
    t.columns = [f"n{k}" for k in range(5)]
    t = t.reset_index().rename(columns={cls_col: "cls", ward_col: "ward"})
    t = t.merge(wards_pga[["mean_lnpga", "sd_lnpga", "sigma_shakemap"]].rename_axis("ward").reset_index(), on="ward", how="inner")
    t["x"] = t["mean_lnpga"]
    t["s"] = np.sqrt(t["sd_lnpga"] ** 2 + t["sigma_shakemap"].fillna(0) ** 2)
    return t

def fit_classes(groups, out_dir, classes=("URM", "RC_LOW", "RC_MID"), n_boot=200, truncated=True, seed=0, plots=None):
    """Fit and bootstrap each class; write fit_<class>.json under out_dir (and plots if a plots directory is given)."""
    out_dir = pathlib.Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    res = {}
    for c in classes:
        g = groups[groups.cls == c]
        if len(g) < 20 or g[[f"n{k}" for k in range(1, 5)]].to_numpy().sum() < 200:
            res[c] = {"skipped": f"too little data ({len(g)} groups)"}
            continue
        x, s, n = g.x.to_numpy(), g.s.to_numpy(), g[[f"n{k}" for k in range(5)]].to_numpy()
        mle = fit(x, s, n, truncated)
        boot = bootstrap(g.ward.to_numpy(), x, s, n, mle, n_boot, seed, truncated)
        r = summarize(mle, boot)
        r.update(n_buildings=int(n.sum()), n_groups=int(len(g)), truncated=truncated)
        (out_dir / f"fit_{c}.json").write_text(json.dumps(r))
        if plots:
            plot_fit(pathlib.Path(plots) / f"{c}.png", f"{c} (n={int(n.sum()):,})", x, n, mle, truncated)
        res[c] = r
    return res

# ---------------- choosing final curves and held-out scoring (MASTER_PLAN 3.5, 3.6.1) ----------------
def choose_final(fits, current):
    """Per class: use the fitted curve where the bootstrap interval is narrow (width of the extensive-damage median's 90%
    interval < 50% of the median), otherwise keep the current values and say why. `current` is the dict loaded from
    fragility.json. Returns a new dict with updated medians/beta, class_status and bootstrap samples for the Monte Carlo.
    (The plan also allows blending with GEM/HAZUS curves where the interval is wide; those curves are not available here,
    so the previous values are kept and flagged instead.)"""
    new = json.loads(json.dumps(current))
    new["samples"] = dict(current.get("samples", {}))
    meds, beta, status = new["pga_g_medians_slight_moderate_extensive_complete"], new["beta"], new.setdefault("class_status", {})
    any_fit = False
    for cls, r in fits.items():
        if "skipped" in r:
            status[cls] = f"kept previous values: {r['skipped']}"
        elif r["narrow_ci"]:
            meds[cls] = [round(r[k]["mle"], 4) for k in ("theta_slight", "theta_moderate", "theta_extensive", "theta_complete")]
            beta[cls] = round(r["beta"]["mle"], 4)
            status[cls] = (f"fitted to Nepal 2015 survey, n={r['n_buildings']:,}, extensive-median 90% CI width "
                           f"{r['ci_width_over_theta_extensive']:.0%} of the median" + ("; conditional on damage (truncated)" if r["truncated"] else ""))
            new["samples"][cls] = r["bootstrap_samples"]
            any_fit = True
        else:
            status[cls] = f"kept previous values: fit too uncertain (CI width {r['ci_width_over_theta_extensive']:.0%} of the median, rule < 50%)"
    if any_fit:
        new["status"] = "PARTLY CALIBRATED: see class_status"
    return new

def holdout_score(fit_res, test_groups, train_groups):
    """Score held-out groups (columns x, s, n0..n4) against a fitted class: mean log-loss per building of the observed damage
    grades, versus a null model that predicts the training groups' overall grade mix everywhere; plus Brier score of
    P(DS >= extensive | damaged). Lower is better; skill = 1 - logloss/logloss_null."""
    x, s = test_groups.x.to_numpy(), test_groups.s.to_numpy()
    n = test_groups[[f"n{k}" for k in range(1, 5)]].to_numpy().astype(float)
    P = state_probs(x, np.log(fit_res["theta"]), fit_res["beta"], s)[:, 1:]
    P = P / P.sum(1, keepdims=True)
    mix = train_groups[[f"n{k}" for k in range(1, 5)]].to_numpy().sum(0).astype(float)
    mix = mix / mix.sum()
    ll = -(n * np.log(np.clip(P, 1e-12, None))).sum() / n.sum()
    ll0 = -(n * np.log(np.clip(mix, 1e-12, None))[None, :]).sum() / n.sum()
    pred_ext = P[:, 2:].sum(1)
    obs_ext = n[:, 2:].sum(1) / n.sum(1)
    brier_ = float(np.average((pred_ext - obs_ext) ** 2, weights=n.sum(1)))
    brier0 = float(np.average((mix[2:].sum() - obs_ext) ** 2, weights=n.sum(1)))
    return {"log_loss": float(ll), "log_loss_null": float(ll0), "skill": float(1 - ll / ll0), "brier": brier_, "brier_null": brier0,
            "n_buildings": int(n.sum())}

# ---------------- binary outcome (e.g. destroyed vs not) with shaking uncertainty ----------------
def fit_binary(x, s, n_pos, n_tot):
    """One lognormal curve P(event | x) = Phi((x - ln theta) / sqrt(beta^2 + s^2)) fitted by maximum likelihood to grouped binary
    counts (x = ln PGA of the group, s = shaking spread). Returns dict(theta, beta, nll, success)."""
    x, s, k, n = (np.asarray(v, float) for v in (x, s, n_pos, n_tot))
    def nll(p):
        pr = np.clip(ndtr((x - p[0]) / np.sqrt(np.exp(2 * p[1]) + s ** 2)), 1e-12, 1 - 1e-12)
        return -float((k * np.log(pr) + (n - k) * np.log1p(-pr)).sum())
    best = None
    for t0, b0 in ((np.log(0.5), np.log(0.5)), (np.log(1.0), np.log(0.8)), (np.log(0.3), np.log(0.4))):
        r = minimize(nll, [t0, b0], method="Nelder-Mead", options={"xatol": 1e-6, "fatol": 1e-8, "maxiter": 4000})
        if best is None or r.fun < best.fun:
            best = r
    return {"theta": float(np.exp(best.x[0])), "beta": float(np.exp(best.x[1])), "nll": float(best.fun), "success": bool(best.success)}

def predict_binary(fit, x, s):
    return ndtr((np.asarray(x, float) - np.log(fit["theta"])) / np.sqrt(fit["beta"] ** 2 + np.asarray(s, float) ** 2))

def score_binary(p, k, n, base_rate):
    """Log-loss and Brier score (per observation) of probabilities p for grouped counts, and the same for a constant base rate."""
    p, k, n = (np.asarray(v, float) for v in (p, k, n))
    def ll(q):
        q = np.clip(q, 1e-9, 1 - 1e-9)
        return -float((k * np.log(q) + (n - k) * np.log1p(-q)).sum() / n.sum())
    def br(q):
        return float((k * (1 - q) ** 2 + (n - k) * q ** 2).sum() / n.sum())
    return {"log_loss": ll(p), "log_loss_null": ll(np.full_like(p, base_rate)), "brier": br(p), "brier_null": br(np.full_like(p, base_rate)),
            "skill_logloss": 1 - ll(p) / ll(np.full_like(p, base_rate)), "n": int(n.sum()), "events": int(k.sum())}

def calibration_bins(p, k, n, edges=(0, 1e-4, 1e-3, 1e-2, 0.05, 0.2, 0.5, 1.0)):
    """Predicted vs observed event rate in probability bins (grouped counts)."""
    p, k, n = (np.asarray(v, float) for v in (p, k, n))
    rows = []
    for a, b in zip(edges[:-1], edges[1:]):
        m = (p >= a) & (p < b if b < 1 else p <= b)
        if n[m].sum() > 0:
            rows.append({"predicted_range": f"{a:g}-{b:g}", "n": int(n[m].sum()), "predicted": float((p[m] * n[m]).sum() / n[m].sum()), "observed": float(k[m].sum() / n[m].sum())})
    return rows
