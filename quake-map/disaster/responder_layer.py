"""The responder layer for one area and the run on the map: facilities with damage, road blockage, access, routes, dispatch
(MASTER_PLAN Phase 5). Wraps facilities.py, roads.py and responders.py; testable offline by passing a RoadNet and facilities."""
import numpy as np, pandas as pd, shapely
import facilities as F, roads as R, responders as P

N_SAMPLES = 50
PRIORITY_CELLS = 8

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
        self._geoms = None
        self.access = {}                                   # run -> result
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

    def blockage(self):
        L = self.model.last
        h = L["levels"].to_numpy(float) * R.STOREY_M
        return R.blockage(self.net, self._building_geoms_utm(), h, L["p_collapse"].to_numpy(float), L["p_extensive"].to_numpy(float))

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
                           "blockage_constants": {"reach": R.REACH, "share_of_width": R.F_BLOCK, "partial": R.G_PARTIAL, "calibrated": False}}}
        self.access[run] = res
        return res

    # ---- routes and dispatch ----
    def _cell_node(self, rank):
        c = next((f for f in self.model.cells if f["properties"]["rank"] == rank), None)
        if c is None:
            raise KeyError(f"No priority cell {rank}")
        lon, lat = c["geometry"]["coordinates"]
        xy = self._tr.transform(lon, lat) if self._tr is not None else (lon, lat)
        return int(P.nearest_nodes(self.net, [xy])[0]), c

    def _need_access(self):
        a = self.access.get(self.model.run_id)
        if a is None:
            raise ValueError("Compute access for this simulation first.")
        return a

    def route(self, source, rank):
        """source: facility id, or 'nearest:<kind>' (the working facility of that kind with the shortest expected time)."""
        a = self._need_access()
        node, cell = self._cell_node(rank)
        state = self.facility_state()
        if isinstance(source, str) and source.startswith("nearest:"):
            kind = source.split(":", 1)[1]
            cand = np.flatnonzero(((state.kind == kind) & ~state.may_not_work).to_numpy())
            if len(cand) == 0:
                raise ValueError(f"No working {kind.replace('_', ' ')} found.")
            M = self.graph.matrix(P.expected_cost(self.net, a["p_edge"]))
            from scipy.sparse.csgraph import dijkstra
            d = dijkstra(M, directed=True, indices=self.fnode[cand])
            best = int(cand[np.argmin(d[:, node])])
            if not np.isfinite(d[:, node].min()):
                raise ValueError("No open route found.")
        else:
            best = int(source)
        r = P.route(self.net, int(self.fnode[best]), node, a["p_edge"], self.graph)
        if r is None:
            raise ValueError("No open route found.")
        gj = P.route_geojson(self.net, r)
        gj["properties"].update(facility_id=int(self.fac.id[best]), facility=self.fac.name[best] or self.fac.kind[best], cell_rank=rank)
        return gj

    def dispatch(self, teams_per_kind):
        """teams_per_kind: {'fire_station': 3, 'ambulance': 2, ...}; teams are spread round-robin over working facilities of that kind."""
        a = self._need_access()
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
                teams.append({"team": f"{kind.replace('_', ' ')} {i + 1}", "facility": self.fac.name[f] or self.fac.kind[f], "node": int(self.fnode[f])})
        cells = []
        for ft in self.model.cells:
            p = ft["properties"]
            if p["rank"]:
                node, _ = self._cell_node(p["rank"])
                cells.append({"rank": p["rank"], "node": node, "people_at_risk": p["risk"]})
        return P.dispatch(self.net, teams, cells, a["p_edge"], self.graph)

def build_for_area(data_dir, area_id, model, bbox, log=print):
    """Download/cached facilities and roads for an area and return a Layer. Needs network the first time."""
    import facilities as F2
    log("Finding hospitals, fire stations and shelters…")
    fac = F2.load_or_build(data_dir, area_id, bbox, model.g)
    G = R.load_or_download(data_dir, area_id, bbox, log=log)
    log("Building the road graph…")
    return Layer(model, R.from_osmnx(G, data_dir), fac)
