"""Every tunable of the drone module in one place. Override any field with an environment variable DRONE_<NAME> (upper case)."""
import dataclasses, os
from dataclasses import dataclass


@dataclass
class Config:
    # --- feature switches
    enabled: bool = True
    max_stations: int = 2                  # drones are created for at most this many police stations (best placed for the risky areas)
    camera_drone: bool = True              # the first drone is the 'camera drone': never auto-tasked, it waits for a pilot (the phone); the rest fly on their own
    offline_example: bool = False          # also show one drone whose station is in a likely-damaged building (an example of a station that is down)
    seed: int = 1                          # hidden truth and noise
    auto_mode: bool = True                 # drones start by choosing sorties from the priority queue on their own
    # --- reference airframe (an enterprise quadrotor with a thermal + lidar payload); tuned so endurance is ~25-35 min
    mass_kg: float = 4.0
    battery_wh: float = 200.0
    p_hover_w: float = 330.0
    p_payload_w: float = 25.0
    k_forward: float = 0.002               # forward flight: P *= 1 + k v^2
    k_wind: float = 0.004                  # wind: P *= 1 + k w^2
    eta_climb: float = 0.7
    cruise_speed_ms: float = 10.0
    survey_speed_ms: float = 8.0
    max_speed_ms: float = 14.0
    climb_rate_ms: float = 3.0
    landing_wh: float = 8.0                # fixed energy for the last descent
    reserve_fraction: float = 0.25         # of a full battery kept for the return
    rth_margin: float = 1.1                # safety factor on the return estimate
    launch_delay_s: float = 6.0            # pre-flight checks (sim seconds): short, so a press on Take off is followed by take-off within seconds at any speed
    swap_time_s: float = 300.0
    recharge_time_s: float = 3600.0
    spare_batteries: int = 3
    wind_ms: float = 3.0                   # steady wind used by the energy model
    # --- sensing
    lidar_range_m: float = 60.0
    lidar_az: int = 24
    lidar_el: int = 5
    camera_fov_deg: float = 70.0
    swath_m: float = 60.0
    scan_rate_per_s: float = 0.35          # fraction of a building seen per second inside the footprint (before quality)
    partial_threshold: float = 0.10
    scanned_threshold: float = 0.60
    observation_accuracy: float = 0.9
    # --- detection (simplified)
    p_detect_low: float = 0.9              # per detection chance, at <= 30 m
    p_detect_high: float = 0.3             # at >= 120 m
    detect_tick_s: float = 1.0
    detect_scale: float = 0.5              # per-tick probability = p_detect * detect_scale
    false_alarms_per_km2: float = 1.5
    merge_radius_m: float = 15.0
    merge_window_s: float = 600.0
    sos_merge_radius_m: float = 20.0
    auto_confirm_threshold: float = 0.0    # 0 = off; e.g. 0.85
    verify_dwell_s: float = 30.0
    # --- hidden truth
    p_trapped_collapsed: float = 0.35
    p_exposed_given_trapped: float = 0.002   # most trapped people are inside rubble where a camera cannot see them: only a few are visible from the air
    p_exposed_given_trapped_partial: float = 0.006
    p_trapped_partial: float = 0.05
    p_escape: float = 0.002                  # people who got out and are standing in the open (few: they have usually walked away)
    truth_cell_corr: float = 0.55          # share of the damage draw shared inside a 500 m cell (spatial correlation)
    # --- world and planner
    chunk_m: float = 250.0
    block_m: float = 250.0
    min_buildings_per_block: int = 5
    w_v: float = 1.0
    w_d: float = 2.0
    w_sos: float = 15.0
    survey_overlap: float = 0.2
    clearance_m: float = 20.0
    min_alt_m: float = 30.0
    max_alt_m: float = 120.0
    storey_m: float = 2.8
    default_height_m: float = 6.0
    planner_k: int = 30
    min_sortie_value: float = 2.0
    auto_tick_s: float = 5.0
    sos_bump: bool = True                  # an airborne auto drone detours to a new SOS / sighting when the battery allows
    # --- feedback
    feedback_enabled: bool = True
    feedback_lambda: float = 0.3
    feedback_min_scanned: int = 8
    feedback_clip_lo: float = 0.5
    feedback_clip_hi: float = 2.0
    # --- manual control and failsafes (wall-clock seconds)
    deadman_s: float = 0.5
    linklost_s: float = 5.0
    manual_speed_ms: float = 8.0
    manual_max_clock: float = 2.0          # manual flight needs the demo clock at or below this
    safe_radius_m: float = 6.0             # collision assist keeps this clear of a wall
    # --- pilot tablet
    pair_code_ttl_s: float = 120.0
    lease_ttl_s: float = 1.5
    lease_grace_s: float = 5.0
    stale_input_s: float = 0.25
    # --- misc
    telemetry_hz: float = 10.0
    max_sim_substep_s: float = 0.5
    slam_drift_per_sqrt_s: float = 0.12    # INS-style random walk of the believed position (Drone-SLAM's default)
    worker_python: str = ""                # path of a Python with torch: turns on the Drone-SLAM physics drone (L1)
    worker_device: str = "cpu"             # one drone runs ~8x real time on one CPU thread; the GPU is not needed
    l1_clock_cap: float = 4.0              # the physics drone keeps up with the demo clock only up to about this speed


def _cast(field: dataclasses.Field, raw: str):
    t = field.type if not isinstance(field.type, str) else {"bool": bool, "int": int, "float": float, "str": str}[field.type]
    if t is bool:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return t(raw)


def from_env(**overrides) -> Config:
    """Defaults, then DRONE_<NAME> environment variables, then explicit keyword overrides."""
    cfg = Config()
    for f in dataclasses.fields(cfg):
        raw = os.environ.get("DRONE_" + f.name.upper())
        if raw not in (None, ""):
            setattr(cfg, f.name, _cast(f, raw))
    for k, v in overrides.items():
        if not hasattr(cfg, k):
            raise AttributeError(k)
        setattr(cfg, k, v)
    return cfg


PUBLIC = ("enabled", "max_stations", "battery_wh", "cruise_speed_ms", "survey_speed_ms", "max_speed_ms", "reserve_fraction", "lidar_range_m",
          "swath_m", "scanned_threshold", "partial_threshold", "launch_delay_s", "swap_time_s", "recharge_time_s", "spare_batteries",
          "manual_speed_ms", "manual_max_clock", "deadman_s", "linklost_s", "telemetry_hz", "feedback_enabled", "auto_confirm_threshold",
          "false_alarms_per_km2", "observation_accuracy", "pair_code_ttl_s", "lease_ttl_s", "min_alt_m", "max_alt_m", "wind_ms")


def public(cfg: Config) -> dict:
    return {k: getattr(cfg, k) for k in PUBLIC}
