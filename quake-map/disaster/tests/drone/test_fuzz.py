"""Randomised stress test of the service: random operator commands for thousands of ticks, with invariants checked on every tick."""
import json, math
import numpy as np, pytest
from drone.config import Config
from drone.service import DroneService, FakeCalls, ServiceError
import dronefix, live

AIR = ("takeoff", "transit", "surveying", "returning", "landing", "hold")


@pytest.fixture(scope="module")
def city(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    c = dronefix.make_city(mp, tmp_path_factory.mktemp("fuzz"))
    yield c
    mp.undo()


def strict(msg):
    json.dumps(msg, allow_nan=False, default=lambda o: o.item() if hasattr(o, "item") else list(o))


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_random_operator_actions_never_break_an_invariant(city, seed):
    rng = np.random.default_rng(seed)
    cfg = Config(launch_delay_s=10.0, swap_time_s=30.0, max_stations=4, camera_drone=False, offline_example=True, seed=seed, auto_mode=bool(seed % 2))
    s = DroneService(cfg, FakeCalls(speed=float(rng.choice([5, 10, 30, 60]))))
    s.bind(city["model"], city["layer"], s.calls, live.blockage_now(city["layer"]))
    bad = []
    def check(m):
        try: strict(m)
        except Exception as ex: bad.append((m["type"], str(ex)))
    s.subscribe(check)
    wall = 5000.0
    units = list(s.units.values())
    for step in range(1500):
        wall += 0.1
        if step % 7 == 0:
            u = units[int(rng.integers(len(units)))]
            op = rng.choice(["launch", "take", "input", "rth", "hold", "release", "goto", "pin", "skip", "task", "det", "sos", "speed", "pause", "auto"])
            try:
                if op == "launch": s.launch_best(u.id)
                elif op == "take": s.take_control(u.id)
                elif op == "input":
                    for _ in range(5): s.manual_input(u.id, *rng.uniform(-8, 8, 3), rng.uniform(-1, 1), wall_now=wall, assist=bool(rng.random() < .8))
                elif op == "rth": s.return_home(u.id)
                elif op == "hold": s.hold(u.id)
                elif op == "release": s.release(u.id, str(rng.choice(["auto", "hold", "rth"])))
                elif op == "goto":
                    lon, lat = s.frame.to_lonlat(float(rng.uniform(-900, 900)), float(rng.uniform(-900, 900))); s.goto(u.id, float(lon), float(lat), float(rng.uniform(20, 100)))
                elif op in ("pin", "skip"):
                    q = s.queue(10, False)
                    if q: s.patch_task(q[int(rng.integers(len(q)))]["id"], pinned=(op == "pin") or None, skipped=(op == "skip") or None)
                elif op == "task": s.create_task({"type": "inspect_building", "building": int(rng.integers(s.index.n))})
                elif op == "det":
                    b = int(rng.integers(s.index.n)); s._add_detection({"x": float(s.index.x[b]), "y": float(s.index.y[b]), "building": b, "confidence": float(rng.uniform(.2, .99)), "est_people": 1, "true": True, "site": 0}, u.id, s.clock.t)
                    un = s.unverified_ids() if hasattr(s, "unverified_ids") else [d for d, v in s.detections.items() if v["status"] == "unverified"]
                    if un and rng.random() < .5: (s.confirm_detection if rng.random() < .5 else s.reject_detection)(un[0])
                elif op == "sos":
                    lon, lat = s.frame.to_lonlat(float(rng.uniform(-900, 900)), float(rng.uniform(-900, 900))); s.calls.add_phone_call(float(lat), float(lon), float(rng.choice([1, 2, 3, 4])))
                elif op == "speed": s.calls.set_speed(float(rng.choice([1, 2, 5, 10, 30, 60])))
                elif op == "pause": s.clock.paused = bool(rng.random() < .3)
                elif op == "auto": s.set_auto(bool(rng.random() < .6))
            except ServiceError:
                pass                                                                    # refusing a nonsensical command is correct behaviour
        s.tick(0.1, wall)
        for u in units:
            kd = u.kd
            assert np.all(np.isfinite(kd.pos)) and np.all(np.isfinite(kd.vel)) and math.isfinite(kd.battery_wh), (seed, step, u.id)
            assert 0.0 <= kd.battery_wh <= cfg.battery_wh + 1e-6, (seed, step, u.id, kd.battery_wh)
            assert kd.pos[2] >= -1e-6 and kd.pos[2] <= cfg.max_alt_m + 1e-6 or kd.status in ("landed", "ready", "swapping"), (seed, step, u.id, kd.pos)
            if kd.airborne:
                assert u.status in AIR and kd.mode in ("auto", "assisted", "manual", "rth", "hold")
            assert 0 <= u.station["spare"] <= cfg.spare_batteries
        assert np.all(s.scan.conf >= 0) and np.all(s.scan.conf <= 1.0 + 1e-6)
    assert not bad, bad[:3]
    strict(s.snapshot())
    assert len(s.recording) <= 80_000
