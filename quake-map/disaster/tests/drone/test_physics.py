"""The Drone-SLAM physics drone (L1). Needs a Python with torch for the worker: set DRONE_WORKER_PYTHON (the tests skip otherwise)."""
import math, os
import numpy as np, pytest
from drone.config import Config
from drone import energy as E, planner as P
from drone.physics import PhysicsDrone, WorkerClient, WorkerError

PY = os.environ.get("DRONE_WORKER_PYTHON", "")
pytestmark = pytest.mark.skipif(not PY, reason="set DRONE_WORKER_PYTHON to a python with torch to run the physics tests")


@pytest.fixture(scope="module")
def worker():
    w = WorkerClient(PY)
    info = w.start()
    assert info["hello"] and info["device"] == "cpu"
    yield w
    w.close()


def task(cfg, x=700.0, y=300.0, tid="t1"):
    path = P.survey_path(cfg, (x, y, x + 250, y + 250))
    return {"id": tid, "type": "survey_block", "alt": 40.0, "wps": P.survey_wps(cfg, path, 40.0, tid), "entry": path[0], "exit": path[-1], "centre": (x + 125, y + 125), "value": 10.0}


def fly(d, max_s=3600, dt=0.5):
    ev, t = [], 0.0
    while d.airborne and t < max_s:
        ev += d.step(dt, t, t, None); t += dt
    return ev, t


def test_worker_steps_the_vendored_quadrotor(worker):
    r = worker.call({"op": "reset", "id": "a", "pos": [0, 0, 0], "yaw": 0.0, "seed": 1})
    assert r["pos"] == [0.0, 0.0, 0.0]
    for _ in range(10):
        r = worker.call({"op": "step", "id": "a", "goal": [0, 0, 30], "vmax": 4.0, "n": 25})
    assert r["pos"][2] > 20 and abs(r["tilt"]) < 45                                  # it climbs to the goal height

def test_worker_tracks_a_waypoint_at_city_speeds(worker):
    worker.call({"op": "reset", "id": "b", "pos": [0, 0, 30], "yaw": 0.0, "seed": 2})
    speeds = []
    for _ in range(40):
        r = worker.call({"op": "step", "id": "b", "goal": [r_x(worker) + 30 if False else 400, 0, 30], "vmax": 12.0, "n": 25})
        speeds.append(math.hypot(r["vel"][0], r["vel"][1]))
    assert max(speeds) > 8.0 and abs(r["pos"][1]) < 6.0                              # fast, and close to the straight line

def r_x(w): return 0

def test_unknown_op_and_unknown_drone_are_reported_not_fatal(worker):
    with pytest.raises(WorkerError):
        worker.call({"op": "step", "id": "nobody", "goal": [0, 0, 0], "vmax": 1, "n": 1})
    assert worker.call({"op": "reset", "id": "c", "pos": [0, 0, 5], "yaw": 0, "seed": 3})["pos"][2] == 5.0     # still alive

def test_physics_drone_flies_a_planned_sortie_and_lands_with_the_reserve(worker):
    cfg = Config()
    route = P.build_route(cfg, (0.0, 0.0), [task(cfg)], 60.0)
    planned, t_plan, _ = E.route_energy(cfg, route)
    d = PhysicsDrone(cfg, "drn-t", 1, (0.0, 0.0), 1, worker)
    d.launch(route, "s1")
    ev, t = fly(d, 4000)
    assert d.status == "landed" and d.phys_ok
    used = cfg.battery_wh - d.battery_wh
    assert 0.6 * planned < used < 1.6 * planned                                       # the flyer is slower and wobblier than the plan, but close
    assert d.battery_wh >= E.reserve_wh(cfg) * 0.99
    assert any(e["type"] == "task_done" for e in ev)

def test_physics_drone_obeys_manual_commands_and_hold(worker):
    cfg = Config()
    d = PhysicsDrone(cfg, "drn-m", 2, (0.0, 0.0), 2, worker)
    d.status, d.pos[:] = "transit", [0.0, 0.0, 40.0]; d.mode = "auto"
    d._reset_quad(); d.pos[:] = [0.0, 0.0, 0.0]
    d.pos[:] = [0.0, 0.0, 40.0]; d._sync_quad()
    d.take_control(); d.yaw = 0.0
    for k in range(60):
        d.manual_input(6.0, 0.0, 0.0, 0.0, wall_now=float(k)); d.step(0.5, k * 0.5, float(k))
    assert d.pos[0] > 40.0 and abs(d.pos[1]) < 10.0 and 25 < d.pos[2] < 55
    x = d.pos[0]
    for k in range(60, 80):
        d.manual_input(0.0, 0.0, 0.0, 0.0, wall_now=float(k)); d.step(0.5, k * 0.5, float(k))
    assert abs(d.pos[0] - x) < 25                                                     # it stops (with some coasting)

def test_losing_the_worker_falls_back_to_the_simple_model():
    w = WorkerClient(PY); w.start()
    cfg = Config()
    d = PhysicsDrone(cfg, "drn-f", 3, (0.0, 0.0), 3, w)
    d.launch(P.build_route(cfg, (0.0, 0.0), [task(cfg, 500, 0)], 60.0), "s")
    for k in range(80):
        d.step(0.5, k * 0.5, 0.0)
    w.proc.kill(); w.proc.wait()
    ev, _ = fly(d, 6000)
    assert not d.phys_ok and d.status == "landed"                                      # it still finished the flight


# ---------------------------------------------------------------------- through the whole app
def test_l1_mode_in_the_app_reports_fidelity_and_caps_the_demo_clock(tmp_path, monkeypatch):
    import time, dronefix
    from fastapi.testclient import TestClient
    monkeypatch.setenv("DRONE_WORKER_PYTHON", PY); monkeypatch.setenv("DRONE_AUTO_MODE", "0"); monkeypatch.setenv("DRONE_CAMERA_DRONE", "0"); monkeypatch.setenv("DRONE_LAUNCH_DELAY_S", "5"); monkeypatch.setenv("DRONE_MAX_STATIONS", "2")
    city = dronefix.make_city(monkeypatch, tmp_path)
    import app as appmod
    with TestClient(appmod.create_app(tmp_path)) as c:
        st = c.app.state.st
        st.model, st.layer = city["model"], city["layer"]
        assert c.get("/api/drone/config").json()["fidelity"] == ["L0", "L1"]
        snap = c.post("/api/drone/start").json()["snapshot"]
        assert snap["fidelity"] == ["L0", "L1"] and {d["fidelity"] for d in snap["fleet"]} == {"L1"}
        c.post("/api/live/speed", json={"speed": 30})
        svc = c.app.state.drone
        u = next(u for u in svc.units.values() if u.ground == "ready" and u.station["available"])
        assert c.post(f"/api/drone/drones/{u.id}/launch").status_code == 200
        t0 = time.time()
        while time.time() - t0 < 60 and not (u.kd.airborne and u.kd.pos[2] > 15):
            time.sleep(0.2)
        assert u.kd.airborne and u.kd.pos[2] > 15
        assert svc.calls.speed() <= svc.cfg.l1_clock_cap                           # the physics drone only keeps up to about this speed
        c.post(f"/api/drone/drones/{u.id}/return_home")
        t0 = time.time()
        while time.time() - t0 < 120 and u.kd.airborne:
            time.sleep(0.3)
        assert not u.kd.airborne
        t0 = time.time()
        while time.time() - t0 < 3 and svc.calls.speed() != 30.0:
            time.sleep(0.1)
        assert svc.calls.speed() == 30.0                                            # restored after the flight
