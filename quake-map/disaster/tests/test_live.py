"""Live demo layer (live.py): SOS priority, safe-place routing on a toy city, and the phone <-> dashboard flow over WebSockets."""
import time
import numpy as np, pandas as pd, pytest
from fastapi.testclient import TestClient
from synthetic import synthetic_exposure
import live
from test_responder_layer import layer, shapely_t  # noqa: F401  (pytest fixture reused)
import responder_layer as RL

def test_priority_ranks_trapped_and_damaged_buildings_first():
    s_help, l_help = live.priority(False, False, 1)
    s_inj, l_inj = live.priority(False, True, 1)
    s_trap, l_trap = live.priority(True, False, 1)
    s_bad, l_bad = live.priority(True, True, 3, p_collapse=0.3, p_extensive=0.6)
    assert s_help < s_inj < s_trap < s_bad
    assert l_help == "low" and l_trap == "high" and l_bad == "critical"
    assert live.priority(True, False, 1, 0, 0)[1] in ("high", "critical")      # trapped never ranks below high

def test_gmaps_link_is_a_walking_directions_url():
    u = live.gmaps_link(28.5, 77.4, 28.49, 77.39)
    assert u.startswith("https://www.google.com/maps/dir/?api=1&") and "travelmode=walking" in u and "destination=28.500000%2C77.400000" in u

def _with_open_space(L, lon, lat, name="Park"):
    row = pd.DataFrame({"id": [99], "kind": ["open_space"], "name": [name], "lon": [lon], "lat": [lat], "source": "osm", "beds": np.nan, "building_id": -1})
    return RL.Layer(L.model, L.net, pd.concat([L.fac, row], ignore_index=True))

def test_safe_route_walks_to_the_nearest_open_space(layer):
    L, m, (lon0, lat0) = layer
    L2 = _with_open_space(L, lon0 + 0.0021, lat0 + 0.0001, "Near park")
    L2 = _with_open_space(L2, lon0 + 0.012, lat0 + 0.0108, "Far park")
    gj = live.safe_route(L2, lat0 + 0.0002, lon0 + 0.0002, "earthquake")
    p = gj["properties"]
    assert gj["geometry"]["type"] == "LineString" and len(gj["geometry"]["coordinates"]) >= 3
    assert p["name"] == "Near park" and p["kind"] == "open_space" and 100 < p["distance_m"] < 700 and p["walk_min"] >= 1
    assert p["p_open"] == 1.0 and p["risky_streets"] == 0                    # no run yet: nothing is blocked
    assert gj["geometry"]["coordinates"][-1] == [round(lon0 + 0.0021, 6), round(lat0 + 0.0001, 6)]

def test_safe_route_without_safe_places_says_so(layer):
    L, m, (lon0, lat0) = layer
    with pytest.raises(ValueError, match="No safe places"):
        live.safe_route(L, lat0, lon0, "earthquake")

def test_safe_route_and_team_route_after_a_quake(layer):
    L, m, (lon0, lat0) = layer
    L2 = _with_open_space(L, lon0 + 0.0021, lat0 + 0.0001)
    m.run(lat0 + 0.004, lon0 + 0.004, 8, 7.6)
    gj = live.safe_route(L2, lat0 + 0.0002, lon0 + 0.0002, "earthquake")
    assert 0 <= gj["properties"]["p_open"] <= 1
    tr = live.team_route(L2, "fire_station", lat0 + 0.005, lon0 + 0.005)
    assert tr["properties"]["facility"] == "F1" and tr["properties"]["expected_time_min"] > 0
    no_police = RL.Layer(m, L.net, L.fac[L.fac.kind != "police"])
    with pytest.raises(ValueError, match="No working police"):
        live.team_route(no_police, "police", lat0, lon0)

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("VS30_TIF", str(tmp_path / "none.tif"))
    import app as appmod
    c = TestClient(appmod.create_app(tmp_path))
    from simulate import Model
    c.app.state.st.model = Model(synthetic_exposure(500), data_dir=tmp_path)
    return c

def test_phone_alert_sos_assign_resolve_flow(client):
    m = client.app.state.st.model
    lat, lon = float(m.lat[0]), float(m.lon[0])
    assert client.get("/sos").status_code == 200
    with client.websocket_connect("/ws/live?role=dashboard") as dash, client.websocket_connect("/ws/live?role=phone&device=phone1234") as ph:
        snap = dash.receive_json()
        assert snap["type"] == "snapshot"
        hello = ph.receive_json()
        assert hello["type"] == "hello" and hello["sos"] is None
        assert dash.receive_json()["phone"]["online"] is True
        ph.send_json({"type": "loc", "lat": lat, "lon": lon, "acc": 12})
        assert dash.receive_json()["phone"]["lat"] == lat

        far = client.post("/api/live/alert", json={"lat": lat + 1, "lon": lon, "radius_km": 5, "message": "far away"}).json()
        assert far["inside"] == 0 and far["reached"] == 0
        assert dash.receive_json()["type"] == "alert"
        near = client.post("/api/live/alert", json={"lat": lat, "lon": lon, "radius_km": 2, "message": "Shaking expected"}).json()
        assert near["reached"] == 1
        got = ph.receive_json()
        assert got["type"] == "alert" and got["alert"]["message"] == "Shaking expected" and got["alert"]["distance_km"] == 0
        assert dash.receive_json()["type"] == "alert"

        s = client.post("/api/live/sos", json={"device": "phone1234", "lat": lat, "lon": lon, "trapped": True, "people": 3}).json()
        assert s["status"] == "new" and s["level"] in ("high", "critical") and s["building"] is not None
        assert dash.receive_json()["sos"]["id"] == s["id"]
        assert ph.receive_json()["sos"]["id"] == s["id"]
        dash.receive_json()                                                        # phone update (no longer safe)
        again = client.post("/api/live/sos", json={"device": "phone1234", "lat": lat, "lon": lon, "injured": True}).json()
        assert again["id"] == s["id"]                                              # one open SOS per phone: updated, not duplicated

        assert client.post(f"/api/live/sos/{s['id']}/cancel", json={"device": "someoneelse"}).status_code == 403
        a = client.post(f"/api/live/sos/{s['id']}/assign", json={"kind": "ambulance"}).json()
        assert a["status"] == "assigned" and a["team"] == "Ambulance" and a["route"] is None and "not loaded" in a["route_note"]
        r = client.post(f"/api/live/sos/{s['id']}/resolve").json()
        assert r["status"] == "resolved"
        assert client.post(f"/api/live/sos/{s['id']}/assign", json={"kind": "police"}).status_code == 409

    st = client.get("/api/live/state").json()
    assert st["phones"][0]["online"] is False and len(st["alerts"]) == 2

def test_late_phone_gets_a_recent_alert_for_its_spot(client):
    m = client.app.state.st.model
    lat, lon = float(m.lat[0]), float(m.lon[0])
    client.post("/api/live/alert", json={"lat": lat, "lon": lon, "radius_km": 3, "message": "Go to open ground"})
    with client.websocket_connect("/ws/live?role=phone&device=latecomer") as ph:
        assert ph.receive_json()["type"] == "hello"                                # position not known yet: no alert
        ph.send_json({"type": "loc", "lat": lat, "lon": lon})
        assert ph.receive_json()["alert"]["message"] == "Go to open ground"
    hub = client.app.state.live
    sent = []
    real = hub.to_phone
    async def spy(device, msg):
        if msg.get("type") == "alert":
            sent.append(device)
        return await real(device, msg)
    hub.to_phone = spy
    for _ in range(2):                                                             # reconnect / reload: the alarm must not sound again
        with client.websocket_connect("/ws/live?role=phone&device=latecomer") as ph:
            assert ph.receive_json()["type"] == "hello"
            ph.send_json({"type": "loc", "lat": lat, "lon": lon})
            ph.send_json({"type": "safe", "safe": True})
            for _ in range(200):
                if hub.phones["latecomer"]["safe"]:
                    break
                time.sleep(0.01)
    assert sent == []

def test_open_sos_follows_the_phone(client):
    m = client.app.state.st.model
    lat, lon = float(m.lat[0]), float(m.lon[0])
    with client.websocket_connect("/ws/live?role=phone&device=walker01") as ph:
        ph.receive_json()
        s = client.post("/api/live/sos", json={"device": "walker01", "lat": lat, "lon": lon}).json()
        assert ph.receive_json()["sos"]["id"] == s["id"]
        ph.send_json({"type": "loc", "lat": lat + 0.0001, "lon": lon})              # ~11 m: too small to move the call
        ph.send_json({"type": "loc", "lat": lat + 0.001, "lon": lon})               # ~110 m: the call follows
        moved = ph.receive_json()["sos"]
        assert moved["id"] == s["id"] and moved["lat"] == pytest.approx(lat + 0.001)
    assert client.get("/api/live/state").json()["sos"][0]["lat"] == pytest.approx(lat + 0.001)

def test_bad_message_closes_only_that_phone(client):
    with client.websocket_connect("/ws/live?role=phone&device=garbage1") as ph:
        ph.receive_json()
        ph.send_text("not json")
    assert client.get("/api/live/state").json()["phones"][0]["online"] is False

def test_demo_tools_and_validation(client):
    assert client.get("/api/live/safe-route", params={"lat": 28.5, "lon": 77.4}).status_code == 409   # no responder layer
    assert client.post("/api/live/simulate", json={"n": 15}).json()["added"] == 15
    st = client.get("/api/live/state").json()
    assert len(st["sos"]) == 15 and all(s["sim"] for s in st["sos"])
    spot = client.get("/api/live/demo-spot").json()
    w, s, e, n = client.app.state.st.model.bounds
    assert w - 0.01 <= spot["lon"] <= e + 0.01 and s - 0.01 <= spot["lat"] <= n + 0.01
    client.post("/api/live/sos", json={"device": "realphone", "lat": spot["lat"], "lon": spot["lon"]})
    client.post("/api/live/clear", params={"sim_only": True})
    assert [s["device"] for s in client.get("/api/live/state").json()["sos"]] == ["realphone"]
    assert client.post("/api/live/sos", json={"device": "bad device!", "lat": 0, "lon": 0}).status_code == 422
    assert client.post("/api/live/alert", json={"lat": 0, "lon": 0, "message": ""}).status_code == 422
    with client.websocket_connect("/ws/live?role=phone&device=x") as ws:           # too short: refused
        with pytest.raises(Exception):
            ws.receive_json()

def test_team_options_rank_stations_and_assign_uses_the_one_picked(layer):
    L, m, (lon0, lat0) = layer
    m.run(lat0 + 0.004, lon0 + 0.004, 8, 7.6)
    opts = live.team_options(L, lat0 + 0.0005, lon0 + 0.0005)
    hosp = [o for o in opts if o["kind"] == "ambulance"]
    assert [o["name"] for o in hosp] == ["H1", "H2"] and hosp[0]["eta_min"] < hosp[1]["eta_min"]   # H1 sits at the caller's corner
    assert {o["kind"] for o in opts} == {"fire_station", "ambulance", "police"}
    far = live.team_route(L, "ambulance", lat0 + 0.0005, lon0 + 0.0005, facility_id=hosp[1]["facility_id"])
    assert far["properties"]["facility"] == "H2" and far["properties"]["facility_id"] == hosp[1]["facility_id"]
    with pytest.raises(ValueError):
        live.team_route(L, "ambulance", lat0, lon0, facility_id=12345)

def test_options_endpoint_explains_when_streets_are_missing(client):
    m = client.app.state.st.model
    s = client.post("/api/live/sos", json={"device": "phone9999", "lat": float(m.lat[0]), "lon": float(m.lon[0])}).json()
    r = client.get(f"/api/live/sos/{s['id']}/options").json()
    assert r["options"] == [] and "streets" in r["note"].lower()
    assert client.get("/api/live/sos/999/options").status_code == 404
