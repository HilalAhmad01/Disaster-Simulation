"""Phase 7: hazard interface, flood and cyclone modules."""
import numpy as np, pytest
gpd = pytest.importorskip("geopandas")
from shapely.geometry import box
import hazards
from hazards import flood as FL, cyclone as CY
from synthetic import synthetic_exposure

@pytest.fixture()
def model(tmp_path, monkeypatch):
    monkeypatch.setenv("VS30_TIF", str(tmp_path / "none.tif"))
    from simulate import Model
    return Model(synthetic_exposure(500))

# ---------- interface ----------
def test_registry_and_describe():
    reg = hazards.registry()
    assert set(reg) == {"earthquake", "flood", "cyclone"}
    for h in reg.values():
        d = h.describe()
        assert d["name"] == h.name and d["unit"] and d["inputs"]

def test_earthquake_via_interface_equals_direct_run(model):
    a = model.run(28.575, 77.33, 10, 7.0, strike=30, dip=60, rake=90)
    from hazards.earthquake import EarthquakeHazard
    b = model.run_hazard(EarthquakeHazard(), dict(lat=28.575, lon=77.33, depth_km=10, mag=7.0, strike=30, dip=60, rake=90, time_of_day="night"))
    assert a["summary"] == {**b["summary"]} and a["cells"] == b["cells"] and a["rupture"] == b["rupture"]
    assert a["summary"]["hazard"] == "earthquake" and a["summary"]["peak"][0][0].startswith("Peak shaking")

# ---------- flood ----------
def depth_raster(path, depth_by_lon):
    import rasterio
    from rasterio.transform import from_origin
    arr = np.zeros((60, 60), "float32")
    for (c0, c1), d in depth_by_lon.items():
        arr[:, c0:c1] = d
    with rasterio.open(path, "w", driver="GTiff", height=60, width=60, count=1, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(77.30, 28.60, 0.001, 0.001), nodata=-9999) as ds:
        ds.write(arr, 1)

def test_flood_depth_sampling_and_nodata(tmp_path):
    depth_raster(tmp_path / "d.tif", {(0, 30): 2.0})
    d = FL.sample_depth(tmp_path / "d.tif", np.array([77.31, 77.34, 77.50]), np.array([28.57, 28.57, 28.57]))
    assert d[0] == pytest.approx(2.0) and d[1] == pytest.approx(0.0) and d[2] == 0.0           # wet, dry, outside the raster
    assert FL.sample_depth(tmp_path / "d.tif", np.array([77.31]), np.array([28.57]), scale=1.5)[0] == pytest.approx(3.0)

def test_state_depths_follow_the_curve():
    th = FL.state_depths()
    assert list(th) == sorted(th) and th[0] < 0.5 < th[-1] <= 6.0
    d, l = FL.depth_damage_curve()
    assert np.interp(th, d, l) == pytest.approx(FL.LOSS_THRESHOLDS, abs=1e-9)

def test_flood_damage_monotone_zero_when_dry_and_taller_buildings_fare_better(model, tmp_path):
    depth_raster(tmp_path / "d.tif", {(0, 20): 0.3, (20, 40): 1.5, (40, 60): 4.0})
    h = FL.FloodHazard()
    inten = h.intensity(model, {"raster": str(tmp_path / "d.tif")})
    p = h.damage(model, inten)
    assert p.shape == (len(model.g), 4) and (np.diff(p, axis=1) <= 1e-12).all()
    dry = inten["value"] == 0
    assert (p[dry] == 0).all()
    lv = model.g["levels"].to_numpy()
    wet = ~dry & (np.abs(inten["value"] - 1.5) < 0.05)
    if wet.any() and (lv[wet] <= 2).any() and (lv[wet] >= 6).any():
        assert p[wet & (lv <= 2)][:, 2].mean() > p[wet & (lv >= 6)][:, 2].mean()
    deep = np.abs(inten["value"] - 4.0) < 0.05
    assert p[deep][:, 2].mean() > p[~dry & (np.abs(inten["value"] - 0.3) < 0.05)][:, 2].mean()

def test_flood_end_to_end_run_and_no_raster_error(model, tmp_path):
    depth_raster(tmp_path / "d.tif", {(0, 60): 2.0})
    r = model.run_hazard(FL.FloodHazard(), {"raster": str(tmp_path / "d.tif"), "time_of_day": "night"})
    s = r["summary"]
    assert s["hazard"] == "flood" and s["flooded_buildings"] > 0 and "casualties" not in s and s["people_at_risk"] > 0
    assert all(f["properties"]["dist_km"] is None for f in r["cells"]["features"]) and "depth_m" in r["cells"]["features"][0]["properties"]
    assert "intensity" in model.last and "JRC" in s["hazard_note"]
    with pytest.raises(ValueError):
        model.run_hazard(FL.FloodHazard(), {})

# ---------- cyclone ----------
def test_holland_profile():
    r = np.array([1, 10, 40, 80, 200, 500.0])
    v = CY.holland_wind(r, 60, 40, 1.5, 20.0)
    assert np.argmax(v) == 2 and v[0] < 5 and (np.diff(v[2:]) < 0).all()                    # peaks at Rmax, falls beyond
    assert 52.0 < v[2] < 54.5                                                             # sqrt(B dp/(rho e)) minus Coriolis
    assert CY.holland_wind(40, 80, 40)[()] > CY.holland_wind(40, 60, 40)[()] > CY.holland_wind(40, 30, 40)[()]

def test_track_geometry_and_asymmetry():
    tl, tn = CY.track_points(20.0, 80.0, 0.0, 100.0, 10.0)                                # heading north
    assert tn == pytest.approx(80.0) and tl[0] < 20.0 < tl[-1] and len(tl) == 21
    tl, tn = CY.track_points(20.0, 80.0, 0.0, 300.0, 5.0)
    east = CY.peak_gust(np.array([20.0]), np.array([80.0 + 40 / 104.5]), tl, tn, 0.0, 10.0, 60, 40)[0]      # right of a northbound track
    west = CY.peak_gust(np.array([20.0]), np.array([80.0 - 40 / 104.5]), tl, tn, 0.0, 10.0, 60, 40)[0]
    assert east > west and east - west == pytest.approx(10.0 * CY.GUST_FACTOR * CY.TERRAIN_FACTOR, rel=0.15)   # 0.5*c on each side
    far = CY.peak_gust(np.array([20.0]), np.array([80.0 + 5.0]), tl, tn, 0.0, 10.0, 60, 40)[0]
    assert far < 0.5 * east

def test_cyclone_run_damage_orders_by_type_and_distance(model):
    r = model.run_hazard(CY.CycloneHazard(), dict(lat=28.575, lon=77.33, heading_deg=45, speed_ms=6, dp_hpa=90, rmax_km=20, time_of_day="day"))
    s = r["summary"]
    assert s["hazard"] == "cyclone" and s["max_gust_ms"] > 20 and r["track"]["geometry"]["type"] == "LineString" and "casualties" not in s
    far = model.run_hazard(CY.CycloneHazard(), dict(lat=34.0, lon=88.0, heading_deg=45, speed_ms=6, dp_hpa=90, rmax_km=20))
    assert far["summary"]["max_gust_ms"] < s["max_gust_ms"] and far["summary"]["heavy"] <= s["heavy"]
    stronger = model.run_hazard(CY.CycloneHazard(), dict(lat=28.575, lon=77.33, heading_deg=45, speed_ms=6, dp_hpa=130, rmax_km=20))
    assert stronger["summary"]["heavy"] >= s["heavy"] and stronger["summary"]["max_gust_ms"] > s["max_gust_ms"]

def test_wind_fragility_masonry_weaker_than_concrete(model):
    p = CY.CycloneHazard().damage(model, {"value": np.full(len(model.g), 60.0)})
    urm = model.g["btype"].to_numpy() == "URM"
    rc = model.g["btype"].to_numpy() == "RC_MID"
    assert p[urm][:, 2].mean() > p[rc][:, 2].mean()

def test_fast_peak_gust_equals_brute_force_over_the_whole_track():
    rng = np.random.default_rng(2)
    blat, blon = 28.5 + rng.uniform(-2.5, 2.5, 400), 77.3 + rng.uniform(-2.5, 2.5, 400)
    for heading, speed, dp, rmax in ((0, 5, 60, 40), (45, 12, 100, 25), (200, 0, 80, 60), (300, 8, 40, 15)):
        tl, tn = CY.track_points(28.5, 77.3, heading, 400.0, max(2.0, rmax / 8.0))
        fast = CY.peak_gust(blat, blon, tl, tn, heading, speed, dp, rmax)
        slow = CY.peak_gust(blat, blon, tl, tn, heading, speed, dp, rmax, fast=False)
        np.testing.assert_allclose(fast, slow, rtol=1e-3, atol=0.05)


def test_flood_curve_is_the_jrc_asia_residential_curve():
    """Values copied from the JRC workbook (Huizinga et al. 2017, 'Damage functions', ASIA, residential)."""
    d, l = FL.depth_damage_curve()
    np.testing.assert_allclose(d, [0, 0.5, 1, 1.5, 2, 3, 4, 5, 6])
    np.testing.assert_allclose(l, [0, 0.3266, 0.4941, 0.6166, 0.7207, 0.8695, 0.9315, 0.9836, 1.0], atol=5e-5)
    assert np.all(np.diff(l) > 0)
