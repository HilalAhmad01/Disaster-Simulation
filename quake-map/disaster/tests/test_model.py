"""Sanity tests: physics must behave sensibly. Run from the project root: pytest -q"""
import sys, pathlib
import numpy as np
import pytest

root = pathlib.Path(__file__).resolve().parent.parent
for d in (root, root / "backend"):
    sys.path.insert(0, str(d))
from gmpe import pga_g, mmi_from_pga

R = np.array([5, 10, 20, 50, 100, 200.0])

@pytest.mark.parametrize("vs30", [250, 760])
def test_shaking_falls_with_distance(vs30):
    assert np.all(np.diff(pga_g(6.5, R, vs30)) < 0)

def test_shaking_rises_with_magnitude():
    assert np.all(np.diff(pga_g(np.array([5, 6, 7, 8.0]), 20.0, 250)) > 0)

def test_soft_soil_amplifies_at_moderate_shaking():
    assert pga_g(6.0, 50.0, 250) > pga_g(6.0, 50.0, 760)

def test_plausible_anchors_rock():
    assert 0.15 < pga_g(7.0, 10.0, 760) < 0.45
    assert 0.03 < pga_g(5.0, 10.0, 760) < 0.15
    assert 0.02 < pga_g(7.5, 100.0, 760) < 0.12

def test_mmi_monotone_and_anchor():
    g = np.array([0.005, 0.02, 0.05, 0.15, 0.3, 0.6])
    assert np.all(np.diff(mmi_from_pga(g)) > 0)
    assert 6.0 <= mmi_from_pga(0.15) <= 7.5

def test_fragility_ordering():
    pytest.importorskip("geopandas")
    from simulate import FRAG
    for t, meds in FRAG.items():
        assert list(meds) == sorted(meds), t
    for k in range(4):
        assert FRAG["URM"][k] < FRAG["RC_LOW"][k]   # masonry is weaker than low-rise concrete

def test_model_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("VS30_TIF", str(tmp_path / "none.tif"))   # keep the test independent of any soil map
    gpd = pytest.importorskip("geopandas")
    from shapely.geometry import box
    from simulate import Model
    offs = [0.0, 0.1, 0.3, 0.6]                              # degrees east of the epicentre
    geoms = [box(77.40 + o, 28.50, 77.4005 + o, 28.5005) for o in offs]
    g = gpd.GeoDataFrame({"btype": ["URM"] * 4, "levels": [2] * 4, "occupants": [10] * 4}, geometry=geoms, crs=4326)
    f = tmp_path / "b.geojson"; g.to_file(f, driver="GeoJSON")
    m = Model(f)
    near = m.run(28.50, 77.40, 15, 6.5); pga = m.last["pga"].to_numpy()
    assert np.all(np.diff(pga) < 0)                          # farther = weaker shaking
    m.run(28.50, 77.40, 60, 6.5); assert np.all(m.last["pga"].to_numpy() <= pga)     # deeper = weaker
    m.run(28.50, 77.40, 15, 7.5); assert np.all(m.last["pga"].to_numpy() > pga)      # bigger = stronger
    assert near["summary"]["buildings"] == 4

def test_exposure_people_are_sane():
    import pandas as pd
    from exposure import attributes
    area = np.array([20000.0, 20000.0, 200.0, 800.0])
    b = pd.DataFrame({"class": ["warehouse", None, None, "apartments"], "height": [10.0, np.nan, np.nan, np.nan]})
    use, levels, btype, people = attributes(b, area)
    assert people.iloc[0] < 500 and people.iloc[1] < 500        # huge sheds are not crowded
    assert 10 <= people.iloc[2] <= 60                           # ordinary 3-storey house
    assert btype[0] == "RC_LOW"                                 # big sheds are not masonry

def test_place_name_choice():
    from places import pick_name
    assert pick_name({"suburb": "Sector 62", "city": "Noida"}) == "Sector 62, Noida"
    assert pick_name({"neighbourhood": "Sector 18", "suburb": "X", "town": "Noida"}) == "Sector 18, Noida"
    assert pick_name({"road": "Dadri Road"}) == "Dadri Road"
    assert pick_name({}) == ""

def test_bilinear_soil_sampling():
    from soil import bilinear
    arr = np.array([[100.0, 200.0], [300.0, 400.0]])
    kw = dict(x0=0.0, dx=1.0, y0=2.0, dy=-1.0)               # cell centres at lon 0.5,1.5 and lat 1.5,0.5
    assert bilinear(arr, lon=np.array([0.5]), lat=np.array([1.5]), **kw)[0] == pytest.approx(100)
    assert bilinear(arr, lon=np.array([1.5]), lat=np.array([0.5]), **kw)[0] == pytest.approx(400)
    assert bilinear(arr, lon=np.array([1.0]), lat=np.array([1.0]), **kw)[0] == pytest.approx(250)   # middle of the four
    assert bilinear(arr, lon=np.array([-5.0]), lat=np.array([9.0]), **kw)[0] == pytest.approx(100)   # outside: nearest edge

def test_soil_falls_back_to_flat(tmp_path):
    from soil import vs30_at, FLAT
    v, src = vs30_at(np.array([28.5, 28.6]), np.array([77.4, 77.5]), tif=tmp_path / "missing.tif")
    assert np.all(v == FLAT) and "flat" in src

def test_building_queries_are_lean_and_complete(tmp_path, monkeypatch):
    monkeypatch.setenv("VS30_TIF", str(tmp_path / "none.tif"))
    gpd = pytest.importorskip("geopandas")
    import json
    from shapely.geometry import box
    from simulate import Model
    offs = [0.0, 0.001, 0.002, 0.003, 0.004]
    geoms = [box(77.40 + o, 28.50, 77.4005 + o, 28.5005) for o in offs]
    g = gpd.GeoDataFrame({"btype": ["URM"] * 5, "levels": [2] * 5, "occupants": [10] * 5, "use": ["residential"] * 5},
                         geometry=geoms, crs=4326)
    m = Model(g)
    assert json.loads(m.buildings_in(77.3, 28.4, 77.5, 28.6))["features"] == []   # nothing before a run
    r = m.run(28.50, 77.40, 15, 7.0)
    view = (77.39, 28.49, 77.41, 28.51)
    fc = json.loads(m.buildings_in(*view))
    assert fc["run"] == r["run"] == m.run_id and fc["radius"] is None
    assert sorted(f["id"] for f in fc["features"]) == [0, 1, 2, 3, 4]
    f = next(f for f in fc["features"] if f["id"] == 2)
    assert set(f["properties"]) == {"cat", "levels", "pc"}
    assert f["properties"]["cat"] == int(m.last["cat"][2])
    assert f["geometry"]["coordinates"][0][0] == list(geoms[2].exterior.coords[0])   # full precision kept

    d = m.building(2)                                       # popup details match the run table
    assert d["mmi"] == m.last["mmi"][2] and d["use"] == "residential" and d["btype"] == "URM"
    assert set(d) >= {"btype", "levels", "occupants", "pga", "cat", "risk", "mmi", "vs30", "p_extensive", "p_collapse"}
    assert m.building(99) is None and m.building(-1) is None

    cut = json.loads(m.buildings_in(77.397, 28.49, 77.405, 28.51, limit=2))   # centre lon 77.401: nearest are 1, then 0
    assert sorted(f["id"] for f in cut["features"]) == [0, 1] and cut["radius"] > 0

    d3 = json.loads(m.buildings_3d(*view, 28.5, 77.4, near=2, far=2))
    assert [f["id"] for f in d3["near"]["features"]] == [0, 1] and len(d3["far"]["features"]) == 2
    assert d3["run"] == m.run_id and d3["near_r"] > 0
    assert m.run(28.50, 77.40, 15, 7.0)["run"] == r["run"] + 1   # every run gets a new number

def test_overture_reads_only_needed_columns():
    pa = pytest.importorskip("pyarrow")
    pytest.importorskip("geopandas")
    import types
    from overture import _read_columns, KEEP
    asked = {}
    class FakeDataset:
        schema = pa.schema([(c, pa.string()) for c in ("geometry", "class", "height", "names", "sources")])
        def to_table(self, columns, filter):
            asked["cols"] = columns
            return pa.table({c: ["x"] for c in columns})
    new = types.SimpleNamespace(_prepare_query=lambda kind, bbox: (FakeDataset(), None))
    t = _read_columns(new, (0, 0, 1, 1))
    assert asked["cols"] == ["geometry", "class", "height"] and set(t.column_names) <= set(KEEP)
    assert _read_columns(types.SimpleNamespace(_prepare_query=lambda kind, bbox: None), (0, 0, 1, 1)) is None
    assert _read_columns(types.SimpleNamespace(), (0, 0, 1, 1)) is False      # old package: caller falls back

def test_vs30_override_wins_where_it_has_values(tmp_path, monkeypatch):
    rasterio = pytest.importorskip("rasterio")
    from rasterio.transform import from_origin
    from soil import vs30_at, FLAT
    arr = np.zeros((10, 10), "float32"); arr[:, 5:] = 600.0            # right half has data (lon >= 77.45)
    p = tmp_path / "ov.tif"
    with rasterio.open(p, "w", driver="GTiff", height=10, width=10, count=1, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(77.40, 28.60, 0.01, 0.01), nodata=0) as ds:
        ds.write(arr, 1)
    monkeypatch.setenv("VS30_OVERRIDE_TIF", str(p)); monkeypatch.setenv("VS30_TIF", str(tmp_path / "none.tif"))
    v, src = vs30_at(np.array([28.55, 28.55, 28.2]), np.array([77.42, 77.47, 77.47]))
    assert list(v) == [FLAT, 600.0, FLAT] and "override" in src          # outside the raster and in its no-data half: mosaic/flat
