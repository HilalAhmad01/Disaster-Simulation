"""Energy model (flight time, return reserve, reach), georeferencing and the clock. Pure Python, no network."""
import math
import numpy as np, pytest
from drone.config import Config, from_env
from drone import energy as E
from drone.georef import AreaFrame, heading_deg, yaw_from_heading
from drone.clock import SimClock


def cfg(**k):
    return Config(**k)


# ---- the endurance acceptance table of the plan (10.6), against the reference airframe
def test_hover_endurance_no_wind():
    assert 32 <= E.endurance_min(cfg(), 0.0, wind=0.0) <= 36

def test_cruise_endurance():
    c = cfg()
    assert 26 <= E.endurance_min(c, 10.0, wind=0.0) <= 31
    assert 20 <= E.endurance_min(c, 15.0, wind=0.0) <= 25

def test_wind_cuts_endurance_15_to_30_percent():
    c = cfg()
    drop = 1 - E.endurance_min(c, 0.0, wind=8.0) / E.endurance_min(c, 0.0, wind=0.0)
    assert 0.15 <= drop <= 0.30

def test_power_is_monotone_and_climb_costs_energy_descent_does_not():
    c = cfg()
    assert E.power_w(c, 10) > E.power_w(c, 5) > E.power_w(c, 0)
    assert E.power_w(c, 5, vz=3) > E.power_w(c, 5)
    assert E.power_w(c, 5, vz=-3) == E.power_w(c, 5)

def test_route_energy_matches_leg_sum_and_land_adds_allowance():
    c = cfg()
    route = [dict(x=0, y=0, z=0, speed=0), dict(x=0, y=0, z=60, speed=3), dict(x=1000, y=0, z=60, speed=10), dict(x=0, y=0, z=60, speed=10), dict(x=0, y=0, z=0, speed=2, kind="land")]
    e, t, legs = E.route_energy(c, route)
    assert e == pytest.approx(sum(l[0] for l in legs)) and t == pytest.approx(sum(l[1] for l in legs))
    e_no_land, *_ = E.route_energy(c, route[:-1] + [dict(route[-1], kind="x")])
    assert e - e_no_land == pytest.approx(c.landing_wh)
    assert 0 < e < c.battery_wh

def test_dwell_adds_hover_energy():
    c = cfg()
    a, *_ = E.route_energy(c, [dict(x=0, y=0, z=30, speed=5), dict(x=100, y=0, z=30, speed=5)])
    b, *_ = E.route_energy(c, [dict(x=0, y=0, z=30, speed=5), dict(x=100, y=0, z=30, speed=5, dwell_s=60)])
    assert b - a == pytest.approx(E.hover_wh(c, 60))

def test_forced_return_threshold_and_warning_order():
    c = cfg()
    e_home = E.energy_to_home(c, 3000, 0, 60, 0, 0)
    assert E.low_battery_warning(c, e_home + 1.4 * E.reserve_wh(c), e_home)
    assert not E.must_return(c, e_home + 1.4 * E.reserve_wh(c), e_home)
    assert E.must_return(c, e_home + 0.9 * E.reserve_wh(c), e_home)

def test_no_return_radius_shrinks_with_distance_and_battery():
    c = cfg()
    full = E.no_return_radius_m(c, c.battery_wh, 0.0)
    assert full > 1000
    assert E.no_return_radius_m(c, c.battery_wh, 2000.0) < full
    assert E.no_return_radius_m(c, c.battery_wh * 0.5, 0.0) < full
    assert E.no_return_radius_m(c, 1.0, 0.0) == 0.0

def test_reach_rings_ordered_and_survey_minutes():
    c = cfg()
    r = E.reach(c)
    assert r["r_max_m"] > r["r_5min_m"] > r["r_10min_m"] > 0
    assert E.survey_minutes_at(c, r["r_10min_m"]) == pytest.approx(10.0, abs=0.01)
    assert E.survey_minutes_at(c, 0) > E.survey_minutes_at(c, 1500)

def test_usable_plus_reserve_is_full_battery():
    c = cfg(battery_wh=120.0, reserve_fraction=0.3)
    assert E.usable_wh(c) + E.reserve_wh(c) == pytest.approx(120.0)


# ---- georeferencing
def test_round_trip_under_a_centimetre_over_30_km():
    f = AreaFrame(77.43, 28.535)
    rng = np.random.default_rng(0)
    lon = 77.43 + (rng.random(500) - .5) * 0.3; lat = 28.535 + (rng.random(500) - .5) * 0.27
    x, y = f.to_local(lon, lat)
    lon2, lat2 = f.to_lonlat(x, y)
    err_m = np.hypot((lon2 - lon) * 111320 * math.cos(math.radians(28.5)), (lat2 - lat) * 110574)
    assert err_m.max() < 0.01

def test_known_distance_and_epsg_choice():
    f = AreaFrame(77.33, 28.575)
    assert f.epsg == 32643
    a = f.to_local(77.32, 28.575); b = f.to_local(77.33, 28.575)
    assert abs(abs(float(b[0] - a[0])) - 978.4) < 1.5          # 0.01 degrees of longitude at 28.6 N
    assert AreaFrame(-70.0, -33.0).epsg == 32719

def test_origin_is_zero_and_chunks_are_deterministic():
    f = AreaFrame(77.33, 28.575)
    x, y = f.to_local(77.33, 28.575)
    assert abs(float(x)) < 1e-6 and abs(float(y)) < 1e-6
    assert AreaFrame.chunk_id(10.0, -10.0, 250) == (0, -1) and AreaFrame.chunk_id(250.0, 499.9, 250) == (1, 1)
    assert AreaFrame.chunk_origin(2, -1, 250) == (500, -250)

def test_heading_conversion_is_its_own_inverse():
    for d in (0, 45, 90, 180, 271.5):
        assert heading_deg(yaw_from_heading(d)) == pytest.approx(d % 360, abs=1e-9)
    assert heading_deg(0.0) == 90.0           # yaw 0 = east = compass 90


# ---- clock and config
def test_clock_follows_the_demo_speed_pause_and_cap():
    speed = [10.0]
    c = SimClock(lambda: speed[0])
    assert c.advance(0.1) == pytest.approx(1.0)
    speed[0] = 60.0
    assert c.advance(0.1) == pytest.approx(6.0)
    c.cap = 2.0
    assert c.speed == 2.0
    c.paused = True
    assert c.advance(1.0) == 0.0
    c.paused = False
    t = c.t
    c.advance(1000.0)
    assert c.t - t <= c.max_step_s + 1e-9
    c.reset(); assert c.t == 0.0 and c.cap is None

def test_config_env_override(monkeypatch):
    monkeypatch.setenv("DRONE_BATTERY_WH", "123.5"); monkeypatch.setenv("DRONE_FEEDBACK_ENABLED", "0"); monkeypatch.setenv("DRONE_MAX_STATIONS", "3")
    c = from_env()
    assert c.battery_wh == 123.5 and c.feedback_enabled is False and c.max_stations == 3
    with pytest.raises(AttributeError):
        from_env(nope=1)
