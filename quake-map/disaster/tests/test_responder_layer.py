"""Responder layer end to end on a toy city (no network): facilities, blockage from a run, access, route, dispatch."""
import numpy as np, pandas as pd, pytest
gpd = pytest.importorskip("geopandas")
from shapely.geometry import box, LineString
from pyproj import Transformer
import roads as R, responder_layer as RL

@pytest.fixture()
def layer(tmp_path, monkeypatch):
    monkeypatch.setenv("VS30_TIF", str(tmp_path / "none.tif"))
    from simulate import Model
    tr = Transformer.from_crs(32643, 4326, always_xy=True)
    x0, y0 = Transformer.from_crs(4326, 32643, always_xy=True).transform(77.40, 28.50)
    n = 7
    xy = [(x0 + i * 200, y0 + j * 200) for j in range(n) for i in range(n)]
    u, v, g = [], [], []
    for j in range(n):
        for i in range(n):
            for di, dj in ((1, 0), (0, 1)):
                if i + di < n and j + dj < n:
                    a, b = j * n + i, (j + dj) * n + i + di
                    line = LineString([xy[a], xy[b]]); u += [a, b]; v += [b, a]; g += [line, line]
    net = R.RoadNet(xy, u, v, [20.0] * len(u), [200.0] * len(u), g, [6.0] * len(u), utm="EPSG:32643")
    # buildings: 6-storey blocks along the streets (3-storey houses cannot cover a 6 m street under the plan's debris rule)
    geoms, lv = [], []
    for j in range(n):
        for i in range(n - 1):
            geoms.append(box(x0 + i * 200 + 70, y0 + j * 200 + 6, x0 + i * 200 + 90, y0 + j * 200 + 20)); lv.append(6)
    geoms = [shapely_t(tr, b) for b in geoms]
    ex = gpd.GeoDataFrame({"btype": "URM", "levels": lv, "occupants": 20, "use": "residential"}, geometry=geoms, crs=4326)
    m = Model(ex)
    lon0, lat0 = tr.transform(x0, y0)
    fx = [tr.transform(x0 + 10, y0 + 10), tr.transform(x0 + 1190, y0 + 1190), tr.transform(x0 + 1190, y0 + 10), tr.transform(x0 + 10, y0 + 1190)]
    fac = pd.DataFrame({"id": [0, 1, 2, 3], "kind": ["hospital", "hospital", "fire_station", "police"], "name": ["H1", "H2", "F1", "P1"],
                        "lon": [p[0] for p in fx], "lat": [p[1] for p in fx], "source": "osm", "beds": np.nan, "building_id": -1})
    return RL.Layer(m, net, fac), m, (lon0, lat0)

def shapely_t(tr, b):
    import shapely
    return shapely.transform(b, lambda c: np.column_stack(tr.transform(c[:, 0], c[:, 1])))

def test_facility_damage_follows_the_building_it_sits_in(layer):
    L, m, (lon0, lat0) = layer
    L.fac.loc[0, "building_id"] = 0                       # hospital H1 sits in building 0
    assert not L.facility_state().may_not_work.any()
    m.run(lat0, lon0, 10, 7.8)
    st = L.facility_state()
    assert st.p_damaged[0] == pytest.approx(m.last["p_extensive"][0]) and st.p_damaged[1] == 0.0
    assert bool(st.may_not_work[0]) == bool(m.last["p_extensive"][0] > 0.5)
    gj = L.facilities_geojson()
    assert len(gj["features"]) == 4 and gj["features"][0]["properties"]["kind"] == "hospital"

def test_access_route_and_dispatch_after_a_quake(layer):
    L, m, (lon0, lat0) = layer
    with pytest.raises(ValueError):
        L.dispatch({"fire_station": 1})                   # needs access first
    m.run(lat0 + 0.004, lon0 + 0.004, 8, 7.6)
    a = L.compute_access(n=20, seed=1)
    s = a["summary"]
    assert s["hospitals"] == 2 and 0 <= s["share_no_access"] <= 1 and s["samples"] == 20 and "Anelli" in s["blockage_constants"]["calibrated"]
    assert len(a["cells"]["features"]) > 0 and all(f["properties"]["minutes"] >= 0 for f in a["cells"]["features"])
    assert a["p_edge"].max() > 0                           # a strong quake blocks something
    rank = next(f["properties"]["rank"] for f in m.cells if f["properties"]["rank"] == 1)
    gj = L.route("nearest:fire_station", rank)
    assert gj["geometry"]["type"] == "LineString" and gj["properties"]["facility"] == "F1" and 0 <= gj["properties"]["p_open"] <= 1
    with pytest.raises(ValueError):
        L.route("nearest:ambulance", rank)                # none in this city
    out = L.dispatch({"fire_station": 2, "police": 1})
    assert 1 <= len(out) <= 3 and [d["order"] for d in out] == list(range(1, len(out) + 1))
    assert {d["facility"] for d in out} <= {"F1", "P1"}

def test_weak_quake_blocks_nothing_and_everything_is_reachable(layer):
    L, m, (lon0, lat0) = layer
    m.run(lat0 + 2.0, lon0 + 2.0, 10, 5.0)                 # far and small
    a = L.compute_access(n=10, seed=0)
    assert a["p_edge"].max() == 0 and a["summary"]["share_no_access"] == 0 and a["summary"]["streets_likely_blocked"] == 0

def test_route_and_dispatch_compute_access_when_it_is_missing(layer):
    """Routing used to fail ('Compute access for this simulation first', HTTP 409) unless access had been run for that run."""
    L, m, (lon0, lat0) = layer
    m.run(lat0 + 0.004, lon0 + 0.004, 8, 7.6)
    assert m.run_id not in L.access
    gj = L.route("nearest:fire_station", 1)
    assert gj["geometry"]["type"] == "LineString" and m.run_id in L.access
    first = L.access[m.run_id]
    assert L.dispatch({"fire_station": 1}) and L.access[m.run_id] is first          # reused, not recomputed
    m.run(lat0 + 0.002, lon0 + 0.002, 8, 7.0)                                         # a new run needs (and gets) its own
    L.route("nearest:fire_station", 1)
    assert m.run_id in L.access and L.access[m.run_id] is not first

def test_cached_street_matching_gives_the_same_blockage(layer, tmp_path):
    L, m, (lon0, lat0) = layer
    m.run(lat0 + 0.004, lon0 + 0.004, 8, 7.6)
    import simulate as S
    h = m.last["levels"].to_numpy(float) * R.STOREY_M
    args = (h, m.last["p_collapse"].to_numpy(float), m.last["p_extensive"].to_numpy(float))
    direct = R.blockage(L.net, L._building_geoms_utm(), *args, p_masonry=m.W[:, S.TYPES.index("URM")])
    cache = tmp_path / "streets.npz"
    L.match_streets(cache)
    assert cache.exists() and np.array_equal(L.blockage(), direct)
    L2 = RL.Layer(m, L.net, L.fac)
    L2.match_streets(cache)                                                           # loaded from the file this time
    assert np.array_equal(L2.blockage(), direct)
