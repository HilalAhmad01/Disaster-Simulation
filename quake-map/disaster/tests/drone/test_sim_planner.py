"""The kinematic drone and the planner: energy consistency, failsafes, manual control, survey paths, sortie feasibility."""
import math, time
import numpy as np, pytest
from drone.config import Config
from drone import energy as E, planner as P
from drone.sim import KDrone


def fly(d, dt=0.5, max_s=7200, wall=lambda t: t, world=None):
    ev, t = [], 0.0
    while d.airborne and t < max_s:
        ev += d.step(dt, t, wall(t), world); t += dt
    return ev, t


def sample_task(cfg, x=1500.0, y=800.0, tid="t1", value=10.0):
    rect = (x, y, x + 250, y + 250)
    path = P.survey_path(cfg, rect)
    wps = P.survey_wps(cfg, path, 45.0, tid)
    return {"id": tid, "type": "survey_block", "alt": 45.0, "wps": wps, "entry": path[0], "exit": path[-1], "centre": (x + 125, y + 125), "value": value}


def test_survey_path_covers_a_block_with_overlapping_passes():
    cfg = Config()
    path = P.survey_path(cfg, (0, 0, 250, 250))
    ys = sorted({round(p[1], 3) for p in path})
    assert ys[0] <= cfg.swath_m / 2 + 1e-6 and 250 - ys[-1] <= cfg.swath_m / 2 + 1e-6          # edges covered
    assert all(b - a <= cfg.swath_m + 1e-6 for a, b in zip(ys, ys[1:]))                       # no gaps wider than the swath
    assert len(ys) >= 5 and len(path) == 2 * len(ys)
    assert path[1][0] == 250 and path[2][0] == 250                                              # alternating direction

def test_block_altitude_rules():
    cfg = Config()
    assert P.block_alt(cfg, 3.0) == (cfg.min_alt_m, False)
    assert P.block_alt(cfg, 40.0) == (60.0, False)
    alt, tall = P.block_alt(cfg, 150.0)
    assert alt == cfg.max_alt_m and tall


def test_planned_energy_matches_simulated_drain_within_5_percent():
    cfg = Config()
    home = (0.0, 0.0)
    for tasks in ([sample_task(cfg)], [sample_task(cfg, 1500, 800, "a"), sample_task(cfg, 2200, -300, "b")]):
        route = P.build_route(cfg, home, tasks, 60.0)
        planned, t_plan, _ = E.route_energy(cfg, route)
        d = KDrone(cfg, "d", "s", home); d.launch(route, "s1")
        ev, t = fly(d)
        assert d.status == "landed"
        used = cfg.battery_wh - d.battery_wh
        assert used == pytest.approx(planned, rel=0.02) and t == pytest.approx(t_plan, abs=2.0)
        assert any(e["type"] == "task_done" for e in ev)

def test_a_planned_sortie_lands_with_at_least_the_reserve():
    cfg = Config()
    cands = [sample_task(cfg, 500 + 300 * k, 300, f"t{k}", value=10 + k) for k in range(12)]
    s = P.plan_sortie(cfg, (0.0, 0.0), cfg.battery_wh, cands, 60.0)
    assert s and len(s["tasks"]) >= 3
    assert P.feasible(cfg, s["energy_wh"], cfg.battery_wh)
    d = KDrone(cfg, "d", "s", (0, 0)); d.launch(s["route"])
    fly(d)
    assert d.status == "landed" and d.battery_wh >= E.reserve_wh(cfg) * 0.99

def test_planner_never_violates_the_energy_constraint_property():
    cfg = Config()
    rng = np.random.default_rng(4)
    for _ in range(15):
        n = int(rng.integers(1, 10))
        cands = [sample_task(cfg, rng.uniform(-3000, 3000), rng.uniform(-3000, 3000), f"t{k}", value=float(rng.uniform(1, 50))) for k in range(n)]
        batt = float(rng.uniform(60, cfg.battery_wh))
        s = P.plan_sortie(cfg, (0.0, 0.0), batt, cands, 60.0)
        if s:
            assert s["energy_wh"] * 1.1 <= batt - E.reserve_wh(cfg) + 1e-9
            assert len(set(s["tasks"])) == len(s["tasks"])

def test_far_task_is_out_of_range_and_reason_is_given():
    cfg = Config()
    r = P.single_feasibility(cfg, (0, 0), sample_task(cfg, 9000, 0), cfg.battery_wh, 60.0)
    assert not r["feasible"] and r["reason"] == "out_of_range"
    ok = P.single_feasibility(cfg, (0, 0), sample_task(cfg, 800, 0), cfg.battery_wh, 60.0)
    assert ok["feasible"]
    low = P.single_feasibility(cfg, (0, 0), sample_task(cfg, 800, 0), 40.0, 60.0)
    assert not low["feasible"] and low["reason"] == "not_enough_battery"

def test_pinned_task_is_included_when_feasible_and_planner_is_fast():
    cfg = Config()
    cands = [sample_task(cfg, 400 + 250 * k, 200, f"t{k}", value=100 - 10 * k) for k in range(30)]
    t0 = time.perf_counter()
    s = P.plan_sortie(cfg, (0.0, 0.0), cfg.battery_wh, cands, 60.0, pinned=("t8",))
    el = time.perf_counter() - t0
    assert s["tasks"][0] == "t8"
    assert el < 3.0                                                         # the plan's 200 ms is for the app; python in CI gets slack


def test_forced_return_when_the_battery_reaches_the_reserve():
    cfg = Config()
    route = P.build_route(cfg, (0, 0), [sample_task(cfg, 2500, 0)], 60.0)
    d = KDrone(cfg, "d", "s", (0, 0)); d.launch(route)
    d.battery_wh = 50.0                                                      # nearly empty far from home
    ev, _ = fly(d)
    codes = [e.get("code") for e in ev if e["type"] == "alarm"]
    assert "RTH_FORCED" in codes
    assert d.status == "landed" and d.battery_wh >= 0.05 * cfg.battery_wh        # lands with at least 5 percent of a full battery left

def test_battery_empty_means_lost():
    cfg = Config(reserve_fraction=0.0, rth_margin=0.0)
    d = KDrone(cfg, "d", "s", (0, 0)); d.launch(P.build_route(cfg, (0, 0), [sample_task(cfg, 4000, 0)], 60.0))
    d.battery_wh = 5.0
    ev, _ = fly(d)
    assert d.status == "lost" and any(e.get("code") == "LOST" for e in ev)

def test_low_battery_warning_comes_before_forced_return():
    cfg = Config()
    d = KDrone(cfg, "d", "s", (0, 0)); d.launch(P.build_route(cfg, (0, 0), [sample_task(cfg, 2000, 0)], 60.0))
    ev, _ = fly(d)
    d2 = KDrone(cfg, "d", "s", (0, 0)); d2.launch(P.build_route(cfg, (0, 0), [sample_task(cfg, 2000, 0)], 60.0)); d2.battery_wh = 70.0
    ev2, _ = fly(d2)
    codes = [e["code"] for e in ev2 if e["type"] == "alarm"]
    assert codes.index("LOW_BATTERY") < codes.index("RTH_FORCED") if "LOW_BATTERY" in codes else "RTH_FORCED" in codes


# ---- manual control, hand-over and failsafes (wall clock is passed in explicitly)
def airborne(cfg, z=50.0):
    d = KDrone(cfg, "d", "s", (0, 0)); d.status = "transit"; d.pos[:] = [100, 0, z]; d.mode = "auto"
    return d

def test_manual_input_moves_the_drone_in_its_heading_frame():
    cfg = Config(); d = airborne(cfg); d.take_control(); d.yaw = math.pi / 2                  # facing north
    d.manual_input(5.0, 0.0, 0.0, 0.0, wall_now=10.0)
    for k in range(10):
        d.step(0.1, k * 0.1, 10.0)
    assert d.pos[1] == pytest.approx(5.0, abs=0.01) and d.pos[0] == pytest.approx(100.0, abs=0.01)

def test_deadman_hovers_after_500_ms_without_input():
    cfg = Config(); d = airborne(cfg); d.take_control()
    d.manual_input(6.0, 0, 0, 0, wall_now=0.0)
    d.step(0.1, 0, 0.3); moving = abs(d.vel[0]) > 1
    d.step(0.1, 0.1, 0.9)                                                                     # 0.9 s after the last input
    assert moving and np.allclose(d.vel, 0) and d.mode == "manual"

def test_link_lost_returns_home_after_five_seconds():
    cfg = Config(); d = airborne(cfg); d.take_control(); d.manual_input(0, 0, 0, 0, wall_now=0.0)
    ev = d.step(0.1, 0, 6.0)
    assert d.mode == "rth" and any(e.get("code") == "LINK_LOST" for e in ev)
    fly(d); assert d.status == "landed" and np.allclose(d.pos[:2], d.home)

def test_take_control_pauses_the_task_and_resume_continues_it():
    cfg = Config(); d = KDrone(cfg, "d", "s", (0, 0)); d.launch(P.build_route(cfg, (0, 0), [sample_task(cfg, 1500, 0)], 60.0))
    for k in range(400):
        d.step(0.5, k * 0.5, 0.0)
    assert d.status in ("transit", "surveying") and d.route
    left = len(d.route) - d.i
    d.take_control()
    assert d.mode == "manual" and d.owner == "operator" and len(d.saved) == left
    ev = d.release("auto")
    assert d.mode == "auto" and d.route and d.owner == "auto"
    fly(d); assert d.status == "landed"

def test_release_to_return_and_release_with_nothing_to_resume():
    cfg = Config(); d = airborne(cfg); d.take_control()
    ev = d.release("auto")
    assert d.mode == "hold"
    d.take_control(); ev = d.release("rth")
    assert d.mode == "rth"

def test_hold_still_drains_the_battery():
    cfg = Config(); d = airborne(cfg); d.hold()
    b0 = d.battery_wh
    for k in range(100):
        d.step(1.0, k, 0.0)
    assert b0 - d.battery_wh == pytest.approx(E.hover_wh(cfg, 100.0), rel=1e-6)
    assert d.status == "hold"

def test_manual_flight_is_limited_to_the_speed_cap_and_altitude_band():
    cfg = Config(); d = airborne(cfg, z=119.0); d.take_control()
    d.manual_input(50.0, 0, 5.0, 0, wall_now=0.0)
    for k in range(40):
        d.step(0.1, k * 0.1, 0.0 + 0.01 * k)
    assert math.hypot(*d.vel[:2]) <= cfg.manual_speed_ms + 1e-9 and d.pos[2] <= cfg.max_alt_m + 1e-9

def test_assist_stops_before_a_wall_and_no_assist_can_crash():
    cfg = Config()
    class Wall:
        """A wall at x = 120: clearance is the distance to it."""
        def clearance(self, x, y, z, r=40.0):
            return max(120.0 - x, 0.0), 5
    def run(assist):
        d = airborne(cfg, z=10.0); d.take_control()
        for k in range(300):
            d.manual_input(6.0, 0, 0, 0, wall_now=k * 0.1, assist=assist)
            d.step(0.1, k * 0.1, k * 0.1, Wall())
            if d.status == "crashed":
                break
        return d
    a, n = run(True), run(False)
    assert a.status != "crashed" and 120.0 - a.pos[0] >= 2.5
    assert n.status == "crashed"

def test_ins_drift_grows_like_a_random_walk():
    cfg = Config(); sizes = []
    for s in range(30):
        d = airborne(cfg); d.rng = np.random.default_rng(s); d.hold()
        for k in range(600):
            d.step(1.0, k, 0.0)
        sizes.append(np.linalg.norm(d.bias))
    expect = cfg.slam_drift_per_sqrt_s * math.sqrt(600) * math.sqrt(3)
    assert 0.5 * expect < np.mean(sizes) < 1.5 * expect

def test_landing_resets_state_and_scanning_flag():
    cfg = Config(); d = KDrone(cfg, "d", "s", (0, 0)); d.launch(P.build_route(cfg, (0, 0), [sample_task(cfg, 600, 0)], 60.0))
    seen = False
    for k in range(4000):
        d.step(0.5, k * 0.5, 0.0); seen |= d.scanning
        if not d.airborne: break
    assert seen and d.status == "landed" and not d.scanning and d.pos[2] == 0 and not d.route


def test_a_goto_hovers_at_the_target_instead_of_landing_in_the_field():
    cfg = Config(); d = airborne(cfg)
    d.follow([dict(x=400.0, y=0.0, z=50.0, speed=10.0, scan=True, kind="transit")], "assisted")
    ev = []
    for k in range(200):
        ev += d.step(0.5, k * 0.5, 0.0)
    assert d.status == "hold" and d.mode == "hold" and d.airborne and d.pos[2] == pytest.approx(50.0)
    assert abs(d.pos[0] - 400.0) < 1e-6 and any(e["type"] == "mode" and e["mode"] == "hold" for e in ev)
    d.release("auto"); assert d.mode == "hold"
