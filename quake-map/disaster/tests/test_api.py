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
    d = client.post("/api/simulate", json={"lat": 28.575, "lon": 77.33, "depth_km": 8, "magnitude": 7.4, "uncertainty": False}).json()
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
