"""Hazard -> damage -> impact. All coefficients are v1 placeholders, see notes."""
import itertools, json
import numpy as np, pandas as pd, geopandas as gpd
import shapely
from shapely.geometry import box, mapping
from gmpe import pga_g, mmi_from_pga
import rupture, gmm
from soil import vs30_at
from scipy.special import ndtr

# Median PGA (g) at which damage state is reached: slight, moderate, extensive, complete.
# PLACEHOLDERS: replace with HAZUS / GEM fragility curves for Indian building types.
FRAG = {"URM": (0.10, 0.18, 0.32, 0.55),
        "RC_LOW": (0.18, 0.32, 0.55, 0.90),
        "RC_MID": (0.14, 0.26, 0.45, 0.75)}
BETA = {"URM": 0.6, "RC_LOW": 0.6, "RC_MID": 0.6}   # lognormal dispersion per type (Phase 3 replaces these with fitted values)
TYPES = ("URM", "RC_LOW", "RC_MID")
TIMES = ("night", "day", "commute")

def damage_probs(pga, W, frag=None, beta=None):
    """P(damage >= slight/moderate/extensive/complete), shape (n, 4): the mix over construction types
    sum_t w_t * Phi((ln PGA - ln theta_t,k) / beta_t). pga in g, shape (n,); W (n, 3) type weights summing to 1."""
    frag, beta = frag or FRAG, beta or BETA
    lp = np.log(np.maximum(pga, 1e-9))[:, None]
    P = np.zeros((len(pga), 4))
    for j, t in enumerate(TYPES):
        sel = W[:, j] > 0
        if not sel.any():
            continue
        Pt = ndtr((lp[sel] - np.log(np.asarray(frag[t]))) / beta[t])
        P[sel] += W[sel, j:j + 1] * Pt
    return P

def local_xy(lat0, lon0, lat, lon):
    """Offsets (metres east, north) of points from an origin along great circles: exact distance and bearing, so the
    finite-fault geometry stays right however far the epicentre is from the area (no map-projection distortion)."""
    p = np.pi / 180
    d = haversine(lat0, lon0, lat, lon) * 1000.0
    dl = (lon - lon0) * p
    brg = np.arctan2(np.sin(dl) * np.cos(lat * p), np.cos(lat0 * p) * np.sin(lat * p) - np.sin(lat0 * p) * np.cos(lat * p) * np.cos(dl))
    return d * np.sin(brg), d * np.cos(brg)

def haversine(lat1, lon1, lat2, lon2):
    p = np.pi / 180
    a = np.sin((lat2 - lat1) * p / 2) ** 2 + np.cos(lat1 * p) * np.cos(lat2 * p) * np.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * np.arcsin(np.sqrt(a))

_RUNS = itertools.count(1)        # run numbers stay unique across area switches, so stale popups can be detected
DETAIL = ["btype", "levels", "levels_src", "p_URM", "p_RC_LOW", "p_RC_MID", "occupants", "use", "pga", "cat", "risk", "mmi", "vs30", "p_extensive", "p_collapse"]
EMPTY_FC = '{"type":"FeatureCollection","features":[]}'

class Model:
    @staticmethod
    def _geom_json(geoms):
        geoms = np.asarray(geoms, dtype=object)
        try:
            return list(shapely.to_geojson(geoms))
        except AttributeError:                      # older shapely
            return [json.dumps(mapping(g)) for g in geoms]

    def _features(self, idx, geoms):
        """Lean features for drawing: id plus damage class, storeys and collapse chance. Details come from building()."""
        cat, lv, pc = (self.last[c].to_numpy()[idx] for c in ("cat", "levels", "p_collapse"))
        return ",".join('{"type":"Feature","id":%d,"properties":{"cat":%d,"levels":%d,"pc":%.2f},"geometry":%s}' % t
                        for t in zip(idx, cat, lv, pc, geoms))

    def _nearest_first(self, idx, clat, clon):
        """Sort building indices by distance from a point; also return the distances (degrees of latitude)."""
        d = np.sqrt((self.lat[idx] - clat) ** 2 + ((self.lon[idx] - clon) * np.cos(np.radians(clat))) ** 2)
        o = np.argsort(d)
        return idx[o], d[o]

    def buildings_3d(self, w, s, e, n, clat, clon, near=40000, far=150000):
        """3D view: nearest buildings with full detail, the next ring simplified. Returns a JSON string.
        near_r is the radius (degrees of latitude) fully covered by the detailed ring, or null if it holds everything."""
        if self.last is None:
            return '{"run":0,"near_r":null,"near":%s,"far":%s}' % (EMPTY_FC, EMPTY_FC)
        idx, d = self._nearest_first(self.g.sindex.query(box(w, s, e, n)), clat, clon)
        near_r = "%.6f" % d[near - 1] if len(idx) > near else "null"
        idx = idx[: near + far]
        ni, fi = idx[:near], idx[near:]
        geoms = self.last.geometry.values
        nf = self._features(ni, self._geom_json(geoms[ni]))
        ff = ""
        if len(fi):
            # simplify ~3 m, then snap to a 1e-5 degree (~1 m) grid. set_precision (not plain rounding) keeps shapes valid
            # and identical to v1.0 (decision D2: no visual change accepted, even though rounding was ~13x faster)
            simple = shapely.set_precision(shapely.simplify(np.asarray(geoms[fi], dtype=object), 0.00003), 1e-5)
            ff = self._features(fi, self._geom_json(simple))
        fc = '{"type":"FeatureCollection","features":[%s]}'
        return '{"run":%d,"near_r":%s,"near":%s,"far":%s}' % (self.run_id, near_r, fc % nf, fc % ff)

    def buildings_in(self, w, s, e, n, limit=60000):
        """Building polygons of the last run inside a box, as a JSON string. Above `limit`, the ones nearest the box
        centre are kept and `radius` (degrees of latitude) says how far from the centre the result is complete."""
        if self.last is None:
            return '{"type":"FeatureCollection","run":0,"radius":null,"features":[]}'
        idx, radius = self.g.sindex.query(box(w, s, e, n)), "null"
        if len(idx) > limit:
            idx, d = self._nearest_first(idx, (s + n) / 2, (w + e) / 2)
            idx, radius = idx[:limit], "%.6f" % d[limit - 1]
        feats = self._features(idx, self._geom_json(self.last.geometry.values[idx]))
        return '{"type":"FeatureCollection","run":%d,"radius":%s,"features":[%s]}' % (self.run_id, radius, feats)

    def building(self, i):
        """Popup details of one building from the last run, or None."""
        if self.last is None or not 0 <= i < len(self.last):
            return None
        row = self.last.iloc[i]
        return {k: (row[k].item() if hasattr(row[k], "item") else row[k]) for k in DETAIL if k in row.index}

    def __init__(self, path, data_dir=None):
        self.data_dir = data_dir
        if isinstance(path, gpd.GeoDataFrame):
            g = path
        elif str(path).endswith(".parquet"):
            g = gpd.read_parquet(path)
        else:
            g = gpd.read_file(path)
        self.g = g.reset_index(drop=True)
        self.last, self.run_id = None, 0
        c = self.g.to_crs(self.g.estimate_utm_crs()).centroid
        self.x, self.y = c.x.to_numpy(), c.y.to_numpy()
        ll = c.to_crs(4326)
        self.lat, self.lon = ll.y.to_numpy(), ll.x.to_numpy()
        self.vs30, self.soil = vs30_at(self.lat, self.lon)
        print(f"Soil: {self.soil}, median Vs30 {np.median(self.vs30):.0f} m/s")
        self.bounds = self.g.total_bounds
        if all(f"p_{t}" in self.g for t in TYPES):
            self.W = np.column_stack([self.g[f"p_{t}"].to_numpy(float) for t in TYPES])
            self.W /= self.W.sum(1, keepdims=True)
        else:                                       # older exposure files: one type per building
            self.W = np.column_stack([(self.g["btype"] == t).to_numpy(float) for t in TYPES])
        self.occ_by = {"night": self.g["occupants"].to_numpy(float)}
        self.occ_by["day"] = self.g["occ_day"].to_numpy(float) if "occ_day" in self.g else self.occ_by["night"]
        self.occ_by["commute"] = self.g["occ_commute"].to_numpy(float) if "occ_commute" in self.g else self.occ_by["night"]
        self.stats = dict(getattr(self.g, "attrs", {}).get("stats") or {})

    def run(self, lat, lon, depth_km, mag, time_of_day="night", strike=None, dip=None, rake=None):
        """One scenario. strike (degrees clockwise from north) switches on a finite rectangular rupture (dip and rake
        optional); without it the epicentral distance is converted to an average Rjb (see rupture.py)."""
        if time_of_day not in TIMES:
            raise ValueError(f"time_of_day must be one of {TIMES}")
        occ = self.occ_by[time_of_day]
        dx, dy = local_xy(lat, lon, self.lat, self.lon)
        r_eff, rjb, geo = rupture.distances(dx, dy, mag, depth_km, strike, dip, rake)
        region = gmm.region_for(lat, lon)
        mu, sigma, tau, phi, gm = gmm.pga(mag, r_eff, self.vs30, geo["mech"], region)
        pga = np.exp(mu)
        self.haz = {"mu": mu, "sigma": sigma, "tau": tau, "phi": phi, "rjb": rjb, "r_eff": r_eff,
                    "scenario": dict(lat=lat, lon=lon, depth_km=depth_km, mag=mag, strike=strike, dip=dip, rake=rake, time_of_day=time_of_day)}
        p = damage_probs(pga, self.W)                            # P(damage >= state), mixed over construction types
        ds = p.sum(1)                                            # expected damage 0-4
        cat = np.digitize(ds, [0.5, 1.5, 2.5])                 # 0 blue,1 green,2 yellow,3 red
        risk = occ * p[:, 2]                                     # people in extensive+ damage

        keep = ["geometry", "btype", "levels", "levels_src", "p_URM", "p_RC_LOW", "p_RC_MID", "use"]
        g = self.g[[c for c in keep if c in self.g]].copy()
        g["occupants"] = occ.round().astype(int)
        g["pga"], g["cat"], g["risk"] = pga.round(3), cat, risk.round(1)
        g["mmi"] = mmi_from_pga(pga).round(1)
        g["vs30"] = self.vs30.round().astype(int)
        for i, k in enumerate(["p_slight", "p_moderate", "p_extensive", "p_collapse"]):
            g[k] = p[:, i].round(3)

        key = (self.x // 500).astype(int) * 100000 + (self.y // 500).astype(int)
        df = pd.DataFrame({"key": key, "lat": self.lat, "lon": self.lon, "ds": ds, "risk": risk,
                           "heavy": cat >= 2, "severe": cat == 3, "collapse": p[:, 3], "pga": pga})
        c = df.groupby("key").agg(lat=("lat", "mean"), lon=("lon", "mean"), n=("ds", "size"),
                                  heavy=("heavy", "sum"), severe=("severe", "sum"), risk=("risk", "sum"),
                                  collapse=("collapse", "sum"), mean_ds=("ds", "mean"), pga=("pga", "mean"))
        c["dist_km"] = haversine(lat, lon, c.lat.to_numpy(), c.lon.to_numpy())
        c = c.sort_values("risk", ascending=False)
        c["rank"] = 0
        c.iloc[:8, c.columns.get_loc("rank")] = np.arange(1, min(8, len(c)) + 1)
        feats = [{"type": "Feature",
                  "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]},
                  "properties": {"n": int(r.n), "heavy": int(r.heavy), "severe": int(r.severe),
                                 "risk": int(round(r.risk)), "collapse": round(float(r.collapse), 1),
                                 "mean_ds": round(float(r.mean_ds), 2), "pga": round(float(r.pga), 3),
                                 "dist_km": round(float(r.dist_km), 1), "rank": int(r.rank)}}
                 for r in c.itertuples()]
        self.last, self.run_id = g, next(_RUNS)
        return {"run": self.run_id, "cells": {"type": "FeatureCollection", "features": feats},
                "rupture": self._rupture_geojson(lat, lon, geo),
                "summary": {"buildings": len(g), "severe": int((cat == 3).sum()), "heavy": int((cat >= 2).sum()),
                            "people_at_risk": int(round(risk.sum())), "max_pga": round(float(pga.max()), 3), "max_mmi": round(float(mmi_from_pga(pga.max())), 1),
                            "vs30_median": int(np.median(self.vs30)), "soil": self.soil,
                            "expected_collapses": int(round(p[:, 3].sum())), "time_of_day": time_of_day,
                            "people_total": int(round(occ.sum())), "distance_method": geo["method"],
                            "mechanism": geo["mech"], "ztor_km": round(geo["ztor_km"], 1), "gm_region": gm["region"],
                            "gm_models": gm["models"], "beyond_validity": gm["beyond_validity"], **self._data_notes()}}

    @staticmethod
    def _rupture_geojson(lat, lon, geo):
        """Outline of the rupture's surface projection for the map (None when the strike is unknown or it is a point)."""
        r = geo["rupture"]
        if r is None or r.point:
            return None
        R = 6371000.0
        pts = [[lon + np.degrees(x / (R * np.cos(np.radians(lat)))), lat + np.degrees(y / R)] for x, y in r.polygon_xy()]
        if r.v1 - r.v0 < 1e-6:                       # vertical fault: a line
            return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [pts[0], pts[1]]}, "properties": {}}
        return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [pts + [pts[0]]]}, "properties": {}}

    def _data_notes(self):
        """How trustworthy the inputs are: share of measured storeys, population source and totals."""
        s = self.stats
        out = {}
        if "levels_measured_share" in s:
            out["storeys_measured_share"] = round(s["levels_measured_share"], 3)
            out["height_source"] = s.get("height_source")
        if "pop_src" in s:
            out["population_source"] = s["pop_src"]
            if s.get("ghs_total"):
                out["ghs_pop_total"] = int(round(s["ghs_total"]))
        return out
