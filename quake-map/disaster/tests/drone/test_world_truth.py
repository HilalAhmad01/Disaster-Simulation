"""Hidden truth, the area index, chunk geometry (rubble, debris) and the numpy lidar on a synthetic city."""
import numpy as np, pytest
shapely = pytest.importorskip("shapely")
from drone.config import Config
from drone.georef import AreaFrame
from drone.world import AreaIndex, RoadIndex, World, raycast, scan_dirs, box_distance, K_RUBBLE, K_INTACT, K_DEBRIS
from drone.truth import Truth, COLLAPSED, PARTIAL, INTACT
import dronefix


@pytest.fixture(scope="module")
def city(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    d = tmp_path_factory.mktemp("city")
    c = dronefix.make_city(mp, d)
    m, layer = c["model"], c["layer"]
    cfg = Config()
    frame = AreaFrame(float(m.lon.mean()), float(m.lat.mean()))
    idx = AreaIndex(m, frame, cfg)
    import live
    p_edge = live.blockage_now(layer)
    truth = Truth(idx, m, cfg, 5, p_edge, layer.net.road_id)
    yield dict(m=m, layer=layer, cfg=cfg, frame=frame, idx=idx, truth=truth, roads=RoadIndex(layer.net, frame), p_edge=p_edge)
    mp.undo()


def test_index_local_coordinates_and_heights(city):
    idx, m = city["idx"], city["m"]
    assert idx.n == len(m.g) == 400
    assert abs(idx.x.mean()) < 600 and np.all(idx.height > 0)
    assert idx.half_x.min() >= 2.0
    assert len(idx.within(idx.x[0], idx.y[0], 1.0)) >= 1

def test_obb_is_a_close_fit_and_cached(city):
    idx = city["idx"]
    o = idx.obb([0, 1, 2])
    assert o.shape == (3, 5) and np.all(o[:, 2] >= o[:, 3] * 0.99)
    assert 5 < o[0, 2] < 20
    assert np.allclose(idx.obb([0]), o[:1])

def test_truth_is_deterministic_and_seed_sensitive(city):
    m, idx, cfg, layer = city["m"], city["idx"], city["cfg"], city["layer"]
    a = Truth(idx, m, cfg, 5, city["p_edge"], layer.net.road_id); b = Truth(idx, m, cfg, 5, city["p_edge"], layer.net.road_id)
    c = Truth(idx, m, cfg, 6, city["p_edge"], layer.net.road_id)
    assert np.array_equal(a.state, b.state) and np.array_equal(a.s_x, b.s_x)
    assert not np.array_equal(a.state, c.state)

def test_truth_matches_the_model_on_average():
    """Average collapsed count over many seeds approaches the model's expected collapses (statistical, fixed seeds)."""
    mp = pytest.MonkeyPatch()
    import tempfile, pathlib
    c = dronefix.make_city(mp, pathlib.Path(tempfile.mkdtemp()))
    m, cfg = c["model"], Config()
    frame = AreaFrame(float(m.lon.mean()), float(m.lat.mean())); idx = AreaIndex(m, frame, cfg)
    exp = float(m.last["p_collapse"].sum())
    got = np.mean([int((Truth(idx, m, cfg, s).state == COLLAPSED).sum()) for s in range(40)])
    mp.undo()
    assert exp > 5
    assert abs(got - exp) < max(3.0, 0.2 * exp)

def test_survivors_only_in_damaged_buildings_and_inside_the_area(city):
    t, idx = city["truth"], city["idx"]
    assert t.state[t.s_building[t.s_kind == 0]].min() >= PARTIAL            # trapped people are only in damaged buildings
    assert np.all(t.s_count >= 1)
    dist = np.hypot(t.s_x - idx.x[t.s_building], t.s_y - idx.y[t.s_building])
    assert dist.max() < 60
    assert t.trapped[t.state == INTACT].sum() == 0 and t.exposed[t.state == INTACT].sum() == 0
    s = t.stats()
    assert s["collapsed"] > 0 and s["survivor_sites"] > 0

def test_edge_blocking_is_per_street(city):
    t, layer = city["truth"], city["layer"]
    rid = layer.net.road_id
    for r in np.flatnonzero(t.road_blocked)[:5]:
        assert t.edge_blocked[rid == r].all()

def test_chunk_geometry_follows_damage(city):
    idx, truth, cfg, roads = city["idx"], city["truth"], city["cfg"], city["roads"]
    w = World(idx, truth, cfg, roads)
    i, j = AreaFrame.chunk_id(float(idx.x[0]), float(idx.y[0]), cfg.chunk_m)
    ch = w.chunk(i, j)
    assert len(ch.building_ids) > 0 and len(ch.boxes) >= len(ch.building_ids)
    again = World(idx, truth, cfg, roads).chunk(i, j)
    assert np.array_equal(ch.boxes, again.boxes)                            # deterministic for the same truth
    for b in ch.building_ids:
        rows = ch.boxes[(ch.boxes[:, 8] == b) & (ch.boxes[:, 7] != K_DEBRIS)]
        s = truth.state[b]
        top = float((rows[:, 2] + rows[:, 5]).max())
        if s == INTACT:
            assert len(rows) == 1 and top == pytest.approx(idx.height[b], rel=1e-6)
        elif s == COLLAPSED:
            assert top < 0.45 * idx.height[b] + 0.5 and (rows[:, 7] == K_RUBBLE).all()
    assert len(set(ch.building_ids.tolist())) == len(ch.building_ids)       # every building once
    j2 = ch.json()
    assert j2["id"] == f"c{i}_{j}" and len(j2["boxes"]) == len(ch.boxes)

def test_blocked_streets_get_debris(city):
    idx, truth, cfg, roads = city["idx"], city["truth"], city["cfg"], city["roads"]
    w = World(idx, truth, cfg, roads)
    blocked = np.flatnonzero(truth.edge_blocked)
    if not len(blocked):
        pytest.skip("no blocked street in this draw")
    e = int(blocked[0]); mx, my = roads.mx[e], roads.my[e]
    i, j = AreaFrame.chunk_id(mx, my, cfg.chunk_m)
    ch = w.chunk(i, j)
    assert (ch.boxes[:, 7] == K_DEBRIS).any()
    assert e in ch.boxes[ch.boxes[:, 7] == K_DEBRIS][:, 8]

def test_lru_cache_is_bounded(city):
    w = World(city["idx"], city["truth"], city["cfg"], city["roads"], lru=3)
    for k in range(6):
        w.chunk(k, 0)
    assert len(w._cache) == 3


# ---- raycast kernel
def test_raycast_hits_a_box_exactly():
    b = np.array([[10.0, 0.0, 5.0, 2.0, 3.0, 5.0, 0.0, 0, 7]])         # 4 x 6 x 10 box centred at x = 10
    t, w = raycast(np.array([0.0, 0.0, 5.0]), np.array([[1.0, 0, 0]]), b, 50)
    assert t[0] == pytest.approx(8.0) and w[0] == 0
    t, w = raycast(np.array([0.0, 0.0, 5.0]), np.array([[-1.0, 0, 0]]), b, 50)
    assert not np.isfinite(t[0]) and w[0] == -2

def test_raycast_rotated_box_and_ground():
    yaw = np.pi / 4
    b = np.array([[20.0, 0.0, 5.0, 3.0, 3.0, 5.0, yaw, 0, 1]])
    d = np.array([[1.0, 0, 0], [0, 0, -1.0]])
    t, w = raycast(np.array([0.0, 0.0, 5.0]), d, b, 80)
    assert t[0] == pytest.approx(20 - 3 * np.sqrt(2), abs=1e-6)         # a square rotated 45 degrees presents a corner
    assert t[1] == pytest.approx(5.0) and w[1] == -1

def test_raycast_matches_shapely_on_random_rays():
    rng = np.random.default_rng(2)
    boxes = np.array([[rng.uniform(-60, 60), rng.uniform(-60, 60), 8.0, rng.uniform(3, 8), rng.uniform(3, 8), 8.0, rng.uniform(0, 3), 0, k] for k in range(25)])
    polys = []
    for b in boxes:
        p = shapely.geometry.box(-b[3], -b[4], b[3], b[4])
        polys.append(shapely.affinity.translate(shapely.affinity.rotate(p, np.degrees(b[6]), origin=(0, 0)), b[0], b[1]))
    tree = shapely.STRtree(polys)
    checked = 0
    for _ in range(300):
        o = np.array([rng.uniform(-90, 90), rng.uniform(-90, 90), 3.0])
        if tree.query(shapely.Point(o[:2]), predicate="within").size:
            continue
        a = rng.uniform(0, 2 * np.pi)
        t, w = raycast(o, np.array([[np.cos(a), np.sin(a), 0.0]]), boxes, 120)
        line = shapely.LineString([o[:2], o[:2] + 120 * np.array([np.cos(a), np.sin(a)])])
        cand = tree.query(line, predicate="intersects")
        if cand.size:
            ref = min(shapely.Point(o[:2]).distance(polys[i].intersection(line)) for i in cand)
            assert t[0] == pytest.approx(ref, abs=1e-6)
            checked += 1
        else:
            assert not np.isfinite(t[0])
    assert checked > 50

def test_lidar_sees_buildings_and_ground_and_is_fast(city):
    import time
    w = World(city["idx"], city["truth"], city["cfg"], city["roads"])
    idx = city["idx"]
    pos = (float(idx.x[10]), float(idx.y[10]) - 20, 25.0)
    t0 = time.perf_counter()
    pts, kind, src = w.lidar(pos)
    first = time.perf_counter() - t0
    t0 = time.perf_counter(); w.lidar(pos); warm = time.perf_counter() - t0
    assert len(pts) > 20 and (kind == -1).any() and (kind >= 0).any()
    assert pts[:, 2].min() >= -1e-6 and np.all(np.hypot(pts[:, 0] - pos[0], pts[:, 1] - pos[1]) <= 61)
    assert warm < 0.05

def test_clearance_and_box_distance(city):
    w = World(city["idx"], city["truth"], city["cfg"], city["roads"])
    idx = city["idx"]
    b = int(np.flatnonzero(city["truth"].state == INTACT)[0])
    d, who = w.clearance(float(idx.x[b]), float(idx.y[b]), idx.height[b] + 10.0)
    assert 8.0 < d <= 10.5 and who == b                                    # 10 m above the roof of building b
    d_air, who = w.clearance(float(idx.x[b]), float(idx.y[b]), 90.0, r=10.0)
    assert d_air > 50
    assert box_distance(np.array([0.0, 0, 0]), np.array([[0, 0, 0, 1, 1, 1, 0, 0, 0.0]]))[0] == 0.0


def test_road_index_midpoints_are_on_the_streets_and_fast(city):
    import time
    roads = city["roads"]
    assert len(roads.mx) == city["layer"].net.n_edges
    for e in (0, 5, 17):
        pl = roads.polyline(e)
        lo, hi = pl.min(0) - 1e-6, pl.max(0) + 1e-6
        assert lo[0] <= roads.mx[e] <= hi[0] and lo[1] <= roads.my[e] <= hi[1]
    t0 = time.perf_counter(); RoadIndex(city["layer"].net, city["frame"]); assert time.perf_counter() - t0 < 0.5
