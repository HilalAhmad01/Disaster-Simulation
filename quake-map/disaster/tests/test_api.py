"""API behaviour with a synthetic area: simulate, uncertainty job, popups."""
import time, pytest
pytest.importorskip("geopandas")
from fastapi.testclient import TestClient
from synthetic import synthetic_exposure

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VS30_TIF", str(tmp_path / "none.tif"))
    import app as appmod
    c = TestClient(appmod.create_app(tmp_path))
    st = c.app.state.st
    from simulate import Model
    st.model = Model(synthetic_exposure(500), data_dir=tmp_path)
    return c

def wait(c, url, key="state", done=("done", "error"), tries=300):
    for _ in range(tries):
        r = c.get(url).json()
        if r[key] in done:
            return r
        time.sleep(0.05)
    raise AssertionError("timeout")

def test_simulate_starts_uncertainty_and_serves_ranges(client):
    r = client.post("/api/simulate", json={"lat": 28.575, "lon": 77.33, "depth_km": 12, "magnitude": 6.8, "time_of_day": "day"})
    assert r.status_code == 200
    d = r.json()
    assert d["summary"]["time_of_day"] == "day" and d["uncertainty_job"].startswith("uncertainty-")
    res = wait(client, f"/api/uncertainty/{d['run']}")
    assert res["state"] == "done" and res["n"] == 200
    t = res["totals"]["people_at_risk"]
    assert t["p10"] <= t["p50"] <= t["p90"] and res["deterministic"]["people_at_risk"] == d["summary"]["people_at_risk"]
    assert 1 <= len(res["worst_case_cells"]) <= 8
    p = client.get(f"/api/building/0?run={d['run']}").json()
    assert 0 <= p["p_extensive_mean"] <= p["p_extensive_p90"] <= 1

def test_uncertainty_can_be_switched_off_and_unknown_runs_404(client):
    d = client.post("/api/simulate", json={"lat": 28.575, "lon": 77.33, "magnitude": 6.5, "uncertainty": False}).json()
    assert "uncertainty_job" not in d and client.get(f"/api/uncertainty/{d['run']}").status_code == 404

def test_stale_run_does_not_attach_ranges_to_the_new_map(client):
    d1 = client.post("/api/simulate", json={"lat": 28.575, "lon": 77.33, "magnitude": 6.5}).json()
    d2 = client.post("/api/simulate", json={"lat": 28.575, "lon": 77.33, "magnitude": 7.2, "uncertainty": False}).json()
    wait(client, f"/api/uncertainty/{d1['run']}")
    assert client.get(f"/api/building/0?run={d1['run']}").status_code == 409          # map shows run 2 now
    assert "p_extensive_p90" not in client.get(f"/api/building/0?run={d2['run']}").json()

def test_finite_fault_inputs_validated(client):
    ok = client.post("/api/simulate", json={"lat": 28.575, "lon": 77.33, "magnitude": 7.2, "strike": 45, "dip": 30, "rake": 90, "uncertainty": False}).json()
    assert ok["rupture"]["geometry"]["type"] == "Polygon" and ok["summary"]["mechanism"] == "rs"
    for bad in ({"strike": 400}, {"dip": 0}, {"rake": 300}, {"time_of_day": "noon"}):
        assert client.post("/api/simulate", json={"lat": 28.5, "lon": 77.3, "magnitude": 6.5, **bad}).status_code == 422

# ---------------- responder layer endpoints (toy layer injected, no network) ----------------
def test_responder_endpoints_flow(client, tmp_path, monkeypatch):
    import numpy as np, pandas as pd
    from shapely.geometry import LineString
    import roads as R, responder_layer as RL
    st = client.app.state.st
    m = st.model
    lon0, lat0 = float(m.lon.min()), float(m.lat.min())
    from pyproj import Transformer
    utm = m.g.estimate_utm_crs()
    fwd, inv = Transformer.from_crs(4326, utm, always_xy=True), Transformer.from_crs(utm, 4326, always_xy=True)
    cx, cy = fwd.transform(float(m.lon.mean()), float(m.lat.mean()))
    n = 6
    xy = [(cx - 2500 + i * 1000, cy - 2500 + j * 1000) for j in range(n) for i in range(n)]
    u, v, g = [], [], []
    for j in range(n):
        for i in range(n):
            for di, dj in ((1, 0), (0, 1)):
                if i + di < n and j + dj < n:
                    a, b = j * n + i, (j + dj) * n + i + di
                    ln = LineString([xy[a], xy[b]]); u += [a, b]; v += [b, a]; g += [ln, ln]
    net = R.RoadNet(xy, u, v, [60.0] * len(u), [1000.0] * len(u), g, [6.0] * len(u), utm=utm)
    fl = [inv.transform(*xy[0]), inv.transform(*xy[-1]), inv.transform(*xy[5])]
    fac = pd.DataFrame({"id": [0, 1, 2], "kind": ["hospital", "hospital", "fire_station"], "name": ["H1", "H2", "F1"], "lon": [p[0] for p in fl],
                        "lat": [p[1] for p in fl], "source": "osm", "beds": np.nan, "building_id": -1})
    assert client.get("/api/facilities").status_code == 409                       # not prepared yet
    monkeypatch.setattr(RL, "build_for_area", lambda *a, **k: RL.Layer(m, net, fac))
    jid = client.post("/api/responders/prepare").json()["job"]
    r = wait(client, f"/api/jobs/{jid}")
    assert r["state"] == "done" and r["result"]["facilities"]["hospital"] == 2
    assert len(client.get("/api/facilities").json()["features"]) == 3
    assert client.post("/api/access").status_code == 409                          # no simulation yet
    assert client.get("/api/route", params={"rank": 1}).status_code == 409             # no simulation yet
    d = client.post("/api/simulate", json={"lat": 28.575, "lon": 77.33, "depth_km": 8, "magnitude": 7.4, "uncertainty": False}).json()
    early = client.get("/api/route", params={"rank": 1, "source": "nearest:fire_station"})   # before "estimate access": used to be 409
    assert early.status_code == 200 and early.json()["geometry"]["type"] == "LineString"
    j = client.post("/api/access").json()
    r = wait(client, f"/api/jobs/{j['job']}")
    assert r["state"] == "done", r
    res = client.get(f"/api/access/{d['run']}").json()
    assert res["summary"]["hospitals"] == 2 and len(res["cells"]["features"]) > 0
    rt = client.get("/api/route", params={"rank": 1, "source": "nearest:fire_station"})
    assert rt.status_code == 200 and rt.json()["geometry"]["type"] == "LineString"
    assert client.get("/api/route", params={"rank": 99}).status_code == 404
    dp = client.post("/api/dispatch", json={"fire_station": 2})
    assert dp.status_code == 200 and 1 <= len(dp.json()["assignments"]) <= 2
    assert client.post("/api/dispatch", json={"fire_station": 99}).status_code == 422
    # a page that is behind the server (older run number) is told so, not given a stale answer
    assert client.get("/api/route", params={"rank": 1, "run": d["run"] + 7}).status_code == 409
    assert client.post("/api/dispatch", json={"fire_station": 1, "run": d["run"] + 7}).status_code == 409
    assert client.get("/api/route", params={"rank": 1, "run": d["run"]}).status_code == 200
    # the server restarts (uvicorn --reload on every saved .py file): the page still shows Route buttons. With the facilities and roads
    # on disk the layer comes back by itself; the simulation is gone, so the answer is a 409 that says to run again, not "prepare first".
    st.layer = None
    monkeypatch.setattr(RL, "cache_ready", lambda *a, **k: True)
    st.active_id = "toy"
    r = client.get("/api/route", params={"rank": 1, "run": d["run"]})
    assert r.status_code == 200 and st.layer is not None                           # rebuilt, run still on the map
    st.layer = None; st.model.last = None
    r = client.get("/api/route", params={"rank": 1, "run": d["run"]})
    assert r.status_code == 409 and "Run the simulation again" in r.json()["detail"]
    r = client.post("/api/dispatch", json={"fire_station": 1, "run": d["run"]})
    assert r.status_code == 409 and "Run the simulation again" in r.json()["detail"]
    monkeypatch.setattr(RL, "cache_ready", lambda *a, **k: False)
    st.layer = None
    r = client.get("/api/route", params={"rank": 1})
    assert r.status_code == 409 and "Prepare" in r.json()["detail"]                # never prepared: still asks for it

def test_report_endpoints(client, monkeypatch):
    import base64, report
    monkeypatch.setattr(report, "reverse_name", lambda lat, lon: "")      # no network in tests
    d = client.post("/api/simulate", json={"lat": 28.575, "lon": 77.33, "depth_km": 10, "magnitude": 7.0, "uncertainty": False}).json()
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    assert client.post(f"/api/report/{d['run']}/image", json={"png": base64.b64encode(png).decode()}).status_code == 200
    assert client.post(f"/api/report/{d['run']}/image", json={"png": base64.b64encode(b"notpng").decode()}).status_code == 400
    r = client.get(f"/api/report/{d['run']}.pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and "attachment" in r.headers["content-disposition"]
    assert client.get(f"/api/report/{d['run']}.csv").text.startswith("section,rank")
    assert client.get(f"/api/report/{d['run'] + 5}.pdf").status_code == 409

def test_confidence_panel_reads_live_status(client):
    items = {i["topic"]: i for i in client.get("/api/confidence").json()["items"]}
    assert items["Building strength (fragility curves)"]["level"] == "poor" and "ncalibrated" in items["Building strength (fragility curves)"]["text"]
    assert items["Casualty rates"]["level"] == "caution" and "Past-earthquake replays" in items
    assert items["Past-earthquake replays"]["level"] in ("poor", "caution")

def test_priority_cells_report_data_completeness(client):
    d = client.post("/api/simulate", json={"lat": 28.575, "lon": 77.33, "magnitude": 6.5, "uncertainty": False}).json()
    ranked = [f for f in d["cells"]["features"] if f["properties"]["rank"]]
    assert ranked and all(0 <= f["properties"]["measured"] <= 1 for f in ranked)

# ---------------- other hazards ----------------
def test_hazard_list_and_cyclone_endpoint(client):
    hz = client.get("/api/hazards").json()
    assert {h["name"] for h in hz["hazards"]} == {"earthquake", "flood", "cyclone"}
    r = client.post("/api/hazard/cyclone", json={"lat": 28.575, "lon": 77.33, "heading_deg": 45, "dp_hpa": 90, "rmax_km": 20})
    assert r.status_code == 200 and r.json()["summary"]["hazard"] == "cyclone" and "uncertainty_job" not in r.json()
    assert client.post("/api/hazard/cyclone", json={"lat": 28.5, "lon": 77.3, "heading_deg": 400}).status_code == 422

def test_flood_upload_run_and_validation(client, tmp_path):
    import numpy as np, rasterio
    from rasterio.transform import from_origin
    p = tmp_path / "d.tif"
    with rasterio.open(p, "w", driver="GTiff", height=60, width=60, count=1, dtype="float32", crs="EPSG:4326", transform=from_origin(77.30, 28.60, 0.001, 0.001)) as ds:
        ds.write(np.full((60, 60), 1.8, "float32"), 1)
    assert client.post("/api/hazard/flood", json={"raster": "nope"}).status_code == 404
    assert client.post("/api/flood/upload?name=bad name", content=p.read_bytes()).status_code == 400
    assert client.post("/api/flood/upload?name=scen1", content=b"not a tiff").status_code == 400
    assert client.post("/api/flood/upload?name=scen1", content=p.read_bytes()).json() == {"raster": "scen1"}
    assert "scen1" in client.get("/api/hazards").json()["flood_rasters"]
    r = client.post("/api/hazard/flood", json={"raster": "scen1", "scale": 1.0})
    assert r.status_code == 200 and r.json()["summary"]["flooded_buildings"] > 0
    assert client.post("/api/hazard/flood", json={"raster": "../x"}).status_code == 404

# ---------------- vector tiles ----------------
@pytest.fixture()
def tiled_client(tmp_path, monkeypatch):
    import tiles
    if tiles.tippecanoe_path() is None:
        pytest.skip("tippecanoe not installed")
    monkeypatch.setenv("VS30_TIF", str(tmp_path / "none.tif"))
    import app as appmod
    from simulate import Model
    c = TestClient(appmod.create_app(tmp_path))
    st = c.app.state.st
    st.model, st.active_id = Model(synthetic_exposure(500), data_dir=tmp_path), "synth"
    st.ensure_tiles("synth", st.model)
    for _ in range(200):
        if st.tile_state["synth"]["state"] != "building":
            break
        time.sleep(0.1)
    return c

def test_tiles_are_built_served_and_carry_ids(tiled_client):
    import gzip, mapbox_vector_tile as mvt, math
    s = tiled_client.get("/api/tiles/status").json()
    assert s["state"] == "ready" and s["layer"] == "bld" and s["buildings"] == 500 and "{z}" in s["url"]
    lat, lon, z = 28.575, 77.33, 14
    x, y = int((lon + 180) / 360 * 2 ** z), int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * 2 ** z)
    r = tiled_client.get(s["url"].replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y)))
    assert r.status_code == 200 and r.headers["content-encoding"] == "gzip" and "immutable" in r.headers["cache-control"]
    feats = mvt.decode(gzip.decompress(r.content) if r.content[:2] == b"\x1f\x8b" else r.content)["bld"]["features"]
    assert len(feats) > 0 and all(isinstance(f["id"], int) and "levels" in f["properties"] for f in feats)
    assert tiled_client.get("/tiles/synth/14/0/0.pbf").status_code == 204
    assert tiled_client.get("/tiles/nope/14/0/0.pbf").status_code == 404

def test_run_attrs_match_the_run_table(tiled_client):
    d = tiled_client.post("/api/simulate", json={"lat": 28.575, "lon": 77.33, "depth_km": 10, "magnitude": 7.0, "uncertainty": False}).json()
    a = tiled_client.get(f"/api/runattrs/{d['run']}").json()
    m = tiled_client.app.state.st.model
    assert a["n"] == 500 and len(a["cat"]) == 500 and len(a["pc"]) == 1500
    assert [int(c) for c in a["cat"]] == m.last["cat"].tolist()
    assert [int(a["pc"][3 * i:3 * i + 3]) / 100 for i in range(500)] == pytest.approx(m.last["p_collapse"].round(2).tolist(), abs=0.0051)
    assert tiled_client.get(f"/api/runattrs/{d['run'] + 1}").status_code == 409

def test_tiles_rebuild_when_the_area_changes(tiled_client):
    st = tiled_client.app.state.st
    import tiles
    old = st.tile_state["synth"]["version"]
    from simulate import Model
    st.model = Model(synthetic_exposure(520), data_dir=st.data)
    st.ensure_tiles("synth", st.model)
    for _ in range(200):
        if st.tile_state["synth"]["state"] != "building":
            break
        time.sleep(0.1)
    assert st.tile_state["synth"]["state"] == "ready" and st.tile_state["synth"]["version"] != old and tiles.meta(tiles.tile_path(st.data, "synth"))["buildings"] == 520
