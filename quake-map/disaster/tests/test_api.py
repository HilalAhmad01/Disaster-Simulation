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
