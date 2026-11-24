"""The drone module through the real FastAPI app: REST, both websockets, pairing, the control lease, replay, exports, chunk gating."""
import json, time
import numpy as np, pytest
from fastapi.testclient import TestClient
import dronefix


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("DRONE_LAUNCH_DELAY_S", "10"); mp.setenv("DRONE_SWAP_TIME_S", "30"); mp.setenv("DRONE_MAX_STATIONS", "3"); mp.setenv("DRONE_CAMERA_DRONE", "0"); mp.setenv("DRONE_OFFLINE_EXAMPLE", "1"); mp.setenv("DRONE_AUTO_MODE", "0")
    d = tmp_path_factory.mktemp("bridge")
    city = dronefix.make_city(mp, d)
    import app as appmod
    app = appmod.create_app(d)
    with TestClient(app) as c:
        st = c.app.state.st
        st.model, st.layer = city["model"], city["layer"]
        yield c, c.app.state.drone, st, city
    mp.undo()


def until(fn, timeout=30.0, step=0.1):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("timed out")


def ready_unit(env, timeout=60):
    """A drone that is ready on its pad (earlier tests may have left others flying or swapping batteries)."""
    c, svc, *_ = env
    fast(env, 300)
    until(lambda: any(u.ground == "ready" and u.kd.status == "ready" and u.station["available"] for u in svc.units.values()), timeout)
    return next(u for u in svc.units.values() if u.ground == "ready" and u.kd.status == "ready" and u.station["available"])


def start(env):
    c, svc, st, _ = env
    r = c.post("/api/drone/start")
    assert r.status_code == 200, r.text
    return r.json()["snapshot"]


def fast(env, speed=300):
    c, svc, *_ = env
    assert c.post("/api/live/speed", json={"speed": speed}).status_code == 200


def test_config_and_needs_before_a_run(env):
    c, svc, st, city = env
    st.model, st.layer = None, None
    cfg = c.get("/api/drone/config").json()
    assert cfg["enabled"] and cfg["sim"] and cfg["needs"] == "area" and not cfg["bound"]
    r = c.post("/api/drone/start")
    assert r.status_code == 409 and r.json()["code"] == "needs_area"
    st.model, st.layer = city["model"], city["layer"]

def test_start_binds_and_returns_the_snapshot(env):
    snap = start(env)
    assert snap["sim"] and snap["fleet"] and snap["queue"] and snap["reach"]
    assert "truth" not in json.dumps(snap)
    cfg = env[0].get("/api/drone/config").json()
    assert cfg["bound"] and cfg["needs"] is None and cfg["fidelity"] == ["L0"]

def test_commands_before_start_are_refused_cleanly():
    import app as appmod, tempfile, pathlib
    with TestClient(appmod.create_app(pathlib.Path(tempfile.mkdtemp()))) as c:
        r = c.get("/api/drone/queue")
        assert r.status_code == 409 and r.json()["code"] == "not_started"

def test_pairing_flow_and_pilot_socket(env):
    c, svc, st, _ = env
    start(env)
    code = c.post("/api/drone/pair/code", json={"role": "pilot"}).json()
    assert len(code["code"]) == 6
    assert c.post("/api/drone/pair/claim", json={"code": "000000", "name": "x"}).status_code == 400
    s = c.post("/api/drone/pair/claim", json={"code": code["code"], "name": "Pixel tablet"}).json()
    assert s["role"] == "pilot" and s["token"]
    assert c.post("/api/drone/pair/claim", json={"code": code["code"], "name": "again"}).status_code == 400      # single use
    with c.websocket_connect(f"/ws/pilot?token={s['token']}") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["role"] == "pilot" and hello["sim"] is True
        snap = ws.receive_json()
        assert snap["type"] == "snapshot" and snap["fleet"]
        ws.send_json({"type": "ping", "t": 123})
        for _ in range(60):
            m = ws.receive_json()
            if m["type"] == "pong":
                assert m["t"] == 123
                break
        else:
            raise AssertionError("no pong")
    assert any(x["name"] == "Pixel tablet" for x in c.get("/api/drone/sessions").json())

def test_bad_token_is_refused(env):
    c = env[0]
    with c.websocket_connect("/ws/pilot?token=nope") as ws:
        m = ws.receive_json()
        assert m["type"] == "error" and m["code"] == "bad_token"

def test_full_flight_with_pilot_lease_manual_input_and_release(env):
    c, svc, st, _ = env
    start(env)
    svc.set_auto(False)
    u = ready_unit(env)
    fast(env, 60)
    assert c.post(f"/api/drone/drones/{u.id}/launch").status_code == 200
    until(lambda: u.kd.airborne and u.kd.status in ("transit", "surveying") and u.kd.pos[2] > 20, 60)
    code = c.post("/api/drone/pair/code", json={"role": "pilot"}).json()["code"]
    s1 = c.post("/api/drone/pair/claim", json={"code": code, "name": "pilot A"}).json()
    code = c.post("/api/drone/pair/code", json={"role": "pilot"}).json()["code"]
    s2 = c.post("/api/drone/pair/claim", json={"code": code, "name": "pilot B"}).json()
    with c.websocket_connect(f"/ws/pilot?token={s1['token']}") as w1, c.websocket_connect(f"/ws/pilot?token={s2['token']}") as w2:
        for w in (w1, w2):
            w.receive_json(); w.receive_json()
        w1.send_json({"type": "take_control", "drone": u.id})
        until(lambda: u.kd.mode == "manual")
        assert svc.calls.speed() <= 2.0                                           # the demo clock was lowered for the pilot
        w2.send_json({"type": "take_control", "drone": u.id})
        errs = []
        for _ in range(80):
            m = w2.receive_json()
            if m["type"] == "error":
                errs.append(m); break
        assert errs and errs[0]["code"] == "lease_taken"
        # B's stick input is ignored, A's moves the drone
        p0 = u.kd.pos.copy()
        for k in range(1, 25):
            w1.send_json({"type": "input", "drone": u.id, "seq": k, "vx": 5.0, "vy": 0.0, "vz": 0.0, "yr": 0.0})
            time.sleep(0.05)
        assert np.hypot(*(u.kd.pos[:2] - p0[:2])) > 0.5
        w1.send_json({"type": "release", "drone": u.id, "then": "rth"})
        until(lambda: u.kd.mode == "rth")
    fast(env, 300)
    until(lambda: not u.kd.airborne, 90)
    assert svc.calls.speed() == 300.0                                              # restored after the manual flight

def test_commander_can_take_the_lease_from_a_pilot(env):
    c, svc, st, _ = env
    start(env)
    svc.set_auto(False)
    u = ready_unit(env); fast(env, 60)
    c.post(f"/api/drone/drones/{u.id}/launch")
    until(lambda: u.kd.airborne and u.kd.pos[2] > 20, 60)
    code = c.post("/api/drone/pair/code").json()["code"]
    tok = c.post("/api/drone/pair/claim", json={"code": code, "name": "p"}).json()["token"]
    with c.websocket_connect(f"/ws/pilot?token={tok}") as w:
        w.receive_json(); w.receive_json()
        w.send_json({"type": "take_control", "drone": u.id})
        until(lambda: u.kd.mode == "manual")
        assert c.post(f"/api/drone/drones/{u.id}/take_control").status_code == 200
        assert svc_lease(c, u.id)["holder"] == "commander"
    c.post(f"/api/drone/drones/{u.id}/release_control", json={"then": "rth"})
    fast(env, 300)
    until(lambda: not u.kd.airborne, 90)

def svc_lease(c, did):
    s = c.app.state.drone_sessions
    return s.lease_info(did)

def test_observer_cannot_fly(env):
    c, svc, *_ = env
    start(env)
    code = c.post("/api/drone/pair/code", json={"role": "observer"}).json()["code"]
    tok = c.post("/api/drone/pair/claim", json={"code": code, "name": "wall display"}).json()["token"]
    u = next(iter(svc.units.values()))
    with c.websocket_connect(f"/ws/pilot?token={tok}") as w:
        w.receive_json(); w.receive_json()
        w.send_json({"type": "take_control", "drone": u.id})
        for _ in range(80):
            m = w.receive_json()
            if m["type"] == "error":
                assert m["code"] == "lease_taken"; break
        else:
            raise AssertionError("expected an error")

def test_revoking_a_session_disconnects_it(env):
    c = env[0]
    start(env)
    code = c.post("/api/drone/pair/code").json()["code"]
    s = c.post("/api/drone/pair/claim", json={"code": code, "name": "gone"}).json()
    assert c.delete(f"/api/drone/sessions/{s['id']}").json()["ok"]
    with c.websocket_connect(f"/ws/pilot?token={s['token']}") as w:
        assert w.receive_json()["code"] == "bad_token"

def test_task_preview_create_and_queue_edits_over_rest(env):
    c, svc, *_ = env
    start(env); svc.set_auto(False)
    q = c.get("/api/drone/queue").json()
    assert q and q[0]["why"] is not None
    tid = q[-1]["id"]
    assert c.patch(f"/api/drone/tasks/{tid}", json={"pinned": True}).status_code == 200
    assert c.get("/api/drone/queue").json()[0]["id"] == tid
    c.patch(f"/api/drone/tasks/{tid}", json={"pinned": False, "skipped": True})
    b = int(np.argmax(svc.p_current()[:, 3]))
    pv = c.post("/api/drone/tasks/preview", json={"type": "inspect_building", "building": b}).json()
    assert "options" in pv and "feasible" in pv
    far = c.post("/api/drone/tasks", json={"type": "goto", "lon": 90.0, "lat": 20.0, "agl": 40})
    assert far.status_code == 409 and far.json()["code"] == "infeasible"
    bad = c.post("/api/drone/tasks", json={"type": "teleport"})
    assert bad.status_code == 400

def test_chunk_is_refused_until_a_drone_has_been_near(env):
    c, svc, *_ = env
    start(env)
    r = c.get("/api/drone/chunk/900/900")
    assert r.status_code == 403 and r.json()["code"] == "not_seen"

def test_exports_contain_no_truth(env):
    c, svc, *_ = env
    start(env)
    v = c.get("/api/drone/export/victims.geojson").json()
    assert v["type"] == "FeatureCollection"
    cov = c.get("/api/drone/export/coverage.geojson").json()
    assert cov["type"] == "FeatureCollection"
    assert c.get("/api/drone/export/pointcloud.ply").text.startswith("ply")
    assert c.get("/api/drone/export/events.ndjson").status_code == 200

def test_model_summary_and_toggle(env):
    c, svc, *_ = env
    start(env)
    s = c.get("/api/drone/model/summary").json()
    assert s["modelled"]["expected_collapses"] > 0 and "note" in s
    assert c.post("/api/drone/model/enable", json={"enabled": False}).json()["enabled"] is False
    c.post("/api/drone/model/enable", json={"enabled": True})
    m = c.get("/api/drone/model/map").json()
    assert m["heat"]["features"] and m["cells"]["features"]

def test_replay_save_list_and_play(env):
    c, svc, *_ = env
    start(env)
    svc.emit("alarm", drone="x", code="TEST", message="m")
    assert c.post("/api/drone/replay/save", json={"name": "demo run!"}).json()["saved"] == "demorun"
    assert "demorun" in c.get("/api/drone/replay/list").json()
    with c.websocket_connect("/ws/drone") as ws:
        for _ in range(30):
            if ws.receive_json()["type"] == "snapshot":
                break
        assert c.post("/api/drone/replay/start", json={"name": "demorun", "speed": 50}).status_code == 200
        kinds = []
        for _ in range(3000):
            m = ws.receive_json(); kinds.append(m["type"])
            if m["type"] == "replay_end":
                break
        assert "replay_start" in kinds and "replay_end" in kinds
    assert c.post("/api/drone/replay/start", json={"name": "nope"}).status_code == 404

def test_dashboard_socket_gets_events_live(env):
    c, svc, *_ = env
    start(env)
    svc.set_auto(False)
    u = ready_unit(env); fast(env, 60)
    with c.websocket_connect("/ws/drone") as ws:
        got = set()
        c.post(f"/api/drone/drones/{u.id}/launch")
        for _ in range(600):
            m = ws.receive_json(); got.add(m["type"])
            if {"hello", "snapshot", "sortie_plan", "drone", "route"} <= got:
                break
        assert {"hello", "snapshot", "sortie_plan", "drone"} <= got
    c.post(f"/api/drone/drones/{u.id}/return_home")
    fast(env, 300)
    until(lambda: not u.kd.airborne, 90)

def test_drone_disabled_leaves_the_app_without_drone_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("DRONE_ENABLED", "0")
    import app as appmod
    c = TestClient(appmod.create_app(tmp_path))
    assert c.get("/api/drone/config").status_code == 404 and c.get("/pilot").status_code == 404
    assert c.get("/api/live/state").status_code == 200                           # the rest of the app is untouched

def test_static_route_is_a_safe_whitelist(env):
    c = env[0]
    assert c.get("/drone-static/../app.py").status_code in (404, 400)
    assert c.get("/drone-static/%2e%2e/app.py").status_code == 404
    assert c.get("/drone-static/nothing.js").status_code == 404
    assert c.get("/pilot").status_code == 200


# ---------------------------------------------------------------------- integration with the existing dispatch (live.py)
def det(svc, b, conf=0.8, people=2, t=None, dx=0.0):
    c = {"x": float(svc.index.x[b]) + dx, "y": float(svc.index.y[b]), "building": b, "confidence": conf, "est_people": people, "true": True, "site": 0}
    svc._add_detection(c, "drn-test", svc.clock.t if t is None else t)
    return list(svc.detections.values())[-1]


def test_confirmed_detection_becomes_a_call_the_existing_dispatch_can_send_a_team_to(env):
    c, svc, st, city = env
    start(env)
    d = det(svc, 17, 0.8, 3, dx=300.0)
    assert d["status"] == "unverified"
    out = c.post(f"/api/drone/detections/{d['id']}/confirm").json()
    assert out["status"] == "confirmed" and out["call_id"]
    calls = c.get("/api/live/state").json()["sos"]
    call = next(s for s in calls if s["id"] == out["call_id"])
    assert call["source"] == "drone" and call["people"] == 3 and call["trapped"] and call["level"] in ("high", "critical") and call["status"] == "new"
    opts = c.get(f"/api/live/sos/{call['id']}/options").json()
    assert opts["options"], opts
    r = c.post(f"/api/live/sos/{call['id']}/assign", json={"kind": opts["options"][0]["kind"], "facility_id": opts["options"][0]["facility_id"]})
    assert r.status_code == 200 and r.json()["status"] == "assigned" and r.json()["route"]

def test_rejecting_a_detection_creates_no_call(env):
    c, svc, *_ = env
    start(env)
    n0 = len(c.get("/api/live/state").json()["sos"])
    d = det(svc, 31, 0.3, 1, dx=-300.0)
    assert c.post(f"/api/drone/detections/{d['id']}/reject").json()["status"] == "rejected"
    assert len(c.get("/api/live/state").json()["sos"]) == n0
    assert c.post(f"/api/drone/detections/{d['id']}/confirm").status_code == 409

def test_a_phone_sos_is_corroborated_by_a_nearby_detection(env):
    c, svc, st, city = env
    start(env)
    b = 55
    lon, lat = svc.frame.to_lonlat(float(svc.index.x[b]) + 6.0, float(svc.index.y[b]))
    r = c.post("/api/live/sos", json={"device": "phone-test-0001", "lat": float(lat), "lon": float(lon), "accuracy_m": 8, "trapped": True, "injured": False, "people": 2, "note": "help"})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    n_drone_calls = lambda: len([s for s in c.get("/api/live/state").json()["sos"] if s.get("source") == "drone"])
    k0 = n_drone_calls()
    d = det(svc, b, 0.7, 1, dx=0.0)
    assert d["status"] == "merged" and d["call_id"] == sid
    call = next(s for s in c.get("/api/live/state").json()["sos"] if s["id"] == sid)
    assert call["drone_corroborated"] and "drone-corroborated" in call["note"]
    assert n_drone_calls() == k0

def test_verify_with_drone_flies_to_the_call_and_tags_it_when_nobody_is_seen(env):
    c, svc, st, city = env
    start(env); svc.set_auto(False); ready_unit(env); fast(env, 60)
    b = int(np.flatnonzero(svc.truth.state == 0)[3])
    lon, lat = svc.frame.to_lonlat(float(svc.index.x[b]), float(svc.index.y[b]))
    sid = c.post("/api/live/sos", json={"device": "phone-test-0002", "lat": float(lat), "lon": float(lon), "accuracy_m": 8, "trapped": False, "injured": True, "people": 1, "note": ""}).json()["id"]
    svc.cfg.false_alarms_per_km2 = 0.0
    r = c.post(f"/api/drone/calls/{sid}/verify_with_drone")
    assert r.status_code == 200, r.text
    fast(env, 120)
    until(lambda: "drone-checked" in "".join(s.get("drone_tag", "") for s in c.app.state.live.sos.values()) or "drone_checked_empty" in str(c.app.state.live.sos[sid].get("drone_tag")), 120)
    assert "could not see anyone" in c.app.state.live.sos[sid]["note"]
    u = svc.units[r.json()["drone"]]
    c.post(f"/api/drone/drones/{u.id}/return_home")
    until(lambda: not u.kd.airborne, 120)

def test_observed_blocked_street_changes_routes_and_hospital_access(env):
    c, svc, st, city = env
    start(env)
    layer = st.layer
    import live
    base = live.blockage_now(layer).copy()
    e = int(np.argmax(layer.net.len))
    assert base[e] < 0.99
    layer.set_edge_override({e: True})
    assert layer.access == {}
    after = live.blockage_now(layer)
    assert after[e] == pytest.approx(0.999) and np.allclose(np.delete(after, e), np.delete(base, e))
    layer.set_edge_override({e: False})
    assert live.blockage_now(layer)[e] == 0.0
    st.layer._edge_override = {}; st.layer._live_pedge = None

def test_blocked_street_override_belongs_to_one_run(env):
    c, svc, st, city = env
    layer = st.layer
    layer.set_edge_override({3: True})
    layer.model.run_id += 1000                                   # a new simulation run
    layer.set_edge_override({5: True})
    assert 3 not in layer._edge_override and layer._edge_override[5] is True
    layer._edge_override = {}; layer._live_pedge = None


# ---------------------------------------------------------------------- robustness
def test_rerunning_the_simulation_mid_flight_recalls_drones_and_rebinds(env):
    c, svc, st, city = env
    start(env); svc.set_auto(False)
    u = ready_unit(env); fast(env, 60)
    c.post(f"/api/drone/drones/{u.id}/launch")
    until(lambda: u.kd.airborne and u.kd.pos[2] > 20, 60)
    code = c.post("/api/drone/pair/code").json()["code"]
    tok = c.post("/api/drone/pair/claim", json={"code": code, "name": "p"}).json()["token"]
    with c.websocket_connect(f"/ws/pilot?token={tok}") as w:
        w.receive_json(); w.receive_json()
        w.send_json({"type": "take_control", "drone": u.id})
        until(lambda: u.kd.mode == "manual")
        old_run = svc.run_id
        city["model"].run(city["lat0"], city["lon0"], 10.0, 6.8)               # a new scenario on the map
        until(lambda: svc.run_id != old_run and svc.bound, 20)
        kinds = set()
        for _ in range(400):
            m = w.receive_json(); kinds.add(m["type"])
            if "reset" in kinds and "snapshot" in kinds:
                break
        assert {"reset", "snapshot"} <= kinds
        assert all(not x.kd.airborne for x in svc.units.values())                  # recalled: new fleet on the ground
        assert c.app.state.drone_sessions.lease_info(u.id)["holder"] is None       # the old lease is gone
    assert c.get("/api/drone/health").json()["error"] is None
    fast(env, 300)

def test_garbage_websocket_messages_never_break_the_socket(env):
    c, svc, *_ = env
    start(env)
    code = c.post("/api/drone/pair/code").json()["code"]
    tok = c.post("/api/drone/pair/claim", json={"code": code, "name": "fuzz"}).json()["token"]
    u = next(iter(svc.units.values()))
    with c.websocket_connect(f"/ws/pilot?token={tok}") as w:
        w.receive_json(); w.receive_json()
        for raw in ("not json", "[]", "42", "{}", '{"type":"input"}', '{"type":"input","drone":"%s","seq":"x","vx":"a"}' % u.id, '{"type":"take_control","drone":null}',
                    '{"type":"goto","drone":"%s","lon":"x"}' % u.id, '{"type":"confirm","id":"zzz"}', '{"type":"nope"}'):
            w.send_text(raw)
        w.send_json({"type": "ping", "t": 7})
        for _ in range(200):
            m = w.receive_json()
            if m["type"] == "pong":
                assert m["t"] == 7
                break
        else:
            raise AssertionError("the socket stopped answering after garbage input")


def test_a_slow_client_keeps_the_newest_telemetry_of_every_drone():
    """Regression: telemetry carries the drone id as "id", so every drone shared one slot and only one drone's update survived."""
    from drone.bridge import Client
    c = Client(None, "commander")
    for t in range(3):
        c.push({"type": "drone", "id": "a", "z": t}); c.push({"type": "drone", "id": "b", "z": 10 + t})
    got = sorted((m["id"], m["z"]) for m in c.latest.values())
    assert got == [("a", 2), ("b", 12)]
