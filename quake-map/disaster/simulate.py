"""Hazard -> damage -> impact. All coefficients are v1 placeholders, see notes."""
import itertools, json
import numpy as np, pandas as pd, geopandas as gpd
import shapely
from shapely.geometry import box, mapping
from gmpe import pga_g, mmi_from_pga
import rupture, gmm, casualty
from soil import vs30_at
from scipy.special import ndtr

def load_fragility(path=None):
    """Median PGA (g) for slight/moderate/extensive/complete damage and lognormal dispersion per construction type, from
    data/priors/fragility.json (user calibration) or the shipped priors/fragility.json. The file carries its own provenance;
    shipped values are UNCALIBRATED v1 placeholders until Phase 3 data is available. Also returns per-class status text and
    any stored bootstrap samples (used by the Monte Carlo)."""
    import pathlib, priors as _p
    f = pathlib.Path(path) if path else _p.path("fragility.json")
    d = json.loads(f.read_text())
    frag = {t: tuple(float(x) for x in v) for t, v in d["pga_g_medians_slight_moderate_extensive_complete"].items()}
    return frag, {t: float(v) for t, v in d["beta"].items()}, d

FRAG, BETA, FRAGILITY_META = load_fragility()
TYPES = ("URM", "RC_LOW", "RC_MID")
TIMES = ("night", "day", "commute")

def damage_probs(pga, W, frag=None, beta=None):
    """P(damage >= slight/moderate/extensive/complete), shape (n, 4): the mix over construction types
    sum_t w_t * Phi((ln PGA - ln theta_t,k) / beta_t). pga in g, shape (n,); W (n, 3) type weights summing to 1."""
    return mix_exceedance(exceedance(pga, W, frag, beta), W, len(pga))

def exceedance(pga, W, frag=None, beta=None):
    """Per construction type: (column j, selector of buildings with weight > 0, P(DS >= k) for them). Shared by damage_probs and
    casualty.casualties_from so the Monte Carlo evaluates each fragility curve once per realisation."""
    frag, beta = frag or FRAG, beta or BETA
    lp = np.log(np.maximum(pga, 1e-9))[:, None]
    out = []
    for j, t in enumerate(TYPES):
        sel = W[:, j] > 0
        if not sel.any():
            continue
        out.append((j, sel, ndtr((lp[sel] - np.log(np.asarray(frag[t]))) / beta[t])))
    return out

def mix_exceedance(E, W, n):
    """Mix per-type exceedance (from exceedance()) over the type weights: (n, 4)."""
    P = np.zeros((n, 4))
    for j, sel, Pt in E:
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
DETAIL = ["btype", "levels", "levels_src", "p_URM", "p_RC_LOW", "p_RC_MID", "occupants", "use", "pga", "cat", "risk", "mmi", "vs30", "p_extensive", "p_collapse", "intensity"]
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
        d = {k: (row[k].item() if hasattr(row[k], "item") else row[k]) for k in DETAIL if k in row.index}
        mc = getattr(self, "mc", None)
        if mc and mc["run"] == self.run_id:                       # ranges from the Monte Carlo, once it has finished
            d["p_extensive_mean"], d["p_extensive_p90"] = round(float(mc["p_extensive_mean"][i]), 3), round(float(mc["p_extensive_p90"][i]), 3)
        return d

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
        self._cas = casualty.load(data_dir)

    def run(self, lat, lon, depth_km, mag, time_of_day="night", strike=None, dip=None, rake=None, pga_override=None):
        """One earthquake scenario. strike (degrees clockwise from north) switches on a finite rectangular rupture (dip and rake
        optional); without it the epicentral distance is converted to an average Rjb (see rupture.py).
        pga_override: per-building PGA in g (e.g. from a ShakeMap) used instead of the ground-motion models; the damage,
        people and aggregation steps are unchanged (used to validate the damage model apart from the shaking model)."""
        from hazards.earthquake import EarthquakeHazard
        return self.run_hazard(EarthquakeHazard(), dict(lat=lat, lon=lon, depth_km=depth_km, mag=mag, time_of_day=time_of_day,
                                                        strike=strike, dip=dip, rake=rake, pga_override=pga_override))

    def run_hazard(self, hazard, params):
        """One scenario of any hazard (MASTER_PLAN 7.1): the hazard supplies intensity and damage probabilities, everything else
        (people, aggregation, priority cells, casualties where the hazard has them) is shared."""
        time_of_day = params.get("time_of_day", "night")
        if time_of_day not in TIMES:
            raise ValueError(f"time_of_day must be one of {TIMES}")
        occ = self.occ_by[time_of_day]
        inten = hazard.intensity(self, params)
        self.haz = inten.get("haz")                              # what the Monte Carlo needs (earthquake only)
        value = inten["value"]
        p = hazard.damage(self, inten)                           # P(damage >= state), mixed over construction types
        ds = p.sum(1)                                            # expected damage 0-4
        cat = np.digitize(ds, [0.5, 1.5, 2.5])                 # 0 blue,1 green,2 yellow,3 red
        risk = occ * p[:, 2]                                     # people in extensive+ damage

        keep = ["geometry", "btype", "levels", "levels_src", "p_URM", "p_RC_LOW", "p_RC_MID", "use"]
        g = self.g[[c for c in keep if c in self.g]].copy()
        g["occupants"] = occ.round().astype(int)
        for k, v in hazard.table(self, inten).items():
            g[k] = v
        g["cat"], g["risk"] = cat, risk.round(1)
        for i, k in enumerate(["p_slight", "p_moderate", "p_extensive", "p_collapse"]):
            g[k] = p[:, i].round(3)

        key = (self.x // 500).astype(int) * 100000 + (self.y // 500).astype(int)
        meas = (self.g["levels_src"].isin(["label", "label_height", "google", "google_over_label"]).to_numpy() if "levels_src" in self.g else np.zeros(len(self.g), bool))
        df = pd.DataFrame({"key": key, "lat": self.lat, "lon": self.lon, "ds": ds, "risk": risk,
                           "heavy": cat >= 2, "severe": cat == 3, "collapse": p[:, 3], "pga": value, "measured": meas})
        c = df.groupby("key").agg(lat=("lat", "mean"), lon=("lon", "mean"), n=("ds", "size"),
                                  heavy=("heavy", "sum"), severe=("severe", "sum"), risk=("risk", "sum"),
                                  collapse=("collapse", "sum"), mean_ds=("ds", "mean"), pga=("pga", "mean"), measured=("measured", "mean"))
        org = hazard.origin(params)
        c["dist_km"] = haversine(org[0], org[1], c.lat.to_numpy(), c.lon.to_numpy()) if org else np.nan
        c = c.sort_values("risk", ascending=False)
        c["rank"] = 0
        c.iloc[:8, c.columns.get_loc("rank")] = np.arange(1, min(8, len(c)) + 1)
        feats = [{"type": "Feature",
                  "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]},
                  "properties": {"n": int(r.n), "heavy": int(r.heavy), "severe": int(r.severe),
                                 "risk": int(round(r.risk)), "collapse": round(float(r.collapse), 1),
                                 "mean_ds": round(float(r.mean_ds), 2), hazard.cell_key: round(float(r.pga), 3),
                                 "dist_km": round(float(r.dist_km), 1) if org else None, "rank": int(r.rank), "measured": round(float(r.measured), 2)}}
                 for r in c.itertuples()]
        heat = self._heat_grid(p[:, 2], risk)
        self.last, self.run_id, self.cells = g, next(_RUNS), feats
        self.hazard = hazard.name
        self.scenario = {"hazard": hazard.name, "time_of_day": time_of_day, **{k: v for k, v in params.items() if k != "pga_override" and (np.isscalar(v) or v is None)}}
        summary = {"buildings": len(g), "severe": int((cat == 3).sum()), "heavy": int((cat >= 2).sum()), "expected_heavy": int(round(p[:, 2].sum())),
                   "people_at_risk": int(round(risk.sum())), "expected_collapses": int(round(p[:, 3].sum())), "time_of_day": time_of_day,
                   "people_total": int(round(occ.sum())), "hazard": hazard.name, **hazard.summary(self, inten, p, occ), **self._data_notes()}
        out = {"run": self.run_id, "cells": {"type": "FeatureCollection", "features": feats}, "heat": heat, **hazard.extra_output(self, params, inten), "summary": summary}
        self.summary = out["summary"]
        return out

    # attributes a scenario run replaces; saved and restored around side runs (scenario comparison) so the page's run stays the model's run
    _RUN_STATE = ("haz", "last", "run_id", "cells", "hazard", "scenario", "summary", "mc")

    def snapshot_state(self):
        return {k: self.__dict__[k] for k in self._RUN_STATE if k in self.__dict__}

    def restore_state(self, snap):
        for k in self._RUN_STATE:
            if k in snap:
                self.__dict__[k] = snap[k]
            else:
                self.__dict__.pop(k, None)

    def run_aftershock(self, lat, lon, depth_km, mag, time_of_day=None, strike=None, dip=None, rake=None):
        """A second earthquake on the damage the last run left (see hazards/aftershock.py). The result is cumulative: people at risk,
        collapses and casualties are totals for both shocks; `aftershock` in the output holds what the second shock added."""
        from hazards.aftershock import AftershockHazard
        if self.last is None or getattr(self, "hazard", None) != "earthquake":
            raise ValueError("Run an earthquake first: an aftershock builds on the damage it left.")
        prior = self.last[["p_slight", "p_moderate", "p_extensive", "p_collapse"]].to_numpy(float)
        before = dict(self.summary)
        sc = dict(self.scenario)
        tod = time_of_day or sc.get("time_of_day", "night")
        out = self.run_hazard(AftershockHazard(prior), dict(lat=lat, lon=lon, depth_km=depth_km, mag=mag, time_of_day=tod,
                                                          strike=strike, dip=dip, rake=rake, pga_override=None))
        s = out["summary"]
        c0, c1 = before.get("casualties"), s.get("casualties")
        if c0 and c1:                                   # casualties were computed for the aftershock alone: add the first shock's
            tot = np.array([c0["injury_1"] + c1["injury_1"], c0["injury_2"] + c1["injury_2"], c0["injury_3"] + c1["injury_3"], c0["deaths"] + c1["deaths"]], float)
            s["casualties"] = casualty.summary(tot, c0["pager_deaths"] + c1["pager_deaths"], self._cas[1])
        s["aftershock"] = True
        self.scenario["aftershock"] = True
        out["aftershock"] = {"magnitude": mag, "lat": lat, "lon": lon, "depth_km": depth_km,
                             "added_collapses": s["expected_collapses"] - before["expected_collapses"],
                             "added_heavy": s["expected_heavy"] - before["expected_heavy"],
                             "added_people_at_risk": s["people_at_risk"] - before["people_at_risk"],
                             "added_deaths": (s["casualties"]["deaths"] - c0["deaths"]) if c0 and c1 else None,
                             "before": {k: before[k] for k in ("people_at_risk", "expected_collapses", "expected_heavy")},
                             "weakening": "Buildings already damaged are shaken up to 60% harder (an assumption)."}
        return out

    HEAT_POINTS = 12000      # about this many grid points cover the area: enough for a smooth surface, small enough to send with every run

    def _heat_grid(self, p_ext, risk):
        """Fine grid for the damage heat map: per square (100 to 500 m, chosen so the area has about HEAT_POINTS of them) the mean chance
        of heavy damage `pe`, the people at risk `r` and the building count `n`. The 500 m priority cells stay as they are (ranking,
        routes); this grid only draws the surface, so one big building cannot wash out a whole neighbourhood."""
        ext = max((self.x.max() - self.x.min()) * (self.y.max() - self.y.min()), 1.0)
        sp = float(np.clip(np.ceil(np.sqrt(ext / self.HEAT_POINTS) / 50.0) * 50.0, 100.0, 500.0))
        key = (self.x // sp).astype(np.int64) * 1_000_000 + (self.y // sp).astype(np.int64)
        d = pd.DataFrame({"key": key, "lat": self.lat, "lon": self.lon, "pe": p_ext, "r": risk})
        c = d.groupby("key").agg(lat=("lat", "mean"), lon=("lon", "mean"), pe=("pe", "mean"), r=("r", "sum"), n=("pe", "size"))
        feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(float(a), 5), round(float(b), 5)]},
                  "properties": {"pe": round(float(pe), 3), "r": round(float(r), 1), "n": int(n)}}
                 for a, b, pe, r, n in zip(c.lon.to_numpy(), c.lat.to_numpy(), c.pe.to_numpy(), c.r.to_numpy(), c.n.to_numpy())]
        return {"type": "FeatureCollection", "features": feats, "spacing_m": sp}

    def correlation_field(self, b_km=8.5):
        """Cached spatially correlated field generator for this area (Cholesky built once per range b)."""
        import uncertainty
        cache = self.__dict__.setdefault("_fields", {})
        if b_km not in cache:
            cache[b_km] = uncertainty.CorrelatedField(self.x, self.y, b_km)
        return cache[b_km]

    def mc_snapshot(self):
        """Everything the Monte Carlo needs from the last run, copied so later runs cannot change it under a background job."""
        if getattr(self, "haz", None) is None or self.last is None:
            raise ValueError("Run a simulation first.")
        h = self.haz
        key = (self.x // 500).astype(np.int64) * 100000 + (self.y // 500).astype(np.int64)
        cell = pd.factorize(key, sort=True)[0]
        return {"mu": h["mu"].copy(), "tau": np.asarray(h["tau"]).copy(), "phi": np.asarray(h["phi"]).copy(), "W": self.W, "frag": dict(FRAG), "beta": dict(BETA),
                "samples": FRAGILITY_META.get("samples", {}), "occ": self.occ_by[h["scenario"]["time_of_day"]], "cell": cell, "run": self.run_id, "casualty": self._cas, "lat": self.lat, "lon": self.lon,
                "scenario": dict(h["scenario"])}

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
