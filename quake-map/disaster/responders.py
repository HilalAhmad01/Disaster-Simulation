"""Access, routes and dispatch on a RoadNet with blocked-road probabilities (MASTER_PLAN 5.4).

* access_mc: Monte Carlo over street closures (each street closed with its blockage probability; each hospital working with its
  functionality). Per node: median / 90th-percentile travel time to the nearest working hospital and P(reachable within a limit).
  Multi-source Dijkstra on scipy.sparse (one pass per sample).
* route: shortest path on the expected cost t * (1 + LAMBDA * P(blocked)); reports the chance the whole route is open and its
  risky segments.
* dispatch: teams to priority cells by linear assignment on travel_time / people_at_risk.
"""
import numpy as np, pandas as pd
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.optimize import linear_sum_assignment
from scipy.spatial import cKDTree
from roads import LAMBDA

REACH_LIMIT_S = 3600.0

class Graph:
    """Precomputed structure for fast repeated Dijkstra with edges switched off (parallel edges reduced to the cheapest open one)."""
    def __init__(self, net):
        self.net = net
        self.order = np.lexsort((net.v, net.u))
        u, v = net.u[self.order], net.v[self.order]
        new = np.r_[True, (u[1:] != u[:-1]) | (v[1:] != v[:-1])]
        self.starts = np.flatnonzero(new)
        self.gu, self.gv = u[self.starts], v[self.starts]
        self.shape = (net.n_nodes, net.n_nodes)

    def matrix(self, cost, open_edge=None):
        """CSR matrix of the cheapest open edge per (u, v); closed edges (open_edge False) are removed."""
        w = np.asarray(cost, float)[self.order].copy()
        if open_edge is not None:
            w[~np.asarray(open_edge)[self.order]] = np.inf
        g = np.minimum.reduceat(w, self.starts)
        ok = np.isfinite(g)
        return csr_matrix((g[ok], (self.gu[ok], self.gv[ok])), shape=self.shape)

def nearest_nodes(net, xy):
    """Index of the nearest road node for each (x, y) in the net's UTM metres."""
    return cKDTree(net.node_xy).query(np.asarray(xy, float))[1]

def expected_cost(net, p_edge):
    return net.t * (1.0 + LAMBDA * np.asarray(p_edge))

def access_mc(net, hosp_nodes, hosp_func, p_edge, n=50, seed=0, limit_s=REACH_LIMIT_S, graph=None):
    """Monte Carlo access. hosp_nodes: node per hospital; hosp_func: P(working) per hospital; p_edge: P(blocked) per directed edge
    (both directions of a street are closed together). Returns dict with arrays over nodes: median_s (inf if unreachable in
    over half the samples), p90_s, p_reach (share of samples reaching a hospital within limit_s), and overall n."""
    rng = np.random.default_rng(seed)
    g = graph or Graph(net)
    hosp_nodes, hosp_func = np.asarray(hosp_nodes, int), np.asarray(hosp_func, float)
    p_road = pd.Series(np.asarray(p_edge)).groupby(net.road_id).max().to_numpy()
    T = np.full((n, net.n_nodes), np.inf)
    for k in range(n):
        closed_road = rng.random(len(p_road)) < p_road
        working = rng.random(len(hosp_nodes)) < hosp_func
        if not working.any():
            continue
        d = dijkstra(g.matrix(net.t, ~closed_road[net.road_id]), directed=True, indices=np.unique(hosp_nodes[working]), min_only=True)
        T[k] = d
    # streets are two-way with equal time each way here, so distance from hospitals outward equals travel time inward
    big = 1e12                                   # percentiles over 'unreachable' (inf) samples: use a finite stand-in, then restore inf
    Tf = np.where(np.isfinite(T), T, big)
    med, p90 = np.median(Tf, axis=0), np.percentile(Tf, 90, axis=0)
    return {"median_s": np.where(med >= big / 2, np.inf, med), "p90_s": np.where(p90 >= big / 2, np.inf, p90), "p_reach": (T <= limit_s).mean(0), "n": n}

def expected_times(net, hosp_nodes, p_edge, graph=None):
    """Travel time from the nearest hospital under expected costs (no sampling)."""
    g = graph or Graph(net)
    return dijkstra(g.matrix(expected_cost(net, p_edge)), directed=True, indices=np.unique(hosp_nodes), min_only=True)

def route(net, src, dst, p_edge, graph=None, risky=0.3):
    """Shortest route src -> dst node on expected costs. Returns None if unreachable, else dict(time_s, p_open, edges, nodes,
    risky (list of edge ids with P(blocked) >= risky))."""
    g = graph or Graph(net)
    cost = expected_cost(net, p_edge)
    M = g.matrix(cost)
    dist, pred = dijkstra(M, directed=True, indices=src, return_predecessors=True)
    if not np.isfinite(dist[dst]):
        return None
    nodes = [int(dst)]
    while nodes[-1] != src:
        nodes.append(int(pred[nodes[-1]]))
    nodes.reverse()
    # edge ids: cheapest directed edge between consecutive nodes
    key = pd.DataFrame({"u": net.u, "v": net.v, "c": cost, "e": np.arange(net.n_edges)}).sort_values("c").drop_duplicates(["u", "v"]).set_index(["u", "v"])["e"]
    edges = [int(key[(a, b)]) for a, b in zip(nodes[:-1], nodes[1:])]
    p_road = {}
    for e in edges:
        p_road[net.road_id[e]] = max(p_road.get(net.road_id[e], 0.0), float(p_edge[e]))
    p_open = float(np.prod([1 - p for p in p_road.values()])) if p_road else 1.0
    free = float(net.t[edges].sum()) if edges else 0.0
    return {"time_s": free, "expected_time_s": float(dist[dst]), "p_open": p_open, "edges": edges, "nodes": nodes,
            "risky": [e for e in edges if p_edge[e] >= risky]}

def route_geojson(net, r):
    """GeoJSON LineString (lon/lat) of a route, from the edge geometries."""
    import shapely
    from pyproj import Transformer
    tr = Transformer.from_crs(net.utm, 4326, always_xy=True)
    coords = []
    for e in r["edges"]:
        xy = np.asarray(net.geom[e].coords)
        lon, lat = tr.transform(xy[:, 0], xy[:, 1])
        pts = list(zip(lon.tolist(), lat.tolist()))
        coords += pts if not coords else pts[1:]
    risky = []
    for e in r["risky"]:
        xy = np.asarray(net.geom[e].coords)
        lon, lat = tr.transform(xy[:, 0], xy[:, 1])
        risky.append(list(zip(lon.tolist(), lat.tolist())))
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"time_min": round(r["time_s"] / 60, 1), "expected_time_min": round(r["expected_time_s"] / 60, 1),
                           "p_open": round(r["p_open"], 3), "risky_segments": risky}}

def dispatch(net, teams, cells, p_edge, graph=None):
    """Assign teams to priority cells. teams: list of dict(team, facility, node); cells: list of dict(rank, node, people_at_risk).
    Minimises sum of travel_time / people_at_risk (linear assignment), i.e. reaches the most people per minute. Returns the
    assignments ordered by people reached per minute (best first), with route open-chance."""
    if not teams or not cells:
        return []
    g = graph or Graph(net)
    M = g.matrix(expected_cost(net, p_edge))
    nodes = np.array([t["node"] for t in teams])
    D = dijkstra(M, directed=True, indices=nodes)                           # (teams, n_nodes) expected seconds
    T = D[:, [c["node"] for c in cells]]
    risk = np.array([max(c["people_at_risk"], 1.0) for c in cells])
    C = np.where(np.isfinite(T), T / 60.0, 1e6) / risk[None, :]
    ti, ci = linear_sum_assignment(C)
    out = []
    for a, b in zip(ti, ci):
        if not np.isfinite(T[a, b]):
            continue
        r = route(net, teams[a]["node"], cells[b]["node"], p_edge, g)
        out.append({"team": teams[a]["team"], "facility": teams[a]["facility"], "cell_rank": cells[b]["rank"], "people_at_risk": int(risk[b]),
                    "travel_min": round(T[a, b] / 60.0, 1), "people_per_minute": round(float(risk[b] / max(T[a, b] / 60.0, 0.1)), 1),
                    "p_route_open": round(r["p_open"], 3) if r else None})
    out.sort(key=lambda d: -d["people_per_minute"])
    for i, d in enumerate(out):
        d["order"] = i + 1
    return out

def people_access(net, node_stats, bnode, occ, cell):
    """People-weighted access per cell and overall: minutes to the nearest working hospital (median over samples), chance of
    reaching one within the limit, and the share of people with no access."""
    t = node_stats["median_s"][bnode]
    pr = node_stats["p_reach"][bnode]
    nc = int(cell.max()) + 1
    w = np.bincount(cell, weights=occ, minlength=nc)
    tt = np.where(np.isfinite(t), t, REACH_LIMIT_S * 2)
    mean_min = np.bincount(cell, weights=occ * tt, minlength=nc) / np.maximum(w, 1e-9) / 60.0
    preach = np.bincount(cell, weights=occ * pr, minlength=nc) / np.maximum(w, 1e-9)
    tot = occ.sum()
    return {"cell_minutes": mean_min, "cell_p_reach": preach, "people": w,
            "share_within_30min": float((occ * (t <= 1800)).sum() / max(tot, 1e-9)), "share_no_access": float((occ * ~np.isfinite(t)).sum() / max(tot, 1e-9)),
            "median_minutes": float(np.average(np.minimum(t, 2 * REACH_LIMIT_S), weights=np.maximum(occ, 1e-9)) / 60.0)}
