"""The one realistic part of the simulated drone: power, flight time, return-to-home and reach. Pure Python, shared by the planner,
the simulator and the reach rings, so a plan's energy estimate and the simulated drain use exactly the same formulas.

    P(v, vz, w) = p_hover * (1 + k_forward v^2) * (1 + k_wind w^2) + p_payload + max(0, m g vz / eta_climb)        [W]
"""
import math

G = 9.81


def power_w(cfg, v: float, vz: float = 0.0, wind: float | None = None) -> float:
    w = cfg.wind_ms if wind is None else wind
    return (cfg.p_hover_w * (1.0 + cfg.k_forward * v * v) * (1.0 + cfg.k_wind * w * w)
            + cfg.p_payload_w + max(0.0, cfg.mass_kg * G * vz / cfg.eta_climb))


def leg(cfg, dx: float, dy: float, dz: float, speed: float, wind: float | None = None):
    """One straight leg at a constant commanded horizontal speed. Returns (energy_wh, seconds, v_horizontal, vz)."""
    dh = math.hypot(dx, dy)
    sp = max(speed, 0.1)
    t = max(dh / sp, abs(dz) / cfg.climb_rate_ms)
    if t <= 1e-9:
        return 0.0, 0.0, 0.0, 0.0
    vh, vz = dh / t, dz / t
    return power_w(cfg, vh, vz, wind) * t / 3600.0, t, vh, vz


def hover_wh(cfg, seconds: float, wind: float | None = None) -> float:
    return power_w(cfg, 0.0, 0.0, wind) * seconds / 3600.0


def route_energy(cfg, route: list[dict], start: tuple[float, float, float] | None = None, wind: float | None = None):
    """route: waypoints with x, y, z, speed and optional dwell_s. Returns (energy_wh, seconds, legs); legs are per-waypoint
    (energy_wh, seconds). The first waypoint is the start unless `start` is given. Landing allowance is added by `landing` kinds."""
    legs, total_e, total_t = [], 0.0, 0.0
    prev = start if start is not None else ((route[0]["x"], route[0]["y"], route[0]["z"]) if route else None)
    for k, wp in enumerate(route):
        if k == 0 and start is None:
            e, t = 0.0, 0.0
        else:
            e, t, _, _ = leg(cfg, wp["x"] - prev[0], wp["y"] - prev[1], wp["z"] - prev[2], wp.get("speed", cfg.cruise_speed_ms), wind)
        d = wp.get("dwell_s", 0.0)
        if d:
            e += hover_wh(cfg, d, wind); t += d
        if wp.get("kind") == "land":
            e += cfg.landing_wh
        legs.append((e, t)); total_e += e; total_t += t
        prev = (wp["x"], wp["y"], wp["z"])
    return total_e, total_t, legs


def usable_wh(cfg) -> float:
    """Energy a full battery may spend before the return reserve is touched."""
    return cfg.battery_wh * (1.0 - cfg.reserve_fraction)


def reserve_wh(cfg) -> float:
    return cfg.reserve_fraction * cfg.battery_wh


def energy_to_home(cfg, x: float, y: float, z: float, hx: float, hy: float, wind: float | None = None) -> float:
    """Straight flight home at cruise speed (climbing to a safe height is ignored: a descent gives nothing back), plus the landing."""
    d = math.hypot(hx - x, hy - y)
    t = d / cfg.cruise_speed_ms
    return (power_w(cfg, cfg.cruise_speed_ms, 0.0, wind) * t / 3600.0 + cfg.landing_wh) * cfg.rth_margin


def must_return(cfg, battery_wh: float, e_home: float) -> bool:
    return battery_wh <= e_home + reserve_wh(cfg)


def low_battery_warning(cfg, battery_wh: float, e_home: float) -> bool:
    return battery_wh <= e_home + 1.5 * reserve_wh(cfg)


def no_return_radius_m(cfg, battery_wh: float, dist_home_m: float, wind: float | None = None) -> float:
    """How much further out the drone can still fly and get home with the reserve intact (the 'point of no return' ring)."""
    k = power_w(cfg, cfg.cruise_speed_ms, 0.0, wind) / (3600.0 * cfg.cruise_speed_ms)      # Wh per metre at cruise
    avail = (battery_wh - reserve_wh(cfg) - cfg.landing_wh * cfg.rth_margin) / cfg.rth_margin
    return max(0.0, (avail / k - dist_home_m) / 2.0)


def endurance_min(cfg, v: float = 0.0, wind: float | None = None) -> float:
    """Minutes from a full battery at a constant horizontal speed, to empty (not to the reserve)."""
    return cfg.battery_wh / power_w(cfg, v, 0.0, wind) * 60.0


def _wh_per_m_cruise(cfg, wind=None) -> float:
    return power_w(cfg, cfg.cruise_speed_ms, 0.0, wind) / (3600.0 * cfg.cruise_speed_ms)


def survey_minutes_at(cfg, dist_m: float, wind: float | None = None) -> float:
    """Minutes of survey left on a full battery after flying out `dist_m` and back."""
    e_use = usable_wh(cfg) - cfg.landing_wh
    p_sv = power_w(cfg, cfg.survey_speed_ms, 0.0, wind)
    return max(0.0, (e_use - 2.0 * _wh_per_m_cruise(cfg, wind) * dist_m) / p_sv * 60.0)


def reach(cfg, wind: float | None = None) -> dict:
    """Round-trip reach of a station with a full battery: r_max (get there, survey two minutes, come back) and the distances at which
    5 and 10 minutes of survey remain."""
    e_use = usable_wh(cfg) - cfg.landing_wh
    p_sv = power_w(cfg, cfg.survey_speed_ms, 0.0, wind)
    k = _wh_per_m_cruise(cfg, wind)
    r = lambda minutes: max(0.0, (e_use - p_sv * minutes / 60.0) / (2.0 * k))
    return {"r_max_m": r(2.0), "r_10min_m": r(10.0), "r_5min_m": r(5.0)}
