"""Phase 2: finite-fault distances and their effect on shaking."""
import numpy as np, pytest
import rupture as R

def test_wells_coppersmith_known_values():
    L, W, A = R.wc94(7.0)
    assert L == pytest.approx(10 ** (-2.44 + 0.59 * 7)) and W == pytest.approx(10 ** (-1.01 + 0.32 * 7)) and A == pytest.approx(10 ** (-3.49 + 0.91 * 7))
    assert 40 < L < 60 and 15 < W < 25                          # M7: ~50 km x ~20 km, as in published tables

def test_mechanism_from_rake():
    assert [R.mechanism(r) for r in (0, 20, -20, 180, 170, 90, 60, -90, -60, None)] == ["ss", "ss", "ss", "ss", "ss", "rs", "rs", "ns", "ns", "unspec"]

def test_rupture_stays_in_seismogenic_layer_and_keeps_area():
    for mag, depth, dip in [(7.5, 10, 30), (8.0, 15, 30), (8.5, 15, 45), (6.0, 8, 60), (7.0, 12, 90)]:
        r = R.Rupture(mag, depth, 0, dip)
        sd = np.sin(np.radians(dip))
        assert r.z_top >= -1e-9 and r.z_top + r.W * sd <= R.Z_BOTTOM + 1e-6, (mag, depth, dip)
        if r.W < R.wc94(mag)[1] - 1e-9:                                  # width was clipped -> area kept by lengthening
            assert r.L * r.W == pytest.approx(R.wc94(mag)[2], rel=1e-6)
        else:                                                            # unclipped: the two WC94 regressions as published
            assert r.L == pytest.approx(R.wc94(mag)[0]) and r.W == pytest.approx(R.wc94(mag)[1])
        assert r.z_top <= depth + 1e-9 <= r.z_top + r.W * sd + 1e-6      # hypocentre lies on the rupture plane

def test_deep_event_falls_back_to_point_source():
    r = R.Rupture(7.0, 33, 0, 45)
    assert r.point and r.L == 0
    d = R.distances(np.array([0.0, 30e3]), np.array([0.0, 0.0]), 7.0, 33.0, strike=10)
    assert d[1] == pytest.approx([0.0, 30.0])                          # Rjb = epicentral distance
    assert d[0] == pytest.approx(np.hypot([0.0, 30.0], 33.0))          # r_eff includes the depth

def test_rjb_zero_above_rupture_and_grows_away():
    r = R.Rupture(7.5, 10, 90.0, 90.0)                                 # vertical fault striking east-west
    x = np.array([0.0, 10e3, 40e3, -40e3, 0, 0, 0])
    y = np.array([0.0, 0.0, 0.0, 0.0, 5e3, 20e3, 60e3])
    rjb = r.rjb(x, y)
    np.testing.assert_allclose(rjb[:4], 0, atol=1e-9)                           # along the 96 km trace
    assert rjb[4] == pytest.approx(5.0) and rjb[5] == pytest.approx(20.0) and rjb[6] == pytest.approx(60.0)
    beyond = r.rjb(np.array([60e3]), np.array([0.0]))                          # past the end of the trace
    assert beyond[0] == pytest.approx(60 - r.L / 2, abs=1e-6)

def test_dipping_rupture_projects_towards_downdip_side():
    r = R.Rupture(7.5, 10, 0.0, 30.0)                                  # strikes north; dips east (right-hand rule)
    assert r.rjb(np.array([5e3]), np.array([0.0]))[0] == 0            # east of the epicentre: over the dipping plane
    # the top edge is up-dip, i.e. west of the epicentre: the projection spans v0 < 0 .. v1 > 0 across strike
    assert r.v0 < -10 and 5 < r.v1 < 12 and r.v1 - r.v0 == pytest.approx(r.W * np.cos(np.radians(30)))
    assert r.rjb(np.array([-5e3]), np.array([0.0]))[0] == 0               # still over the up-dip part
    assert r.rjb(np.array([-20e3]), np.array([0.0]))[0] == pytest.approx(20 + r.v0)    # beyond the up-dip edge
    assert r.rjb(np.array([30e3]), np.array([0.0]))[0] == pytest.approx(30 - r.v1)

def test_rjb_matches_shapely_distance_to_the_polygon():
    shapely = pytest.importorskip("shapely")
    rng = np.random.default_rng(5)
    for strike in (0, 37, 135, 290):
        r = R.Rupture(7.2, 9.0, strike, 40.0)
        poly = shapely.Polygon(r.polygon_xy())
        x, y = rng.uniform(-90e3, 90e3, 200), rng.uniform(-90e3, 90e3, 200)
        ref = shapely.distance(shapely.points(x, y), poly) / 1000.0
        np.testing.assert_allclose(r.rjb(x, y), ref, atol=1e-9)

def test_small_magnitude_rjb_close_to_epicentral_distance():
    repi = np.array([5.0, 10.0, 30.0, 100.0])
    r = R.ps2ff_rjb(repi, 4.5)
    assert np.all(np.abs(r / repi - 1) < np.array([0.25, 0.15, 0.06, 0.02]))    # ~2 km rupture: nearly a point, more so far away
    assert np.all(np.diff(r / repi) > 0)                                        # ratio tends to 1 with distance
    big = R.ps2ff_rjb(repi, 7.5)
    assert np.all(big < r)                                             # bigger ruptures reach closer to the site
    assert R.ps2ff_rjb(np.array([0.0]), 7.0)[0] == 0
    assert np.all(R.ps2ff_rjb(np.array([1000.0, 2000.0]), 6.0) > 900)   # far away: ratio -> 1

def test_ps2ff_table_values_match_the_shipped_file():
    # first row of the table (0.1 km): R7 column = 0.033338
    assert R.ps2ff_rjb(np.array([0.1]), 7.0)[0] == pytest.approx(0.1 * 0.033338, rel=1e-5)
    # interpolation in magnitude: halfway between R7 and R7.25
    assert R.ps2ff_rjb(np.array([0.1]), 7.125)[0] == pytest.approx(0.1 * (0.033338 + 0.033214) / 2, rel=1e-5)

def test_depth_still_weakens_shaking_overhead():
    z = lambda d: R.distances(np.array([0.0]), np.array([0.0]), 5.5, d)[0][0]
    assert z(10) < z(20) < z(40)
    # shallow great rupture: top of rupture reaches the surface, so distance is Rjb itself
    assert R.distances(np.array([20e3]), np.array([0.0]), 8.0, 10.0)[0][0] == pytest.approx(R.ps2ff_rjb(np.array([20.0]), 8.0)[0])

# ---------- through the model ----------
def _model(tmp_path, monkeypatch):
    monkeypatch.setenv("VS30_TIF", str(tmp_path / "none.tif"))
    gpd = pytest.importorskip("geopandas")
    from shapely.geometry import box
    from simulate import Model
    lons, lats = np.meshgrid(np.linspace(76.4, 78.4, 9), np.linspace(27.6, 29.6, 9))
    geoms = [box(x, y, x + 3e-4, y + 3e-4) for x, y in zip(lons.ravel(), lats.ravel())]
    n = len(geoms)
    g = gpd.GeoDataFrame({"btype": ["RC_LOW"] * n, "levels": [3] * n, "occupants": [10] * n}, geometry=geoms, crs=4326)
    return Model(g)

def test_known_strike_elongates_shaking_along_the_fault(tmp_path, monkeypatch):
    m = _model(tmp_path, monkeypatch)
    lat0, lon0 = 28.6, 77.4
    m.run(lat0, lon0, 10, 7.8, strike=0.0, dip=90.0, rake=0.0)       # fault runs north-south
    pga = m.last["pga"].to_numpy()
    # sites ~80 km north and ~80 km east of the epicentre
    d = lambda lat, lon: int(np.argmin((m.lat - lat) ** 2 + (m.lon - lon) ** 2))
    north, east = pga[d(lat0 + 0.72, lon0)], pga[d(lat0, lon0 + 0.82)]
    assert north > 1.5 * east
    m.run(lat0, lon0, 10, 7.8, strike=90.0, dip=90.0, rake=0.0)      # rotate the fault
    pga2 = m.last["pga"].to_numpy()
    assert pga2[d(lat0, lon0 + 0.82)] > 1.5 * pga2[d(lat0 + 0.72, lon0)]

def test_unknown_strike_pattern_is_circular_and_outline_only_for_known_strike(tmp_path, monkeypatch):
    m = _model(tmp_path, monkeypatch)
    r = m.run(28.6, 77.4, 10, 7.0)
    assert r["rupture"] is None and "ps2ff" in r["summary"]["distance_method"]
    r = m.run(28.6, 77.4, 10, 7.0, strike=45.0, dip=40.0, rake=90.0)
    assert r["rupture"]["geometry"]["type"] == "Polygon" and r["summary"]["mechanism"] == "rs"
    r = m.run(28.6, 77.4, 10, 7.0, strike=45.0, dip=90.0, rake=0.0)
    assert r["summary"]["mechanism"] == "ss"
    assert m.run(28.6, 77.4, 10, 7.0, strike=45.0)["summary"]["mechanism"] == "unspec"

def test_mechanism_changes_pga_by_the_ba08_offsets(tmp_path, monkeypatch):
    import gmpe
    m = _model(tmp_path, monkeypatch)
    m.run(28.6, 77.4, 10, 6.5, strike=0.0, dip=60.0, rake=90.0); rs = m.last["pga"].to_numpy()
    m.run(28.6, 77.4, 10, 6.5, strike=0.0, dip=60.0, rake=-90.0); ns = m.last["pga"].to_numpy()
    assert np.all(rs >= ns) and rs.max() > ns.max()                      # reverse faulting shakes more than normal (BA08)
