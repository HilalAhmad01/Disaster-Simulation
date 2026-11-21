"""Routing, access and dispatch on a toy street grid (4-neighbour grid, 100 m blocks, 10 s per block)."""
import numpy as np, pytest
from shapely.geometry import LineString
import roads as R, responders as P

def grid(n=5, block=100.0, sec=10.0):
    ids = lambda i, j: j * n + i
    xy = [(i * block, j * block) for j in range(n) for i in range(n)]
    u, v, g = [], [], []
    for j in range(n):
        for i in range(n):
            for di, dj in ((1, 0), (0, 1)):
                if i + di < n and j + dj < n:
                    a, b = ids(i, j), ids(i + di, j + dj)
                    line = LineString([xy[a], xy[b]])
                    u += [a, b]; v += [b, a]; g += [line, line]
    m = len(u)
    return R.RoadNet(xy, u, v, [sec] * m, [block] * m, g, [6.0] * m), ids

def edge_between(net, a, b):
    return int(np.flatnonzero((net.u == a) & (net.v == b))[0])

def test_free_flow_times_are_manhattan_distances():
    net, ids = grid()
    t = P.expected_times(net, [ids(0, 0)], np.zeros(net.n_edges))
    assert t[ids(4, 4)] == pytest.approx(80.0) and t[ids(2, 1)] == pytest.approx(30.0) and t[ids(0, 0)] == 0

def test_multi_source_takes_the_nearest():
    net, ids = grid()
    t = P.expected_times(net, [ids(0, 0), ids(4, 4)], np.zeros(net.n_edges))
    assert t[ids(4, 3)] == pytest.approx(10.0) and t[ids(2, 2)] == pytest.approx(40.0)

def test_certain_closure_forces_a_detour_and_expected_cost_avoids_risky_street():
    net, ids = grid()
    p = np.zeros(net.n_edges)
    for a, b in ((ids(1, 0), ids(2, 0)),):            # block the street 1-2 on the bottom row
        p[edge_between(net, a, b)] = p[edge_between(net, b, a)] = 1.0
    r = P.route(net, ids(0, 0), ids(4, 0), p)
    assert r["p_open"] == 1.0 and r["time_s"] == pytest.approx(60.0)          # detour over the second row: 2 extra blocks
    assert ids(2, 0) not in r["nodes"] or ids(1, 0) not in r["nodes"] or abs(r["nodes"].index(ids(1, 0)) - r["nodes"].index(ids(2, 0))) != 1
    p2 = np.zeros(net.n_edges); p2[edge_between(net, ids(1, 0), ids(2, 0))] = p2[edge_between(net, ids(2, 0), ids(1, 0))] = 0.3
    r2 = P.route(net, ids(0, 0), ids(4, 0), p2)
    assert r2["time_s"] == pytest.approx(60.0) and r2["p_open"] == 1.0         # 0.3 * LAMBDA makes the direct street too costly

def test_p_open_is_the_product_over_streets_and_risky_segments_listed():
    g = [LineString([(i * 100, 0), (i * 100 + 100, 0)]) for i in range(3)]       # a chain 0-1-2-3: the only route is the chain
    u, v = [0, 1, 1, 2, 2, 3], [1, 0, 2, 1, 3, 2]
    net = R.RoadNet([[0, 0], [100, 0], [200, 0], [300, 0]], u, v, [10] * 6, [100] * 6, [g[0], g[0], g[1], g[1], g[2], g[2]], [6.0] * 6)
    p = np.array([0.1, 0.1, 0.4, 0.4, 0.0, 0.0])
    r = P.route(net, 0, 3, p)
    assert r["nodes"] == [0, 1, 2, 3] and r["p_open"] == pytest.approx(0.9 * 0.6)
    assert r["time_s"] == pytest.approx(30.0) and r["expected_time_s"] == pytest.approx(10 * (1 + 20 * 0.1) + 10 * (1 + 20 * 0.4) + 10)
    assert r["risky"] == [2]                                                    # only the 0.4 street passes the 0.3 threshold
    from pyproj import Transformer
    net.utm = "EPSG:32643"
    x0, y0 = Transformer.from_crs(4326, 32643, always_xy=True).transform(77.3, 28.5)
    net.geom = np.array([LineString([(x0 + 100 * i, y0), (x0 + 100 * i + 100, y0)]) for i in (0, 0, 1, 1, 2, 2)], dtype=object)
    gj = P.route_geojson(net, r)
    assert gj["geometry"]["type"] == "LineString" and len(gj["geometry"]["coordinates"]) == 4
    assert gj["geometry"]["coordinates"][0][0] == pytest.approx(77.3, abs=1e-4) and gj["properties"]["p_open"] == pytest.approx(0.54, abs=1e-3)
    assert len(gj["properties"]["risky_segments"]) == 1

def test_unreachable_destination_returns_none():
    net, ids = grid(3)
    p = np.zeros(net.n_edges)
    for e in np.flatnonzero((net.u == ids(2, 2)) | (net.v == ids(2, 2))):
        p[e] = 1.0
    assert P.route(net, ids(0, 0), ids(2, 2), p)["time_s"] > 0                # expected-cost route still exists (penalty, not removal)
    mc = P.access_mc(net, [ids(0, 0)], [1.0], p, n=20, seed=1)
    assert mc["p_reach"][ids(2, 2)] == 0.0 and mc["p_reach"][ids(1, 1)] == 1.0 and np.isinf(mc["median_s"][ids(2, 2)])

def test_access_mc_matches_closure_probability_and_seed():
    net, ids = grid(3)
    p = np.zeros(net.n_edges)
    for e in np.flatnonzero((net.u == ids(2, 2)) | (net.v == ids(2, 2))):      # both streets into the corner close together half the time
        p[e] = 0.5
    # the two streets are independent roads: corner reachable unless both closed -> 0.75
    a = P.access_mc(net, [ids(0, 0)], [1.0], p, n=2000, seed=3)
    assert a["p_reach"][ids(2, 2)] == pytest.approx(0.75, abs=0.04)
    b = P.access_mc(net, [ids(0, 0)], [1.0], p, n=2000, seed=3)
    np.testing.assert_array_equal(a["p_reach"], b["p_reach"])

def test_hospital_functionality_is_sampled():
    net, ids = grid(3)
    a = P.access_mc(net, [ids(0, 0)], [0.3], np.zeros(net.n_edges), n=1500, seed=2)
    assert a["p_reach"][ids(2, 2)] == pytest.approx(0.3, abs=0.04)

def test_dispatch_assigns_each_team_once_and_prefers_people_per_minute():
    net, ids = grid()
    teams = [{"team": "T1", "facility": "fire-A", "node": ids(0, 0)}, {"team": "T2", "facility": "fire-B", "node": ids(4, 4)}]
    cells = [{"rank": 1, "node": ids(4, 3), "people_at_risk": 1000}, {"rank": 2, "node": ids(1, 0), "people_at_risk": 900},
             {"rank": 3, "node": ids(2, 2), "people_at_risk": 5000}]
    out = P.dispatch(net, teams, cells, np.zeros(net.n_edges))
    assert len(out) == 2 and len({d["team"] for d in out}) == 2 and len({d["cell_rank"] for d in out}) == 2
    assert [d["order"] for d in out] == [1, 2] and out[0]["people_per_minute"] >= out[1]["people_per_minute"]
    by = {d["team"]: d for d in out}
    assert by["T1"]["cell_rank"] in (2, 3) and by["T2"]["cell_rank"] in (1, 3)
    assert P.dispatch(net, [], cells, np.zeros(net.n_edges)) == []

def test_people_access_summary():
    net, ids = grid(3)
    st = P.access_mc(net, [ids(0, 0)], [1.0], np.zeros(net.n_edges), n=5, seed=0)
    bnode = np.array([ids(0, 0), ids(1, 0), ids(2, 2)]); occ = np.array([10.0, 10.0, 80.0]); cell = np.array([0, 0, 1])
    a = P.people_access(net, st, bnode, occ, cell)
    assert a["cell_minutes"][0] == pytest.approx(10 / 60.0 / 2) and a["cell_minutes"][1] == pytest.approx(40 / 60.0)
    assert a["share_within_30min"] == pytest.approx(1.0) and a["share_no_access"] == 0
