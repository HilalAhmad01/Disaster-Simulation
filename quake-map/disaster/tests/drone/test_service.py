"""The drone service end to end on the synthetic city, with a fake dispatch side and no web framework."""
import math
import numpy as np, pytest
from drone.config import Config
from drone.service import DroneService, FakeCalls, ServiceError
import dronefix, live


@pytest.fixture()
def svc(tmp_path, monkeypatch):
    c = dronefix.make_city(monkeypatch, tmp_path)
    cfg = Config(launch_delay_s=20.0, swap_time_s=60.0, max_stations=3, camera_drone=False, offline_example=True)
    s = DroneService(cfg, FakeCalls(speed=10.0))
    s.bind(c["model"], c["layer"], s.calls, live.blockage_now(c["layer"]))
    s.city = c
    return s


def run(s, sim_seconds, speed=None, step=0.1, until=None):
    """Advance the service by simulated seconds using wall steps of `step` (sim time = step x speed)."""
    if speed is not None:
        s.calls.set_speed(speed)
    wall = 1000.0
    t_end = s.clock.t + sim_seconds
    while s.clock.t < t_end:
        wall += step
        s.tick(step, wall)
        if until and until(s):
            break
    return s


def first_ready(s):
    return next(u for u in s.units.values() if u.ground == "ready" and u.station["available"])


def test_fleet_is_built_from_police_stations_with_one_offline_example(svc):
    assert 1 <= len(svc.units) <= 4
    assert all(s["drone_id"] in svc.units for s in svc.stations)
    ready = [u for u in svc.units.values() if u.status == "ready"]
    assert ready and all(u.station["spare"] == svc.cfg.spare_batteries for u in ready)
    # a station in a likely-damaged building shows an offline drone with a reason
    off = [u for u in svc.units.values() if u.status == "offline"]
    for u in off:
        assert u.station["reason"] and not u.station["available"]
    snap = svc.snapshot()
    assert snap["sim"] is True and len(snap["fleet"]) == len(svc.units) and snap["reach"]

def test_snapshot_never_contains_hidden_truth(svc):
    import json
    text = json.dumps(svc.snapshot())
    for word in ("truth", "s_found", "trapped", "exposed", "edge_blocked", "road_blocked"):
        assert word not in text
    run(svc, 120)
    q = json.dumps(svc.queue())
    assert "trapped" not in q and "true" not in json.dumps(list(svc.detections.values())[:0])

def test_queue_ranks_by_value_per_energy_with_reasons(svc):
    q = svc.queue()
    assert len(q) > 3
    assert all(q[i]["score"] >= q[i + 1]["score"] - 1e-9 or q[i]["manual_rank"] is not None for i in range(len(q) - 1))
    top = q[0]
    assert top["value"] > 0 and top["components"]["victims"] >= 0 and top["why"] and top["est_energy_wh"] > 0
    assert top["reachable_from"] or top["reason"] == "out_of_range"

def test_pin_skip_and_ban_change_the_queue(svc):
    q = svc.queue()
    last = q[-1]["id"]
    svc.patch_task(last, pinned=True)
    assert svc.queue()[0]["id"] == last and svc.queue()[0]["pinned"]
    svc.patch_task(last, skipped=True)
    assert [t for t in svc.queue() if t["id"] == last][0]["skipped"]
    first = [t for t in svc.queue() if not t["skipped"]][0]
    lon, lat = first["lon"], first["lat"]
    d = 0.003
    svc.ban_zone([[lon - d, lat - d], [lon + d, lat - d], [lon + d, lat + d], [lon - d, lat + d], [lon - d, lat - d]])
    assert first["id"] not in [t["id"] for t in svc.queue()]

def test_auto_mode_launches_flies_scans_lands_and_swaps_battery(svc):
    events = []
    svc.subscribe(lambda m: events.append(m["type"]))
    run(svc, 4000, speed=30, until=lambda s: any(u.kd.status == "landed" or u.ground == "swapping" for u in s.units.values()) and "scan" in events)
    flown = [u for u in svc.units.values() if u.kd.flight_s > 0 or u.plan or u.ground != "ready"]
    assert flown, "auto mode should have launched a drone"
    assert "sortie_plan" in events and "route" in events and "scan" in events
    assert svc.scan.counts()["scanned"] > 0 and svc.scan.conf.max() > 0.5
    # the drone lands with at least the reserve and swaps a battery, using up a spare
    done = lambda u: u.ground == "ready" and u.kd.status == "ready" and u.station["spare"] < svc.cfg.spare_batteries
    run(svc, 1500, speed=30, until=lambda s: any(done(u) for u in s.units.values()))
    u = next(u for u in svc.units.values() if done(u))
    assert u.kd.battery_wh == svc.cfg.battery_wh and u.station["charging"]

def test_planned_energy_and_simulated_energy_agree_within_5_percent(svc):
    svc.set_auto(False); svc.cfg.sos_bump = False
    u = first_ready(svc)
    plan = svc.launch_best(u.id)
    planned = plan["energy_wh"]
    run(svc, 6000, speed=60, until=lambda s: u.kd.status == "landed")
    assert u.kd.status == "landed" or u.ground in ("swapping", "ready")
    used = svc.cfg.battery_wh - (u.kd.battery_wh if u.kd.status == "landed" else svc.cfg.battery_wh - planned)
    assert used == pytest.approx(planned, rel=0.05)

def test_sortie_never_breaks_the_reserve(svc):
    svc.set_auto(True)
    low = {"v": 1e9}
    def watch(m):
        if m["type"] == "drone" and m["status"] not in ("ready", "offline", "swapping", "waiting_battery", "launching"):
            low["v"] = min(low["v"], m["battery_wh"])
    svc.subscribe(watch)
    run(svc, 5000, speed=60)
    assert low["v"] >= svc.cfg.reserve_fraction * svc.cfg.battery_wh * 0.95

def test_scanning_changes_building_state_monotonically_and_creates_observations(svc):
    obs = []
    svc.subscribe(lambda m: obs.append(m) if m["type"] == "observation" else None)
    before = svc.scan.conf.copy()
    run(svc, 3000, speed=60, until=lambda s: len(obs) >= 5)
    assert np.all(svc.scan.conf >= before - 1e-9)
    assert obs and {"building", "observed", "before", "after"} <= set(obs[0])
    assert svc.feedback.n_obs >= 1
    states = svc.scan.state
    assert set(np.unique(states)) <= {0, 1, 2}

def test_detections_merge_become_calls_and_confirm_or_reject(svc):
    # drive the detection logic directly with synthetic candidates
    s = svc
    t = s.clock.t
    x, y = float(s.index.x[0]), float(s.index.y[0])
    cand = {"x": x, "y": y, "building": 0, "confidence": 0.6, "est_people": 2, "true": True, "site": 0}
    s._add_detection(cand, "drn-x", t)
    s._add_detection({**cand, "x": x + 5, "confidence": 0.8, "est_people": 3}, "drn-x", t + 30)       # within 15 m: merges
    s._add_detection({**cand, "x": x + 400, "y": y + 400, "building": 5}, "drn-x", t + 60)           # far: a second detection
    ds = list(s.detections.values())
    assert len(ds) == 2 and ds[0]["sightings"] == 2 and ds[0]["confidence"] == 0.8 and ds[0]["est_people"] == 3 and ds[0]["status"] == "unverified"
    out = s.confirm_detection(ds[0]["id"])
    assert out["status"] == "confirmed" and s.calls.calls[out["call_id"]]["source"] == "drone"
    with pytest.raises(ServiceError):
        s.confirm_detection(ds[0]["id"])
    assert s.reject_detection(ds[1]["id"])["status"] == "rejected"
    # the same building always merges (a person does not move); outside a building the 10 minute window applies
    s._add_detection(cand, "drn-x", t + 5000)
    assert len(s.detections) == 2
    s._add_detection({**cand, "building": None}, "drn-x", t + 20000)
    assert len(s.detections) == 3

def test_a_phone_sos_nearby_is_corroborated_not_duplicated(svc):
    s = svc
    x, y = float(s.index.x[3]), float(s.index.y[3])
    lon, lat = s.frame.to_lonlat(x + 8, y)
    cid = s.calls.add_phone_call(float(lat), float(lon))
    s._add_detection({"x": x, "y": y, "building": 3, "confidence": 0.7, "est_people": 1, "true": True, "site": 1}, "drn-x", s.clock.t)
    d = list(s.detections.values())[0]
    assert d["status"] == "merged" and d["call_id"] == cid and s.calls.corroborated[cid] == d["id"]
    assert len([c for c in s.calls.calls.values() if c["source"] == "drone"]) == 0

def test_auto_confirm_threshold(svc):
    svc.cfg.auto_confirm_threshold = 0.85
    s = svc
    c = {"x": float(s.index.x[0]), "y": float(s.index.y[0]), "building": 0, "est_people": 1, "true": True, "site": 0}
    s._add_detection({**c, "confidence": 0.9}, "d", 0.0)
    s._add_detection({**c, "x": c["x"] + 500, "confidence": 0.5}, "d", 0.0)
    st = sorted(d["status"] for d in s.detections.values())
    assert st == ["confirmed", "unverified"]

def test_false_alarms_appear_at_roughly_the_configured_rate(svc):
    rng = np.random.default_rng(1)
    svc.cfg.false_alarms_per_km2 = 20.0
    n = 0
    for _ in range(400):
        n += sum(1 for c in svc.scan.detect(0.0, 0.0, 40.0, 8.0, 5000.0, 0.0, rng) if not c["true"])
    assert n / 400 == pytest.approx(20.0 * 5000 / 1e6, rel=0.3)

def test_detection_probability_falls_with_altitude(svc):
    from drone.scan import p_detect
    c = svc.cfg
    assert p_detect(c, 20) == pytest.approx(c.p_detect_low) and p_detect(c, 200) == pytest.approx(c.p_detect_high)
    assert p_detect(c, 30) > p_detect(c, 75) > p_detect(c, 120)

def test_take_control_manual_flight_release_and_clock_cap(svc):
    svc.set_auto(False)
    u = first_ready(svc)
    svc.launch_best(u.id)
    run(svc, 400, speed=10, until=lambda s: u.kd.airborne and u.kd.status in ("transit", "surveying"))
    assert u.kd.airborne
    svc.take_control(u.id)
    assert u.kd.mode == "manual" and svc.calls.speed() <= svc.cfg.manual_max_clock       # the demo clock was lowered for the pilot
    pos0 = u.kd.pos.copy()
    for k in range(30):
        svc.manual_input(u.id, 5.0, 0.0, 0.0, 0.0, wall_now=2000.0 + k * 0.05)
        svc.tick(0.05, 2000.0 + k * 0.05)
    assert np.hypot(*(u.kd.pos[:2] - pos0[:2])) > 1.0
    svc.release(u.id, "auto")
    assert u.kd.mode in ("auto", "hold", "rth") and svc.calls.speed() == 10.0           # clock restored

def test_manual_flight_needs_an_airborne_drone_and_return_home_lands(svc):
    u = first_ready(svc)
    with pytest.raises(ServiceError):
        svc.take_control(u.id)
    svc.set_auto(False); svc.launch_best(u.id)
    run(svc, 300, speed=10, until=lambda s: u.kd.airborne)
    svc.return_home(u.id)
    run(svc, 3000, speed=30, until=lambda s: u.kd.status == "landed" or u.ground == "swapping")
    assert u.ground in ("swapping", "ready") and np.allclose(u.kd.pos[:2], u.kd.home)

def test_operator_task_preview_and_infeasible_reason(svc):
    b = int(np.argmax(svc.p_current()[:, 3]))
    pv = svc.preview({"type": "inspect_building", "building": b})
    assert pv["options"] and "feasible" in pv and pv["alt_m"] >= svc.cfg.min_alt_m
    lon, lat = svc.frame.to_lonlat(30000.0, 0.0)
    far = svc.preview({"type": "goto", "lon": float(lon), "lat": float(lat), "agl": 40})
    assert not far["feasible"] and far["reasons"]
    with pytest.raises(ServiceError) as e:
        svc.create_task({"type": "goto", "lon": float(lon), "lat": float(lat), "agl": 40})
    assert e.value.code == "infeasible"

def test_send_drone_to_a_building_flies_the_ring_and_returns(svc):
    svc.set_auto(False)
    b = int(np.argmax(svc.p_current()[:, 3]))
    r = svc.create_task({"type": "inspect_building", "building": b})
    u = svc.units[r["drone"]]
    assert svc.task_state[r["task"]]["pinned"]
    run(svc, 6000, speed=60, until=lambda s: s.scan.conf[b] > 0.5 and u.kd.status in ("returning", "landing", "landed"))
    assert svc.scan.conf[b] > 0.5

def test_verify_with_drone_corroborates_or_tags_empty(svc):
    svc.set_auto(False)
    b = int(np.flatnonzero(svc.truth.state == 0)[0])                         # an intact building: nobody to see
    lon, lat = svc.frame.to_lonlat(float(svc.index.x[b]), float(svc.index.y[b]))
    cid = svc.calls.add_phone_call(float(lat), float(lon))
    r = svc.create_task({"type": "verify_sos", "call_id": cid})
    u = svc.units[r["drone"]]
    run(svc, 6000, speed=60, until=lambda s: cid in s.calls.tags)
    assert svc.calls.tags.get(cid) == "drone_checked_empty" or cid in svc.calls.corroborated

def test_sos_call_raises_the_value_of_its_block(svc):
    k = 5
    x, y = svc.blocks.centre[k]
    base = svc.blocks.values(svc.p_current()[:, 3], svc.occ, svc.scan.conf)["value"][k]
    lon, lat = svc.frame.to_lonlat(float(x), float(y))
    svc.calls.add_phone_call(float(lat), float(lon), weight=4.0)
    svc._queue_dirty = True
    after = svc.blocks.values(svc.p_current()[:, 3], svc.occ, svc.scan.conf, svc._sos_points())["value"][k]
    assert after == pytest.approx(base + svc.cfg.w_sos * 4.0)

def test_priority_value_is_monotone():
    from drone.priority import Blocks
    class Idx:  # a one-block index
        n = 4; x = np.array([10., 20, 30, 40]); y = np.zeros(4); height = np.full(4, 9.0)
    cfg = Config(min_buildings_per_block=1)
    b = Blocks(Idx(), cfg)
    occ, pc, conf = np.full(4, 10.0), np.full(4, 0.3), np.zeros(4)
    v = b.values(pc, occ, conf)["value"][0]
    assert b.values(pc, occ * 2, conf)["value"][0] > v
    assert b.values(pc + 0.1, occ, conf)["victims"][0] > b.values(pc, occ, conf)["victims"][0]
    assert b.values(pc, occ, conf + 0.5)["value"][0] < v
    infos = [b.values(np.full(4, p), occ, conf)["info"][0] for p in (0.1, 0.5, 0.9)]
    assert infos[1] > infos[0] and infos[1] > infos[2]

def test_reroll_changes_truth_and_resets_scan(svc):
    s0 = svc.truth.state.copy()
    run(svc, 300, speed=30)
    svc.reroll(seed=99)
    assert not np.array_equal(s0, svc.truth.state) and svc.scan.conf.max() == 0 and svc.clock.t == 0


def test_taking_control_does_not_trigger_link_lost_before_any_input_arrives(svc):
    """Regression: the manual-input timestamp used to start at 0, so the first physics step saw 'no input for decades' and returned home."""
    svc.set_auto(False)
    u = first_ready(svc)
    svc.launch_best(u.id)
    run(svc, 400, speed=10, until=lambda s: u.kd.airborne and u.kd.status in ("transit", "surveying"))
    svc.take_control(u.id)
    codes = []
    svc.subscribe(lambda m: codes.append(m.get("code")) if m["type"] == "alarm" else None)
    wall = svc.wall()
    for k in range(20):                                    # two seconds of wall time, no stick input at all
        svc.tick(0.1, wall + 0.1 * (k + 1))
    assert u.kd.mode == "manual" and "LINK_LOST" not in codes
    for k in range(20, 80):                                # five more seconds of silence: now the link really is lost
        svc.tick(0.1, wall + 0.1 * (k + 1))
    assert "LINK_LOST" in codes                              # it then flew home (and may already have landed)


def test_default_fleet_is_two_drones_one_of_them_the_camera_drone(tmp_path, monkeypatch):
    c = dronefix.make_city(monkeypatch, tmp_path)
    s = DroneService(Config(launch_delay_s=20.0), FakeCalls(speed=10.0))
    s.bind(c["model"], c["layer"], s.calls, live.blockage_now(c["layer"]))
    assert len(s.units) <= 2 and not [u for u in s.units.values() if u.status == "offline"]
    roles = [u.role for u in s.units.values()]
    assert roles.count("camera") == 1 and roles[0] == "camera"
    msgs = {m["id"]: m for m in s.snapshot()["fleet"]}
    assert {m["label"] for m in msgs.values()} <= {"Camera drone", "Auto drone"}


def test_camera_drone_is_never_auto_tasked_and_launches_to_a_hover(tmp_path, monkeypatch):
    c = dronefix.make_city(monkeypatch, tmp_path)
    s = DroneService(Config(launch_delay_s=20.0, swap_time_s=60.0), FakeCalls(speed=10.0))
    s.bind(c["model"], c["layer"], s.calls, live.blockage_now(c["layer"]))
    s.set_auto(True)
    run(s, 120)
    cam = next(u for u in s.units.values() if u.role == "camera")
    assert cam.status == "ready"                                         # auto mode sent the other drone, not this one
    s.launch_best(cam.id)                                                # no target needed: it just takes off
    run(s, 60, until=lambda _s: cam.kd.airborne)
    assert cam.kd.airborne
    run(s, 120)
    assert cam.kd.airborne and cam.kd.mode == "hold"                         # it waits in the air for the pilot
    s.take_control(cam.id)
    assert cam.kd.mode == "manual"
