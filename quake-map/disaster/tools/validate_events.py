"""Replay past earthquakes against satellite damage grading (MASTER_PLAN 3.6). Validation data is kept apart from any
calibration data (data/validation/, separate area registry).

  python tools/validate_events.py --event turkiye2023     # EMSR648 grading, USGS ShakeMap us6000jllz
  python tools/validate_events.py --event nepal2015       # EMSR125 Kathmandu grading, USGS ShakeMap us20002926

For each graded building it reports how well the model ranks damaged above undamaged buildings (AUC, with cluster
bootstrap intervals), using (a) the USGS ShakeMap PGA (isolates damage + exposure) and (b) our own shaking model with the
event's finite fault (full chain), and compares with ranking by PGA alone. Absolute counts are NOT compared: the grading is
visible damage on satellite imagery, a different thing from engineering damage states, and it is not a random sample.
Writes docs/validation_<event>.md and data/validation/<event>/results.json.
"""
import argparse, json, pathlib, re, sys, urllib.request, zipfile
import numpy as np, pandas as pd
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import geopandas as gpd, shapely
import areas, validation as V, calibration as C
from overture import fetch_buildings, latest_release
from simulate import Model, local_xy
import os
os.environ.setdefault("VS30_TIF", str(HERE / "data" / "rasters" / "vs30_mosaic_median_30c.tif"))

S3 = "https://cems-mapping-website.s3.eu-west-1.amazonaws.com/static/activations"
EVENTS = {"turkiye2023": dict(usgs="us6000jllz", ems="EMSR648", title="Kahramanmaras (Turkiye) 2023-02-06, M7.8"),
          "nepal2015": dict(usgs="us20002926", ems="EMSR125", title="Gorkha (Nepal) 2015-04-25, M7.8")}

def get(url, dest):
    dest = pathlib.Path(dest); dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        print("  downloading", url.split("/")[-1], flush=True)
        req = urllib.request.Request(url, headers={"User-Agent": "quake-damage-map-validation/1.0"})
        with urllib.request.urlopen(req, timeout=300) as r, open(dest, "wb") as f:
            f.write(r.read())
    return dest

def usgs_event(eid, work):
    """Origin, moment-tensor plane and ShakeMap file URLs from the USGS event service."""
    f = work / "event.json"
    if not f.exists():
        get(f"https://earthquake.usgs.gov/fdsnws/event/1/query?eventid={eid}&format=geojson", f)
    d = json.loads(f.read_text())
    p, prod = d["properties"], d["properties"]["products"]
    org = prod["origin"][0]["properties"]
    ev = {"lat": float(org["latitude"]), "lon": float(org["longitude"]), "depth_km": float(org["depth"]), "mag": float(p["mag"])}
    mt = (prod.get("moment-tensor") or [{}])[0].get("properties", {})
    planes = [tuple(float(mt[f"nodal-plane-{i}-{k}"]) for k in ("strike", "dip", "rake")) for i in (1, 2) if f"nodal-plane-{i}-strike" in mt]
    contents = prod["shakemap"][0]["contents"]
    ev["shakemap"] = {k.split("/")[-1]: v["url"] for k, v in contents.items() if k.split("/")[-1] in ("grid.xml", "uncertainty.xml")}
    # the moment tensor has two nodal planes; the real fault is the one that follows the USGS rupture polygon (ShakeMap rupture.json)
    trend = None
    rj = next((v["url"] for k, v in contents.items() if k.endswith("rupture.json")), None)
    if rj:
        try:
            g = json.loads(get(rj, work / "rupture.json").read_text())["features"][0]["geometry"]
            flat = np.array([c[:2] for ring in (g["coordinates"] if g["type"] == "Polygon" else [r for poly in g["coordinates"] for r in poly]) for c in (ring if isinstance(ring[0][0], float) else [p for r in ring for p in r])])
            xy = np.column_stack([(flat[:, 0] - ev["lon"]) * np.cos(np.radians(ev["lat"])), flat[:, 1] - ev["lat"]])
            w, v = np.linalg.eigh(np.cov(xy.T)); ax = v[:, np.argmax(w)]
            trend = float(np.degrees(np.arctan2(ax[0], ax[1])) % 180)
        except Exception as ex:
            print("  (could not read rupture.json:", ex, ")")
    ev["rupture_trend"] = trend
    if planes:
        pick = min(planes, key=lambda p: min(abs((p[0] % 180) - trend), 180 - abs((p[0] % 180) - trend))) if trend is not None else planes[0]
        ev["strike"], ev["dip"], ev["rake"] = pick
    else:
        ev["strike"] = ev["dip"] = ev["rake"] = None
    return ev

def pga_at(grid, lon, lat):
    """ShakeMap PGA (g) at points: inverse-distance mean of the 4 nearest grid points."""
    from scipy.spatial import cKDTree
    tree = cKDTree(np.column_stack([grid.lon, grid.lat]))
    d, i = tree.query(np.column_stack([lon, lat]), k=4)
    w = 1.0 / np.maximum(d, 1e-6)
    lp = np.log(grid.pga_g.to_numpy().clip(1e-6))[i]
    return np.exp((w * lp).sum(1) / w.sum(1))

def graded_points(event, work):
    """Graded building points -> GeoDataFrame(geometry, locality, state) with state in {'none','damaged','destroyed'}."""
    code = EVENTS[event]["ems"]
    out = []
    if event == "turkiye2023":
        page = get(f"https://rapidmapping.emergency.copernicus.eu/{code}", work / "page.html").read_text()
        zips = sorted(set(re.findall(rf"{S3}/{code}/({code}_AOI\d+_GRA_PRODUCT_[^\"' <>]*_vector\.zip)", page.replace("\\u002F", "/"))))
        for z in zips:
            zp = get(f"{S3}/{code}/{z}", work / z)
            with zipfile.ZipFile(zp) as zf:
                names = zf.namelist()
                bp = [n for n in names if "builtUpP" in n and n.endswith(".json")]
                aoi = [n for n in names if "areaOfInterestA" in n and n.endswith(".json")]
                if not bp:
                    continue
                zf.extract(bp[0], work / "x"); zf.extract(aoi[0], work / "x")
            g = gpd.read_file(work / "x" / bp[0]); loc = gpd.read_file(work / "x" / aoi[0])["locality"].iloc[0]
            m = {"Destroyed": "destroyed", "Damaged": "damaged", "No visible damage": "none", "Possibly damaged": "possible"}
            g["state"] = g["damage_gra"].map(m)
            out.append(gpd.GeoDataFrame({"locality": loc, "aoi": z.split("_")[1], "state": g["state"]}, geometry=g.geometry, crs=4326))
    else:
        z = f"{code}_01KATHMANDU_GRADING_OVERVIEW_v4_vector.zip"
        zp = get(f"{S3}/{code}/{z}", work / z)
        with zipfile.ZipFile(zp) as zf:
            zf.extractall(work / "x")
        f = next((work / "x").rglob("*crisis_information_point_grading.shp"))
        g = gpd.read_file(f).to_crs(4326)
        m = {"Completely Destroyed": "destroyed", "Highly Damaged": "damaged", "Moderately Damaged": "damaged", "Negligible to slight damage": "none"}
        out.append(gpd.GeoDataFrame({"locality": "Kathmandu", "aoi": "01", "state": g["grading"].map(m)}, geometry=g.geometry, crs=4326))
    g = pd.concat(out, ignore_index=True)
    return gpd.GeoDataFrame(g.dropna(subset=["state"]), crs=4326)

def area_for(reg, key, pts, pad=0.004):
    """Registry area (Overture buildings) around a set of graded points."""
    w, s, e, n = pts.total_bounds
    bbox = (w - pad, s - pad, e + pad, n + pad)
    if (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) > 0.08:
        raise ValueError(f"{key}: area too large for one download ({bbox})")
    if reg.meta(key) is None:
        rel = latest_release()
        print(f"  Overture buildings for {key} ...", flush=True)
        reg.create(bbox, fetch_buildings(bbox, release=rel), rel, aid=key, name=key)
    return reg.load_exposure(key)

def match(pts, g, max_m=15.0):
    """Index of the building each point falls in (or the nearest within max_m); one row per building (worst state)."""
    u = g.estimate_utm_crs()
    P = gpd.GeoDataFrame({"state": pts["state"].to_numpy()}, geometry=pts.geometry.to_crs(u).values, crs=u)
    B = gpd.GeoDataFrame({"bid": np.arange(len(g))}, geometry=g.geometry.to_crs(u).values, crs=u)
    j = gpd.sjoin_nearest(P, B, how="inner", max_distance=max_m)
    rank = {"none": 0, "possible": 1, "damaged": 2, "destroyed": 3}
    j["r"] = j.state.map(rank)
    j = j.sort_values("r").groupby("bid").tail(1)
    return j.bid.to_numpy(), j.state.to_numpy()

def score_rows(name, model, bid, state, tag):
    """AUC table rows for one prediction source; model.last holds the run."""
    L = model.last
    cell = ((model.x // 500).astype(int) * 100000 + (model.y // 500).astype(int))[bid]
    pga = L["pga"].to_numpy()[bid]
    pm, pc = L["p_moderate"].to_numpy()[bid], L["p_collapse"].to_numpy()[bid]
    rows = []
    for label, keep, pos, pred in (("damaged or destroyed vs no visible damage", state != "possible", np.isin(state, ["damaged", "destroyed"]), pm),
                                   ("destroyed vs no visible damage", np.isin(state, ["none", "destroyed"]), state == "destroyed", pc),
                                   ("destroyed vs damaged (severity among damaged)", np.isin(state, ["damaged", "destroyed"]), state == "destroyed", pc)):
        k = keep & ((state != "damaged") | (label.startswith("damaged")) | ("severity" in label))
        if "severity" in label:
            k = np.isin(state, ["damaged", "destroyed"])
        y = pos[k]
        if y.sum() < 5 or (~y).sum() < 5:
            continue
        for nm, sc in (("full model", pred[k]), ("PGA only", pga[k])):
            lo, hi = V.auc_ci(sc, y, n=100, block=cell[k])
            rows.append({"shaking": tag, "outcome": label, "ranked by": nm, "n": int(k.sum()), "damaged": int(y.sum()), "AUC": V.auc(sc, y), "AUC 90% CI": f"{lo:.3f}-{hi:.3f}"})
        t = V.cell_table(cell[k], pred[k], y)
        if len(t) >= 5:
            rows.append({"shaking": tag, "outcome": label, "ranked by": "cells (500 m): full model", "n": int(t.n.sum()), "damaged": len(t),
                         "AUC": V.spearman(t.pred, t.obs), "AUC 90% CI": f"top-10% hit {V.top_cell_hit_rate(t.pred, t.obs, max(1, len(t) // 10)):.2f} (Spearman shown)"})
    return rows

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--event", required=True, choices=list(EVENTS)); ap.add_argument("--max-aoi", type=int, default=99)
    a = ap.parse_args()
    cfg = EVENTS[a.event]
    work = HERE / "data" / "validation" / a.event
    reg = areas.Registry(HERE / "data" / "validation")
    ev = usgs_event(cfg["usgs"], work)
    print(a.event, {k: v for k, v in ev.items() if k != "shakemap"})
    grid = C.read_shakemap_grid(get(ev["shakemap"]["grid.xml"], work / "grid.xml"))
    pts = graded_points(a.event, work)
    print("graded points:", pts.state.value_counts().to_dict(), flush=True)
    rows, per_area = [], []
    for (loc, aoi), p in list(pts.groupby(["locality", "aoi"]))[: a.max_aoi]:
        if len(p) < 200 or p.state.isin(["damaged", "destroyed"]).sum() < 5:
            continue
        key = f"val-{a.event}-{aoi}"
        try:
            g = area_for(reg, key, p)
        except Exception as ex:
            print("  skip", loc, ex); continue
        model = Model(g, data_dir=HERE / "data")
        bid, state = match(p, g)
        if len(bid) < 100:
            continue
        lonlat = (model.lon, model.lat)
        pga_sm = pga_at(grid, *lonlat)
        res = []
        model.run(ev["lat"], ev["lon"], ev["depth_km"], ev["mag"], pga_override=pga_sm)
        res += score_rows(loc, model, bid, state, "ShakeMap PGA")
        model.run(ev["lat"], ev["lon"], ev["depth_km"], ev["mag"], strike=ev["strike"], dip=ev["dip"], rake=ev["rake"])
        pga_model = model.last["pga"].to_numpy()
        res += score_rows(loc, model, bid, state, "our shaking model")
        for r in res:
            r["area"] = f"{loc} ({aoi})"
        rows += res
        per_area.append({"area": f"{loc} ({aoi})", "buildings in footprint data": len(g), "graded points matched": int(len(bid)),
                         "damaged+destroyed share": float(np.isin(state, ["damaged", "destroyed"]).mean()),
                         "ShakeMap PGA median g": float(np.median(pga_sm[bid])), "our model PGA median g": float(np.median(pga_model[bid])),
                         "log ratio our/ShakeMap (median)": float(np.median(np.log(pga_model[bid] / pga_sm[bid])))})
        print("  done", loc, aoi, len(bid), flush=True)
    df = pd.DataFrame(rows)
    (work / "results.json").write_text(json.dumps({"event": ev | {"shakemap": None}, "rows": rows, "areas": per_area}, default=float))
    md = [f"# Replay: {cfg['title']}", "",
          f"Shaking: USGS ShakeMap {cfg['usgs']} and our model (finite fault: strike {ev['strike']}, dip {ev['dip']}, rake {ev['rake']}, "
          f"depth {ev['depth_km']} km, M{ev['mag']}). Damage: Copernicus EMS {cfg['ems']} satellite grading. Buildings: Overture.",
          "", "Limits: grading is visible damage on imagery (not engineering damage states) and not a random sample of buildings; the "
          "storeys/types of these buildings are guesses outside Google's coverage; the fragility curves are uncalibrated placeholders. "
          "Only ranking skill is meaningful; absolute counts are not compared.", "", "## Areas", "", pd.DataFrame(per_area).round(3).to_markdown(index=False), "",
          "## Ranking skill (AUC: 0.5 = no skill, 1 = perfect)", ""]
    if len(df):
        md.append(df.drop(columns=[]).round(3).to_markdown(index=False))
    (HERE / "docs" / f"validation_{a.event}.md").write_text("\n".join(md) + "\n")
    head = [{"area": r["area"], "shaking": r["shaking"], "outcome": r["outcome"], "auc": round(r["AUC"], 3), "ci90": r["AUC 90% CI"], "n": r["n"], "positives": r["damaged"]}
            for r in rows if r["ranked by"] == "full model"]
    summ_f = HERE / "docs" / "validation_summary.json"
    summ = json.loads(summ_f.read_text()) if summ_f.exists() else {}
    summ[a.event] = {"event": a.event, "rows": head, "shaking_vs_shakemap_log_ratio": {p["area"]: round(p["log ratio our/ShakeMap (median)"], 2) for p in per_area}}
    summ_f.write_text(json.dumps(summ, indent=1))
    print("\n".join(md))

if __name__ == "__main__":
    main()
