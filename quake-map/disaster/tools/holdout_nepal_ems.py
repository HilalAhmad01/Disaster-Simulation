"""Held-out validation of a fitted fragility curve on Nepal 2015 using Copernicus EMS gradings (MASTER_PLAN 3.6.1, adapted).

The plan's data (the CBS/NSO household registration survey, by district) is not obtainable here (server unreachable, registration
needed), so this uses the nearest REAL data that is: EMSR125 satellite gradings of buildings in several Nepalese towns, joined to the
USGS Gorkha ShakeMap. Differences from the plan, stated plainly:
  * unit = town (not district); outcome = visible destruction (not a 5-grade household survey); no building class (one pooled curve);
  * the grading is not a random sample (teams mapped where damage was expected), so absolute rates are biased upwards where mapping
    followed damage; 'Not Applicable' / 'Unknown' buildings are excluded;
  * leave-one-TOWN-out: fit on all other towns, score the held-out town against a constant base rate.

  python tools/holdout_nepal_ems.py        # needs tools/get_nepal_calibration.py first (ShakeMap), network for the EMS zips
"""
import json, pathlib, re, sys, zipfile
import numpy as np, pandas as pd
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE / "tools"))
import geopandas as gpd
import calibration as C
from validate_events import get, S3

CODE = "EMSR125"
NONE = {"Not Affected", "Negligible to slight damage"}
DESTROYED = {"Completely Destroyed"}
DAMAGED = {"Highly Damaged", "Moderately Damaged"}

def town_points(work):
    page = get(f"https://rapidmapping.emergency.copernicus.eu/{CODE}", work / "page.html").read_text().replace("\\u002F", "/")
    zips = sorted(set(re.findall(rf"{CODE}_\d+[A-Z]+_GRADING_OVERVIEW_v\d+_vector\.zip", page)))
    out = []
    for z in zips:
        name = z.split("_")[1][2:].title()
        try:
            zp = get(f"{S3}/{CODE}/{z}", work / z)
            with zipfile.ZipFile(zp) as zf:
                zf.extractall(work / "x" / z[:-4])
        except Exception as ex:
            print(f"  {name}: skipped ({str(ex)[:60]})"); continue
        f = next((work / "x" / z[:-4]).rglob("*crisis_information_point*.shp"), None)
        if f is None:
            continue
        g = gpd.read_file(f).to_crs(4326)
        if "grading" not in g:
            continue
        state = np.where(g.grading.isin(DESTROYED), "destroyed", np.where(g.grading.isin(DAMAGED), "damaged", np.where(g.grading.isin(NONE), "none", "")))
        g = gpd.GeoDataFrame({"town": name, "state": state}, geometry=g.geometry, crs=4326)
        out.append(g[g.state != ""])
    return pd.concat(out, ignore_index=True)

def shaking_at(grid, lon, lat):
    """Inverse-distance mean ln PGA (g) and mean ShakeMap sigma of the 4 nearest grid points."""
    from scipy.spatial import cKDTree
    d, i = cKDTree(np.column_stack([grid.lon, grid.lat])).query(np.column_stack([lon, lat]), k=4)
    w = 1.0 / np.maximum(d, 1e-6)
    x = np.log(grid.pga_g.to_numpy().clip(1e-6))[i]
    s = grid.sigma_lnpga.to_numpy()[i]
    return (w * x).sum(1) / w.sum(1), (w * s).sum(1) / w.sum(1)

def grouped(pts, outcome):
    """Counts per (town, 0.05 ln-PGA bin): x, s, k events, n total."""
    d = pts.assign(y=pts.state.isin(outcome).astype(int), b=(pts.x / 0.05).round().astype(int))
    g = d.groupby(["town", "b"]).agg(x=("x", "mean"), s=("s", "mean"), k=("y", "sum"), n=("y", "size")).reset_index()
    return g

def loto(g, min_n=100):
    towns = [t for t, v in g.groupby("town") if v.n.sum() >= min_n]
    res, preds = [], []
    for t in towns:
        tr, te = g[g.town != t], g[g.town == t]
        if tr.k.sum() < 5 or te.n.sum() < min_n:
            continue
        f = C.fit_binary(tr.x, tr.s, tr.k, tr.n)
        p = C.predict_binary(f, te.x, te.s)
        sc = C.score_binary(p, te.k, te.n, tr.k.sum() / tr.n.sum())
        res.append({"held_out_town": t, "theta_g": round(f["theta"], 3), "beta": round(f["beta"], 3), "median_pga_g": round(float(np.exp(np.average(te.x, weights=te.n))), 3),
                    "observed_rate": float(te.k.sum() / te.n.sum()), "predicted_rate": float((p * te.n).sum() / te.n.sum()), **{k: sc[k] for k in ("n", "events", "log_loss", "log_loss_null", "brier", "brier_null", "skill_logloss")}})
        preds.append(pd.DataFrame({"town": t, "p": np.asarray(p), "k": te.k.to_numpy(), "n": te.n.to_numpy()}))
    return pd.DataFrame(res), pd.concat(preds) if preds else pd.DataFrame()

def main():
    work = HERE / "data" / "validation" / "nepal_towns"; work.mkdir(parents=True, exist_ok=True)
    grid = C.read_shakemap_grid(HERE / "data" / "calibration" / "nepal" / "grid.xml", HERE / "data" / "calibration" / "nepal" / "uncertainty.xml")
    pts = town_points(work)
    pts["x"], pts["s"] = shaking_at(grid, pts.geometry.x.to_numpy(), pts.geometry.y.to_numpy())
    print(pts.groupby("town").state.value_counts().unstack(fill_value=0).to_string())
    md = ["# Held-out Nepal towns (EMS gradings), 2015 Gorkha", "",
          "**This is NOT the plan's district-level survey validation** (that survey was unreachable). It is a leave-one-town-out test on Copernicus EMS "
          "satellite gradings of buildings (EMSR125) joined to the USGS ShakeMap. Visible destruction only; one pooled curve (no building class); "
          "non-random mapping (teams mapped where damage was expected); 'Not Applicable' and 'Unknown' buildings excluded. Read the sign of the skill, "
          "not the size of the absolute rates.", "", "## Graded buildings used", "",
          pts.groupby("town").state.value_counts().unstack(fill_value=0).assign(median_pga_g=pts.groupby("town").x.apply(lambda v: round(float(np.exp(v.median())), 3))).to_markdown(), ""]
    allres = {}
    for name, outcome in (("destroyed", {"destroyed"}), ("damaged or destroyed", {"destroyed", "damaged"})):
        g = grouped(pts, outcome)
        if g.k.sum() < 10:
            md += [f"## Outcome: {name}", "", f"Only {int(g.k.sum())} events: too few to fit.", ""]
            continue
        full = C.fit_binary(g.x, g.s, g.k, g.n)
        res, preds = loto(g)
        allres[name] = {"fit_all_towns": full, "loto": res.to_dict("records")}
        md += [f"## Outcome: {name}", "", f"Curve fitted to all towns together: median {full['theta']:.2f} g, beta {full['beta']:.2f} (capacity spread; shaking sigma removed).", "",
               "Leave-one-town-out (skill = 1 - logloss / logloss of the training base rate; > 0 means the curve beats a constant):", "", res.round(4).to_markdown(index=False), ""]
        if len(preds):
            cal = C.calibration_bins(preds.p, preds.k, preds.n)
            allres[name]["calibration"] = cal
            md += ["Calibration of the held-out predictions (all held-out towns pooled):", "", pd.DataFrame(cal).round(4).to_markdown(index=False), ""]
            pooled = C.score_binary(preds.p, preds.k, preds.n, g.k.sum() / g.n.sum())
            allres[name]["pooled"] = pooled
            md += [f"Pooled held-out: log-loss {pooled['log_loss']:.4f} vs {pooled['log_loss_null']:.4f} for a constant base rate (skill {pooled['skill_logloss']:+.1%}); Brier {pooled['brier']:.5f} vs {pooled['brier_null']:.5f}.", ""]
    (HERE / "docs" / "validation_nepal_heldout.md").write_text("\n".join(md) + "\n")
    (HERE / "docs" / "validation_nepal_heldout.json").write_text(json.dumps(allres, indent=1, default=float))
    print("\n".join(md))

if __name__ == "__main__":
    main()
