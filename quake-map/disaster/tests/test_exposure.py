"""Phase 1: storeys from heights, type probabilities, population matching."""
import numpy as np, pandas as pd, pytest
gpd = pytest.importorskip("geopandas")
rasterio = pytest.importorskip("rasterio")
import shapely
from shapely.geometry import box
from rasterio.transform import from_origin

# ---------- helpers ----------
def utm_raster(path, arr, x0=500000.0, y1=3000000.0, res=2.0, epsg=32643):
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1, dtype="float32",
                       crs=f"EPSG:{epsg}", transform=from_origin(x0, y1, res, res), nodata=-99.0) as ds:
        ds.write(arr.astype("float32"), 1)

# ---------- 1.1 heights ----------
def test_zonal_median_known_heights():
    import heights
    tf = from_origin(0, 100, 2.0, 2.0)                     # 50 x 50 pixels, 2 m
    arr = np.zeros((50, 50), np.float32)
    arr[10:20, 10:20] = 9.0                               # block A: 20 m square at 9 m
    arr[30:40, 30:40] = 12.0
    arr[30:32, 30:40] = 100.0                              # outliers on 2 of 10 rows: median unaffected
    arr[0, 0] = -99.0
    a, b = box(20, 60, 40, 80), box(60, 20, 80, 40)       # exactly cover the two blocks (x0..x1, y0..y1 in metres)
    med, cnt = heights.zonal_median(np.array([a, b], dtype=object), arr, tf)
    assert med[0] == 9.0 and cnt[0] == 100
    assert med[1] == 12.0 and cnt[1] == 100

def test_zonal_median_small_footprint_uses_centroid_and_nodata_is_skipped():
    import heights
    tf = from_origin(0, 20, 2.0, 2.0)
    arr = np.full((10, 10), -99.0, np.float32)
    arr[4:6, 4:6] = [[6.0, 6.0], [6.0, 6.0]]
    tiny = box(9.2, 9.2, 10.8, 10.8)                       # smaller than one pixel
    nodata_only = box(1, 1, 5, 5)                          # all nodata
    med, cnt = heights.zonal_median(np.array([tiny, nodata_only], dtype=object), arr, tf)
    assert med[0] == pytest.approx(6.0) and cnt[0] < 2
    assert np.isnan(med[1])

def test_zonal_median_banding_is_exact():
    import heights
    rng = np.random.default_rng(1)
    arr = rng.uniform(0, 30, (400, 400)).astype(np.float32)
    tf = from_origin(0, 800, 2.0, 2.0)
    gx, gy = np.meshgrid(np.arange(0, 780, 26.0), np.arange(0, 780, 26.0))      # non-overlapping footprints, jittered inside cells
    jit = rng.uniform(0, 10, (2, gx.size))
    polys = np.array([box(x + a, y + b, x + a + 12, y + b + 12) for x, y, a, b in zip(gx.ravel(), gy.ravel(), jit[0], jit[1])], dtype=object)
    full, c1 = heights.zonal_median(polys, arr, tf)
    small, c2 = heights.zonal_median(polys, arr, tf, band_pix=4000)       # forces many bands
    np.testing.assert_array_equal(full, small); np.testing.assert_array_equal(c1, c2)

def test_building_heights_combines_years_and_reports_spread(tmp_path):
    import heights
    from pyproj import Transformer
    tr = Transformer.from_crs(32643, 4326, always_xy=True)
    x, y = 500100.0, 2999900.0
    foot = shapely.transform(box(x, y, x + 20, y + 20), lambda c: np.column_stack(tr.transform(c[:, 0], c[:, 1])))
    for year, val in ((2021, 6.0), (2022, 9.0), (2023, 12.0)):
        a = np.zeros((400, 400), np.float32); a[:, :] = val
        utm_raster(tmp_path / f"h{year}_32643.tif", a)
    h = heights.building_heights(np.array([foot], dtype=object), tmp_path)
    assert h.h_google[0] == 9.0 and h.h_spread[0] == 6.0 and h.h_years[0] == 3
    assert heights.building_heights(np.array([foot], dtype=object), tmp_path / "nothing") is None
    assert heights.building_heights(np.array([foot], dtype=object), tmp_path, years=[2023]).h_google[0] == 12.0

def test_storeys_from_height():
    import heights
    np.testing.assert_array_equal(heights.storeys_from_height([0.5, 3, 4.4, 4.6, 400], 3.0), [1, 1, 1, 2, 40])
    np.testing.assert_array_equal(heights.storeys_from_height([5.5, 5.7]), np.round(np.array([5.5, 5.7]) / heights.STOREY_M))

# ---------- priority order ----------
def _b(**cols):
    return pd.DataFrame(cols)

def test_levels_priority_label_over_height_label_over_google_over_guess():
    import exposure
    area = np.full(5, 100.0)
    b = _b(num_floors=[7, np.nan, np.nan, np.nan, np.nan], height=[np.nan, 18.0, np.nan, np.nan, np.nan])
    h = pd.DataFrame({"h_google": [30.0, 30.0, 15.0, 15.0, np.nan], "h_spread": [0, 0, 0, 9.0, 0]})
    d = exposure.attributes_full(b, area, h)
    assert list(d["levels_src"]) == ["label", "label_height", "google", "guess_unstable", "guess"]
    assert list(d["levels"]) == [7, 6, 5, 2, 2]               # unstable & missing fall back to the <150 m2 heuristic (2)

def test_tall_label_the_satellite_contradicts_is_rejected_and_shape_cap_applies():
    """80 floors / 300 m on a tower the satellite sees at 94 m (planned or still being built) must not stand; ordinary labels do."""
    import exposure
    b = _b(num_floors=[80, 30, 4, 12, np.nan], height=[300.0, np.nan, np.nan, np.nan, np.nan])
    h = pd.DataFrame({"h_google": [93.5, 85.0, 3.0, 20.0, 60.0], "h_spread": [95.0, 4.0, 1.0, 3.0, 2.0]})
    area = np.array([2400.0, 1500.0, 100.0, 400.0, 6.0])
    d = exposure.attributes_full(b, area, h)
    assert list(d["levels_src"]) == ["google_over_label", "label", "label", "label", "google"]
    # (0) 93.5 m / 2.8 = 33; (1) 30 floors against 85 m (30.4 storeys) agree; (2) low-rise labels are never second-guessed (satellite underreads);
    # (3) 12 floors against 20 m (7.1 storeys) differ by under 2x: kept
    assert d["levels"][0] == 33 and d["levels"][1] == 30 and d["levels"][2] == 4
    assert d["levels"][4] == 2                                  # a 6 m2 footprint is an outbuilding (at most 2 storeys) whatever the satellite says
    tower = exposure.attributes_full(_b(num_floors=[np.nan]), np.array([40.0]), pd.DataFrame({"h_google": [90.0], "h_spread": [1.0]}))
    assert tower["levels"][0] == 13                             # 90 m (32 storeys) on a 40 m2 footprint (6.3 m wide): at most 6 x 6.3 m = 38 m = 13 storeys

def test_google_height_too_low_is_unstable_and_no_raster_means_all_guess():
    import exposure
    b = _b(num_floors=[np.nan, np.nan])
    d = exposure.attributes_full(b, np.array([200.0, 200.0]), pd.DataFrame({"h_google": [1.0, 6.0], "h_spread": [0, 0]}))
    assert list(d["levels_src"]) == ["guess_unstable", "google"] and d["levels"][1] == 2
    d = exposure.attributes_full(b, np.array([200.0, 200.0]), None)
    assert set(d["levels_src"]) == {"guess"}

# ---------- 1.2 type probabilities ----------
def test_type_probabilities_sum_to_one_and_follow_storeys():
    import priors
    lv = np.array([1, 2, 3, 4, 5, 8, 20])
    p = priors.type_probs(lv, np.zeros(7, bool))
    np.testing.assert_allclose(p.sum(1), 1.0, atol=1e-9)
    assert p[0, 0] > 0.4 and p[4, 2] == pytest.approx(0.95) and p[6, 2] == pytest.approx(0.95)
    assert p[0, 0] > p[2, 0] > p[4, 0]                       # masonry share falls with storeys

def test_engineered_rule_keeps_rc_low_at_least_08():
    import priors
    p = priors.type_probs(np.array([1, 2, 4, 6]), np.array([True, True, True, True]))
    assert (p[:3, 1] >= 0.8 - 1e-9).all() and p[3, 2] == pytest.approx(0.95)   # tall engineered: ordinary rule
    np.testing.assert_allclose(p.sum(1), 1.0, atol=1e-9)

def test_prior_builder_rules(tmp_path):
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location("bcp", pathlib.Path(__file__).resolve().parent.parent / "tools" / "build_construction_priors.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    rows = m.mix_for(0.8, 0.5)
    assert rows["1"][0] == pytest.approx(0.8 * 0.5 + 0.5 * 0.8 * 0.5)
    assert rows["3-4"][1] == pytest.approx(0.6)             # concrete roof share 0.5 is clipped up to 0.6
    assert rows["5-7"] == (0.0, 0.05, 0.95)
    assert all(abs(sum(v) - 1) < 1e-9 for v in rows.values())

def test_mixed_damage_one_hot_matches_single_type_and_is_monotone():
    from simulate import damage_probs, FRAG, BETA
    from scipy.stats import norm
    pga = np.array([0.02, 0.1, 0.3, 0.8])
    W = np.tile([0.0, 1.0, 0.0], (4, 1))
    ref = norm.cdf(np.log(pga[:, None] / np.array(FRAG["RC_LOW"])) / BETA["RC_LOW"])
    np.testing.assert_allclose(damage_probs(pga, W), ref, atol=1e-12)
    Wm = np.tile([0.3, 0.5, 0.2], (4, 1))
    P = damage_probs(pga, Wm)
    assert (np.diff(P, axis=0) >= 0).all()                  # more shaking, more damage
    assert (np.diff(P, axis=1) <= 1e-12).all()              # exceedance falls with damage state
    expect = 0.3 * damage_probs(pga, np.tile([1.0, 0, 0], (4, 1))) + 0.5 * ref + 0.2 * damage_probs(pga, np.tile([0, 0, 1.0], (4, 1)))
    np.testing.assert_allclose(P, expect, atol=1e-12)

def test_old_exposure_without_p_columns_still_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("VS30_TIF", str(tmp_path / "none.tif"))
    from simulate import Model
    g = gpd.GeoDataFrame({"btype": ["URM", "RC_LOW"], "levels": [2, 3], "occupants": [10, 20]},
                         geometry=[box(77.4, 28.5, 77.4005, 28.5005), box(77.401, 28.5, 77.4015, 28.5005)], crs=4326)
    m = Model(g)
    assert m.W.tolist() == [[1, 0, 0], [0, 1, 0]]
    assert m.run(28.5, 77.4, 10, 6.5)["summary"]["buildings"] == 2

# ---------- 1.3 population ----------
def _grid_city(tmp_path, pop_per_pixel):
    """A 3 x 3 km city of 90 residential 'houses' per 1 km cell; GHS-like raster with a fixed count per pixel."""
    from pyproj import Transformer
    tr = Transformer.from_crs(32643, 4326, always_xy=True)
    geoms, x0, y0 = [], 500000.0, 3000000.0
    for cx in range(3):
        for cy in range(3):
            for i in range(9):
                for j in range(10):
                    x, y = x0 + cx * 1000 + 50 + i * 100, y0 + cy * 1000 + 50 + j * 95
                    geoms.append(box(x, y, x + 12, y + 12))
    g = gpd.GeoDataFrame(geometry=[shapely.transform(p, lambda c: np.column_stack(tr.transform(c[:, 0], c[:, 1]))) for p in geoms], crs=4326)
    g["class"] = "house"
    lon0, lat1 = tr.transform(x0 - 1000, y0 + 4000)
    lon1, lat0 = tr.transform(x0 + 4000, y0 - 1000)
    w, h = 300, 300
    with rasterio.open(tmp_path / "pop2025.tif", "w", driver="GTiff", height=h, width=w, count=1, dtype="float32", crs="EPSG:4326",
                       transform=rasterio.transform.from_bounds(lon0, lat0, lon1, lat1, w, h)) as ds:
        ds.write(np.full((h, w), pop_per_pixel, np.float32), 1)
    return g, (lon0, lat0, lon1, lat1)

def test_population_scaled_to_ghs_inside_complete_cells(tmp_path):
    import exposure
    g, _ = _grid_city(tmp_path, pop_per_pixel=0.5)
    ex = exposure.make_exposure(g, pop_raster=tmp_path / "pop2025.tif", data_dir=tmp_path)
    st = ex.attrs["stats"]
    assert st["pop_src"] == "GHS-POP" and st["ghs_total"] > 0
    # all buildings are residential: night people must match the GHS total inside the city's cells (+/- rounding)
    assert abs(st["night_total"] / st["ghs_total"] - 1) < 0.15
    assert ex["occupants"].sum() == pytest.approx(st["night_total"], rel=1e-3)
    assert ex["occ_day"].sum() < ex["occupants"].sum()            # people leave home by day
    assert {"capacity", "occ_day", "occ_commute", "occupants"} <= set(ex.columns)

def test_population_without_raster_falls_back_to_capacity_and_says_so():
    import exposure
    g = gpd.GeoDataFrame({"class": ["house", "office"]}, geometry=[box(77.4, 28.5, 77.4003, 28.5003), box(77.401, 28.5, 77.4013, 28.5003)], crs=4326)
    ex = exposure.make_exposure(g)
    assert "capacity" in ex.attrs["stats"]["pop_src"]
    assert ex.loc[0, "occupants"] == ex.loc[0, "capacity"]       # residential night = capacity
    assert ex.loc[1, "occupants"] < ex.loc[1, "capacity"]        # office: 2% at night
    assert ex.loc[1, "occ_day"] > ex.loc[1, "occupants"]

def test_time_of_day_changes_people_at_risk(tmp_path, monkeypatch):
    monkeypatch.setenv("VS30_TIF", str(tmp_path / "none.tif"))
    import exposure
    from simulate import Model
    g = gpd.GeoDataFrame({"class": ["house", "office"]}, geometry=[box(77.4, 28.5, 77.4003, 28.5003), box(77.4004, 28.5, 77.4007, 28.5003)], crs=4326)
    m = Model(exposure.make_exposure(g))
    night = m.run(28.5, 77.4, 10, 7.0, time_of_day="night")["summary"]
    day = m.run(28.5, 77.4, 10, 7.0, time_of_day="day")["summary"]
    assert night["time_of_day"] == "night" and day["time_of_day"] == "day"
    assert night["people_total"] != day["people_total"]
    with pytest.raises(ValueError):
        m.run(28.5, 77.4, 10, 7.0, time_of_day="noon")
