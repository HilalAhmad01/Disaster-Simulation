"""Hazard -> damage -> impact. All coefficients are v1 placeholders, see notes."""
import itertools, json
import numpy as np, pandas as pd, geopandas as gpd
import shapely
from shapely.geometry import box, mapping
from gmpe import pga_g, mmi_from_pga
from soil import vs30_at
from scipy.stats import norm

# Median PGA (g) at which damage state is reached: slight, moderate, extensive, complete.
# PLACEHOLDERS: replace with HAZUS / GEM fragility curves for Indian building types.
FRAG = {"URM": (0.10, 0.18, 0.32, 0.55),
        "RC_LOW": (0.18, 0.32, 0.55, 0.90),
        "RC_MID": (0.14, 0.26, 0.45, 0.75)}
BETA = 0.6   # lognormal dispersion

def haversine(lat1, lon1, lat2, lon2):
    p = np.pi / 180
    a = np.sin((lat2 - lat1) * p / 2) ** 2 + np.cos(lat1 * p) * np.cos(lat2 * p) * np.sin((lon2 - lon1) * p / 2) ** 2
    return 12742 * np.arcsin(np.sqrt(a))

_RUNS = itertools.count(1)        # run numbers stay unique across area switches, so stale popups can be detected
DETAIL = ["btype", "levels", "occupants", "use", "pga", "cat", "risk", "mmi", "vs30", "p_extensive", "p_collapse"]
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
            # simplify ~3 m, then round to 5 decimals (~1 m); rounding is ~13x faster than set_precision, same look
            simple = shapely.transform(shapely.simplify(np.asarray(geoms[fi], dtype=object), 0.00003), lambda c: np.round(c, 5))
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

    def __init__(self, path):
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
        self.meds = np.array([FRAG[t] for t in self.g["btype"]])
        self.occ = self.g["occupants"].to_numpy(float)

    def run(self, lat, lon, depth_km, mag):
        dist = haversine(lat, lon, self.lat, self.lon)
        pga = pga_g(mag, np.sqrt(dist ** 2 + depth_km ** 2), self.vs30)
        p = norm.cdf(np.log(pga[:, None] / self.meds) / BETA)   # P(damage >= state)
        ds = p.sum(1)                                            # expected damage 0-4
        cat = np.digitize(ds, [0.5, 1.5, 2.5])                 # 0 blue,1 green,2 yellow,3 red
        risk = self.occ * p[:, 2]                                # people in extensive+ damage

        g = self.g[["geometry", "btype", "levels", "occupants"] + (["use"] if "use" in self.g else [])].copy()
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
                "summary": {"buildings": len(g), "severe": int((cat == 3).sum()), "heavy": int((cat >= 2).sum()),
                            "people_at_risk": int(round(risk.sum())), "max_pga": round(float(pga.max()), 3), "max_mmi": round(float(mmi_from_pga(pga.max())), 1),
                            "vs30_median": int(np.median(self.vs30)), "soil": self.soil,
                            "expected_collapses": int(round(p[:, 3].sum()))}}
