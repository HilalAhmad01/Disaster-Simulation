"""The responder layer for one area and the run on the map: facilities with damage, road blockage, access, routes, dispatch
(MASTER_PLAN Phase 5). Wraps facilities.py, roads.py and responders.py; testable offline by passing a RoadNet and facilities."""
import pathlib, threading
from concurrent.futures import ThreadPoolExecutor
import numpy as np, pandas as pd, shapely
import facilities as F, roads as R, responders as P

N_SAMPLES = 50
PRIORITY_CELLS = 8

NEEDS_RUN = "No simulation is on the map (the server may have restarted). Run the simulation again."

class NeedsRun(ValueError):
    """There is no simulation on the map (none was run yet, or the server was restarted since). Answered with HTTP 409."""

class Layer:
    def __init__(self, model, net, fac):
        self.model, self.net, self.fac = model, net, fac.reset_index(drop=True)
        self.graph = P.Graph(net)
        tr = None
        if net.utm is not None:
            from pyproj import Transformer
            tr = Transformer.from_crs(4326, net.utm, always_xy=True)
        self._tr = tr
        fx, fy = (tr.transform(self.fac.lon.to_numpy(), self.fac.lat.to_numpy()) if tr is not None else (self.fac.lon.to_numpy(), self.fac.lat.to_numpy()))
        self.fnode = P.nearest_nodes(net, np.column_stack([fx, fy])) if len(self.fac) else np.array([], int)
        bx, by = (tr.transform(model.lon, model.lat) if tr is not None else (model.lon, model.lat))
        self.bnode = P.nearest_nodes(net, np.column_stack([bx, by]))
        self._geoms, self._pairs = None, None
        self.access = {}                                   # run -> result
        self._acc_lock = threading.Lock()                  # one access computation at a time; later callers reuse its result
    # ---- facilities ----
    def facility_state(self):
        """Facility table with the current run's damage: p_damaged, functionality, may_not_work."""
        if self.model.last is None:
            return self.fac.assign(p_damaged=0.0, functionality=1.0, may_not_work=False)
        return pd.concat([self.fac, F.functionality(self.fac, self.model.last["p_extensive"].to_numpy())], axis=1)

    def facilities_geojson(self):
        f = self.facility_state()
        feats = [{"type": "Feature", "id": int(r.id), "geometry": {"type": "Point", "coordinates": [r.lon, r.lat]},
                  "properties": {"kind": r.kind, "name": r.name or "", "beds": None if pd.isna(r.beds) else float(r.beds), "source": r.source,
                                 "p_damaged": round(float(r.p_damaged), 3), "may_not_work": bool(r.may_not_work)}} for r in f.itertuples()]
        return {"type": "FeatureCollection", "features": feats}

    # ---- blockage and access ----
    def _building_geoms_utm(self):
        if self._geoms is None:
            import geopandas as gpd
            g = gpd.GeoSeries(self.model.g.geometry.values, crs=4326)
            self._geoms = np.asarray(g.to_crs(self.net.utm).values, dtype=object) if self.net.utm is not None else np.asarray(g.values, dtype=object)
        return self._geoms

    def match_streets(self, cache=None, log=print):
        """Street-building pairs for the blockage model (run-independent). Found while the layer is prepared, so the first
        access estimate does not pay for the spatial query (~7 s on Noida), and kept in `cache` (.npz) for the next session."""
        if self._pairs is not None:
            return self._pairs
        import shapely
        g = self.model.g.geometry.values
        c = shapely.get_coordinates(np.asarray(g[:: max(1, len(g) // 500)], dtype=object))
        key = np.array([len(g), round(float(c.sum()), 5), self.net.n_edges, round(float(self.net.node_xy.sum()), 3), R.SEARCH_M])
        if cache is not None and pathlib.Path(cache).exists():
            try:
                with np.load(cache) as z:
                    if z["key"].shape == key.shape and np.array_equal(z["key"], key):
                        self._pairs = {k: z[k] for k in ("eb", "bi", "dist", "area")} | {"n": len(g), "search": R.SEARCH_M}
                        return self._pairs
            except Exception as ex:
                print(f"Rebuilding street matching: {ex}")
        log("Matching buildings to the streets they could block…")
        self._pairs = R.blockage_pairs(self.net, self._building_geoms_utm())
        if cache is not None:
            try:
                tmp = pathlib.Path(cache).with_suffix(".part.npz")
                np.savez(tmp, key=key, **{k: self._pairs[k] for k in ("eb", "bi", "dist", "area")})
                tmp.replace(cache)
            except Exception as ex:
                print(f"Could not cache the street matching: {ex}")
        return self._pairs

    def blockage(self):
        L = self.model.last
        h = L["levels"].to_numpy(float) * R.STOREY_M
        import simulate as S
        return R.blockage(self.net, self._building_geoms_utm(), h, L["p_collapse"].to_numpy(float), L["p_extensive"].to_numpy(float),
                          p_masonry=self.model.W[:, S.TYPES.index("URM")], pairs=self.match_streets())

    def working_hospitals(self, state=None):
        s = state if state is not None else self.facility_state()
        m = (s.kind == "hospital") & (self.fnode >= 0)
        return np.flatnonzero(m.to_numpy())

    def compute_access(self, n=N_SAMPLES, seed=0):
        """Blockage for the run on the map, Monte Carlo access to the nearest working hospital, per-cell summary."""
        run = self.model.run_id
        state = self.facility_state()
        p_edge = self.blockage()
        hosp = self.working_hospitals(state)
        if len(hosp) == 0:
            raise ValueError("No hospitals were found in this area, so access times cannot be computed.")
        stats = P.access_mc(self.net, self.fnode[hosp], state.functionality.to_numpy()[hosp], p_edge, n=n, seed=seed, graph=self.graph)
        sn = self.model.mc_snapshot()
        occ = self.model.occ_by[sn["scenario"]["time_of_day"]]
        pa = P.people_access(self.net, stats, self.bnode, occ, sn["cell"])
        nc = len(pa["people"])
        cnt = np.bincount(sn["cell"], minlength=nc)
        clat, clon = np.bincount(sn["cell"], weights=sn["lat"], minlength=nc) / cnt, np.bincount(sn["cell"], weights=sn["lon"], minlength=nc) / cnt
        feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [float(clon[c]), float(clat[c])]},
                  "properties": {"minutes": round(float(pa["cell_minutes"][c]), 1), "p_reach": round(float(pa["cell_p_reach"][c]), 2), "people": int(pa["people"][c])}}
                 for c in range(nc) if cnt[c] > 0]
        blocked = pd.Series(p_edge).groupby(self.net.road_id).max()
        res = {"run": run, "cells": {"type": "FeatureCollection", "features": feats}, "p_edge": p_edge, "stats": stats, "hospitals_total": int(len(hosp)),
               "hospitals_may_not_work": int((state.may_not_work.to_numpy()[hosp]).sum()),
               "summary": {"share_within_30min": round(pa["share_within_30min"], 3), "share_no_access": round(pa["share_no_access"], 3),
                           "median_minutes": round(pa["median_minutes"], 1), "streets": int(len(blocked)),
                           "streets_likely_blocked": int((blocked > 0.5).sum()), "streets_any_risk": int((blocked > 0.05).sum()),
                           "hospitals": int(len(hosp)), "hospitals_may_not_work": int((state.may_not_work.to_numpy()[hosp]).sum()), "samples": n,
                           "blockage_constants": {"min_free_width_m": R.FREE_MIN_M, "direction_weight": R.DIR_W, "calibrated": "form and 3.5 m criterion from Anelli et al. 2020; direction weights assumed"}}}
        self.access[run] = res
        for old in sorted(self.access)[:-3]:             # each result holds per-sample arrays over all road nodes
            self.access.pop(old, None)
        return res

    # ---- routes and dispatch ----
    def label(self, i):
        """A facility's name, or 'unnamed fire station' (not the raw kind 'fire_station') when OpenStreetMap has none."""
        n = self.fac.name[i]
        return n.strip() if isinstance(n, str) and n.strip() else f"unnamed {self.fac.kind[i].replace('_', ' ')}"

    def _cell_node(self, rank):
        c = next((f for f in self.model.cells if f["properties"]["rank"] == rank), None)
        if c is None:
            raise KeyError(f"No priority cell {rank}")
        lon, lat = c["geometry"]["coordinates"]
        xy = self._tr.transform(lon, lat) if self._tr is not None else (lon, lat)
        return int(P.nearest_nodes(self.net, [xy])[0]), c

    def ensure_access(self):
        """Access for the run on the map, computed now if nobody has yet. Routes and dispatch need it; they used to refuse
        (HTTP 409) until 'Estimate hospital access' had been pressed for that run, which was easy to miss."""
        if self.model.last is None:
            raise NeedsRun(NEEDS_RUN)
        with self._acc_lock:
            a = self.access.get(self.model.run_id)
            return a if a is not None else self.compute_access()

    def route(self, source, rank):
        """source: facility id, or 'nearest:<kind>' (the working facility of that kind with the shortest expected time)."""
        a = self.ensure_access()
        node, cell = self._cell_node(rank)
        state = self.facility_state()
        if isinstance(source, str) and source.startswith("nearest:"):
            kind = source.split(":", 1)[1]
            cand = np.flatnonzero(((state.kind == kind) & ~state.may_not_work).to_numpy())
            if len(cand) == 0:
                raise ValueError(f"No working {kind.replace('_', ' ')} found.")
            M = self.graph.matrix(P.expected_cost(self.net, a["p_edge"]))
            from scipy.sparse.csgraph import dijkstra
            # one multi-source pass (min_only) instead of a full facilities x nodes matrix; `sources` names the nearest one
            d, _, src = dijkstra(M, directed=True, indices=self.fnode[cand], min_only=True, return_predecessors=True)
            if not np.isfinite(d[node]):
                raise ValueError("No open route found.")
            ties = cand[self.fnode[cand] == src[node]]
            best = int(ties[0])
        else:
            best = int(source)
        r = P.route(self.net, int(self.fnode[best]), node, a["p_edge"], self.graph)
        if r is None:
            raise ValueError("No open route found.")
        gj = P.route_geojson(self.net, r)
        gj["properties"].update(facility_id=int(self.fac.id[best]), facility=self.label(best), cell_rank=rank)
        return gj

    def dispatch(self, teams_per_kind):
        """teams_per_kind: {'fire_station': 3, 'ambulance': 2, ...}; teams are spread round-robin over working facilities of that kind."""
        a = self.ensure_access()
        state = self.facility_state()
        teams = []
        for kind, k in teams_per_kind.items():
            k = int(k)
            pool = np.flatnonzero(((state.kind == kind) & ~state.may_not_work).to_numpy()) if kind != "ambulance" else \
                np.flatnonzero((state.kind.isin(["ambulance", "hospital"]) & ~state.may_not_work).to_numpy())
            if k <= 0 or len(pool) == 0:
                continue
            for i in range(k):
                f = int(pool[i % len(pool)])
                teams.append({"team": f"{kind.replace('_', ' ')} {i + 1}", "facility": self.label(f), "node": int(self.fnode[f])})
        cells = []
        for ft in self.model.cells:
            p = ft["properties"]
            if p["rank"]:
                node, _ = self._cell_node(p["rank"])
                cells.append({"rank": p["rank"], "node": node, "people_at_risk": p["risk"]})
        return P.dispatch(self.net, teams, cells, a["p_edge"], self.graph)

def cache_ready(data_dir, area_id):
    """True when facilities and roads for this area are already on disk, so the layer can be rebuilt without the network."""
    import facilities as F2
    return F2.path_for(data_dir, area_id).exists() and R.graph_path(data_dir, area_id).exists()

_dl_lock = threading.Lock()      # one download at a time per process: a prefetch and a "Show hospitals" press never fetch the same area twice

def _fetch(data_dir, area_id, model, bbox, log, net=False):
    """Facilities (OpenStreetMap + Overture places, themselves fetched at once) and the road network, downloaded at the same time:
    on a big city each takes 30 s to minutes, so one after another they added up. Returns (facilities, RoadNet or None)."""
    import facilities as F2
    if _dl_lock.locked():
        log("The streets and facilities of this area are already being downloaded in the background; waiting for that…")
    with _dl_lock:
        with ThreadPoolExecutor(max_workers=2) as pool:
            log("Fetching hospitals, fire stations and streets at the same time…")
            ff = pool.submit(F2.load_or_build, data_dir, area_id, bbox, model.g)
            fr = pool.submit(R.load_net if net else R.load_or_download, data_dir, area_id, bbox, log=log)
            fac = ff.result()
            log("Facilities found. Waiting for the street network…" if not fr.done() else "Facilities and streets found.")
            return fac, fr.result()

def build_for_area(data_dir, area_id, model, bbox, log=print):
    """Download/cached facilities and roads for an area and return a Layer. Needs network the first time."""
    fac, net = _fetch(data_dir, area_id, model, bbox, log, net=True)
    layer = Layer(model, net, fac)
    layer.match_streets(R.graph_path(data_dir, area_id).with_name(f"{area_id}.streets.npz"), log=log)
    return layer
