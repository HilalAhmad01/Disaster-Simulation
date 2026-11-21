"""Machine-learned building attributes (MASTER_PLAN 1.5): storeys and use from footprint shape, neighbourhood and satellite height.

Labels are Overture `num_floors` and `class` (sparse, biased towards tall apartment blocks). The model never sees coordinates, so it
has to generalise across cities; it is judged on whole held-out cities (tools/train_attributes.py, docs/ml_attributes.md) against the
calibrated rule-based prior and is only used if it wins. Its outputs are probabilities that feed the construction-type prior
(exposure.py), not hard replacements.

Features (all per building): footprint area, perimeter, compactness (4 pi A / P^2), vertex count, elongation and orientation of the
minimum rotated rectangle; counts and mean/median footprint area of neighbours within 50/100/250 m, distance to the nearest
neighbour, local rank of its own area, density at 500 m; Google 2.5D height (median over years), its spread, and the mean of
usable heights among neighbours within 100/250 m. Heights below 2 m are the product's 'not resolved' case and are kept as a feature.
"""
import numpy as np, pandas as pd, shapely
from scipy.spatial import cKDTree

STOREY_BINS = ("1", "2", "3-4", "5-7", "8+")
USE_GROUPS = ("residential", "commercial", "industrial", "civic", "other")
_USE_TO_GROUP = {"residential": "residential", "office": "commercial", "retail": "commercial", "hotel": "commercial", "industrial": "industrial",
                 "warehouse": "industrial", "education": "civic", "medical": "civic", "religious": "civic", "entertainment": "civic", "outbuilding": "other"}

def storey_bin(levels):
    """0..4 for 1, 2, 3-4, 5-7, 8+ storeys."""
    return np.digitize(np.asarray(levels, float), [1.5, 2.5, 4.5, 7.5])

def use_group(use):
    return pd.Series(use).map(_USE_TO_GROUP).fillna("other").to_numpy()

def _rings(xy, la, rows, radii, hval=None, chunk=4000):
    """Neighbourhood statistics for the query rows against ALL buildings, vectorised in chunks with sparse distance matrices.
    Returns {radius: dict(n, mean_la, rank_la, share_big, [nb_h])}. The building itself is excluded; NaN where it has no neighbour."""
    tree = cKDTree(xy)
    big_thr = np.log(200.0)
    usable = None if hval is None else np.isfinite(hval)
    out = {r: {k: np.full(len(rows), np.nan) for k in ("mean_la", "rank_la", "share_big") + (("nb_h",) if hval is not None else ())} | {"n": np.zeros(len(rows))} for r in radii}
    for a in range(0, len(rows), chunk):
        q = rows[a:a + chunk]
        qt = cKDTree(xy[q])
        for r in radii:
            m = qt.sparse_distance_matrix(tree, r, output_type="coo_matrix")
            i, j = m.row, m.col
            keep = j != q[i]                                  # drop the building itself
            i, j = i[keep], j[keep]
            n = np.bincount(i, minlength=len(q))
            has = n > 0
            sl = slice(a, a + len(q))
            out[r]["n"][sl] = n
            mean = np.bincount(i, weights=la[j], minlength=len(q)) / np.maximum(n, 1)
            rank = np.bincount(i, weights=(la[j] < la[q][i]).astype(float), minlength=len(q)) / np.maximum(n, 1)
            big = np.bincount(i, weights=(la[j] > big_thr).astype(float), minlength=len(q)) / np.maximum(n, 1)
            for k, v in (("mean_la", mean), ("rank_la", rank), ("share_big", big)):
                out[r][k][sl] = np.where(has, v, np.nan)
            if hval is not None:
                w = usable[j].astype(float)
                nh = np.bincount(i, weights=w, minlength=len(q))
                mh = np.bincount(i, weights=np.where(usable[j], hval[j], 0.0), minlength=len(q)) / np.maximum(nh, 1)
                out[r]["nb_h"][sl] = np.where(nh > 0, mh, np.nan)
    return out

def features(geoms, heights=None, utm=None, rows=None):
    """Feature DataFrame for footprints (EPSG:4326 shapely array); one row per entry of `rows` (default all) while every footprint
    serves as neighbourhood context. heights: DataFrame h_google/h_spread (heights.building_heights) or None."""
    import geopandas as gpd
    g = gpd.GeoSeries(np.asarray(geoms, dtype=object), crs=4326)
    u = g.to_crs(utm or g.estimate_utm_crs())
    geo_all = np.asarray(u.values, dtype=object)
    rows = np.arange(len(geo_all)) if rows is None else np.asarray(rows, int)
    c = shapely.centroid(geo_all)
    xy = np.column_stack([shapely.get_x(c), shapely.get_y(c)])
    la_all = np.log(np.maximum(shapely.area(geo_all), 1.0))
    geo = geo_all[rows]
    area, per = shapely.area(geo), shapely.length(geo)
    rect = shapely.minimum_rotated_rectangle(geo)
    rcoords, n_pts = shapely.get_coordinates(rect), shapely.get_num_coordinates(rect)
    starts = np.r_[0, np.cumsum(n_pts)[:-1]]
    sel = np.flatnonzero(n_pts == 5)
    e1 = np.full((len(geo), 2), np.nan); e2 = np.full((len(geo), 2), np.nan)
    e1[sel] = rcoords[starts[sel] + 1] - rcoords[starts[sel]]
    e2[sel] = rcoords[starts[sel] + 2] - rcoords[starts[sel] + 1]
    l1, l2 = np.hypot(*e1.T), np.hypot(*e2.T)
    long_, short_ = np.maximum(l1, l2), np.minimum(l1, l2)
    vec = np.where((l1 >= l2)[:, None], e1, e2)
    theta = np.arctan2(vec[:, 1], vec[:, 0])
    tree = cKDTree(xy)
    f = {"log_area": la_all[rows], "log_perimeter": np.log(np.maximum(per, 1.0)), "compactness": 4 * np.pi * area / np.maximum(per ** 2, 1e-9),
         "n_vertices": shapely.get_num_coordinates(geo).astype(float), "elongation": np.where(short_ > 0, long_ / np.maximum(short_, 1e-9), np.nan),
         "orient_sin2": np.sin(2 * theta), "orient_cos2": np.cos(2 * theta), "dist_nn": tree.query(xy[rows], k=2)[0][:, 1]}
    hval = None
    if heights is not None:
        h, sp = heights["h_google"].to_numpy(float), heights["h_spread"].to_numpy(float)
        hval = np.where(np.isfinite(h) & (h >= 2) & (sp <= 6), h, np.nan)
        f["h_google"], f["h_spread"], f["h_lt2"] = h[rows], sp[rows], (h[rows] < 2).astype(float)
    rings = _rings(xy, la_all, rows, (50, 100, 250), hval)
    for r, d in rings.items():
        f[f"n_{r}"], f[f"mean_la_{r}"] = d["n"], d["mean_la"]
        if r != 250:
            f[f"rank_la_{r}"], f[f"share_big_{r}"] = d["rank_la"], d["share_big"]
        if hval is not None and r in (100, 250):
            f[f"nb_h_{r}"] = d["nb_h"]
    f["density_500"] = tree.query_ball_point(xy[rows], 500, return_length=True) / 0.785
    return pd.DataFrame(f)

# ---------------- using the trained satellite-supervised storey model ----------------
_MODEL = {}

def model_files(data_dir=None):
    """(booster text file, metadata json): data/models/ wins over the shipped priors/models/."""
    import pathlib
    here = pathlib.Path(__file__).resolve().parent
    for base in ([pathlib.Path(data_dir) / "models"] if data_dir else []) + [here / "priors" / "models"]:
        if (base / "storeys_satellite.txt").exists() and (base / "storeys_satellite.json").exists():
            return base / "storeys_satellite.txt", base / "storeys_satellite.json"
    return None

def load_model(data_dir=None):
    """(lightgbm booster, metadata dict) or None when no model is installed or lightgbm is missing. Cached by file time."""
    import json
    files = model_files(data_dir)
    if files is None:
        return None
    key = (str(files[0]), files[0].stat().st_mtime_ns)
    if key not in _MODEL:
        try:
            import lightgbm as lgb
        except ImportError:
            return None
        _MODEL.clear()
        _MODEL[key] = (lgb.Booster(model_file=str(files[0])), json.loads(files[1].read_text()))
    return _MODEL[key]

def predict_storeys(geoms, rows, utm=None, data_dir=None):
    """Storey-bin probabilities (len(rows), 5) for footprints `rows` of `geoms` (EPSG:4326), or None if no model. No coordinates and no
    satellite heights are used (see tools/train_satellite_storeys.py), so the result does not depend on whether heights exist."""
    m = load_model(data_dir)
    if m is None or len(rows) == 0:
        return None
    bst, meta = m
    X = features(geoms, None, utm=utm, rows=rows)
    return bst.predict(X[meta["features"]])
