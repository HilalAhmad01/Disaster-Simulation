"""L0 kinematic drone: a point mass that flies planned legs at constant speed and drains the battery with energy.py's own formulas, so a
plan's estimated energy equals the simulated drain. It also flies manually (velocity commands with collision assist), holds, and
returns home on its own. A believed position drifts like Drone-SLAM's INS (random walk) for the SLAM-map look.

Statuses it owns (flight): takeoff, transit, surveying, returning, landing, hold, landed, lost, crashed. The service owns the ground
statuses (offline, ready, launching, swapping, waiting_battery).
"""
import math
import numpy as np
from . import energy as E

KIND_STATUS = {"takeoff": "takeoff", "climb": "takeoff", "transit": "transit", "descend": "transit", "survey": "surveying", "dwell": "surveying",
               "goto": "transit", "return": "returning", "land": "landing"}
AIRBORNE = ("takeoff", "transit", "surveying", "returning", "landing", "hold")


class KDrone:
    fidelity = "L0"

    def __init__(self, cfg, did: str, station_id, home: tuple[float, float], seed: int = 0):
        self.cfg, self.id, self.station_id = cfg, did, station_id
        self.home = (float(home[0]), float(home[1]))
        self.rng = np.random.default_rng([cfg.seed, seed])
        self.pos = np.array([self.home[0], self.home[1], 0.0])
        self.yaw, self.vel = 0.0, np.zeros(3)
        self.battery_wh = cfg.battery_wh
        self.status, self.mode, self.owner = "ready", "auto", "auto"
        self.route: list[dict] = []
        self.i = 0
        self._leg = None
        self.dwell = 0.0
        self.saved: list[dict] | None = None
        self.manual = {"vx": 0.0, "vy": 0.0, "vz": 0.0, "yr": 0.0, "t": 0.0}
        self.assist = True
        self.assist_active = False
        self.sortie_id: str | None = None
        self.task_id: str | None = None
        self.low_warned = False
        self.rth_reason: str | None = None
        self.flight_s = 0.0
        self.dist_m = 0.0
        self.bias = np.zeros(3)                 # believed - true position (INS drift)
        self.nearest_m = 999.0
        self._last_avoid_alarm = -1e9

    # ------------------------------------------------------------------ commands
    @property
    def airborne(self) -> bool:
        return self.status in AIRBORNE

    def launch(self, route: list[dict], sortie_id: str | None = None) -> None:
        self.route, self.i, self._leg, self.dwell = [dict(w) for w in route], 0, None, 0.0
        self.sortie_id, self.mode, self.owner = sortie_id, "auto", "auto"
        self.low_warned, self.rth_reason, self.flight_s, self.dist_m = False, None, 0.0, 0.0
        self.pos[:] = [self.home[0], self.home[1], 0.0]
        self.bias[:] = 0.0
        self.status = "takeoff"

    def follow(self, route: list[dict], mode: str = "assisted") -> None:
        """New waypoints while airborne (assisted tasks)."""
        self.route, self.i, self._leg, self.dwell = [dict(w) for w in route], 0, None, 0.0
        self.mode = mode

    def start_rth(self, reason: str = "operator") -> list[dict]:
        z = float(self.pos[2])
        Z = max(z, 40.0)
        r = []
        if z < Z - 0.5:
            r.append(dict(x=self.pos[0], y=self.pos[1], z=Z, speed=self.cfg.climb_rate_ms, scan=False, kind="climb"))
        r += [dict(x=self.home[0], y=self.home[1], z=Z, speed=self.cfg.cruise_speed_ms, scan=False, kind="return"),
              dict(x=self.home[0], y=self.home[1], z=0.0, speed=2.0, scan=False, kind="land")]
        self.route, self.i, self._leg, self.dwell = r, 0, None, 0.0
        self.mode, self.owner, self.rth_reason = "rth", "auto", reason
        self.manual.update(vx=0.0, vy=0.0, vz=0.0, yr=0.0)
        return [{"type": "mode", "mode": "rth", "reason": reason}]

    def take_control(self) -> None:
        if self.mode in ("auto", "assisted") and self.route and self.i < len(self.route):
            self.saved = [dict(w) for w in self.route[self.i:]]
        self.mode, self.owner, self._leg = "manual", "operator", None
        self.manual.update(vx=0.0, vy=0.0, vz=0.0, yr=0.0)

    def release(self, then: str = "auto"):
        """Give control back. 'auto' continues the paused route if the battery still allows it, else returns home; with nothing to
        resume it hovers."""
        self.owner = "auto"
        self.manual.update(vx=0.0, vy=0.0, vz=0.0, yr=0.0)
        if then == "rth":
            return self.start_rth("operator")
        if then == "hold" or not self.saved:
            self.mode, self.status, self._leg = "hold", "hold", None
            return [{"type": "mode", "mode": "hold", "reason": "released"}]
        e_rest = E.route_energy(self.cfg, self.saved, start=tuple(self.pos), wind=self.cfg.wind_ms)[0]
        if self.battery_wh - e_rest * 1.1 < E.reserve_wh(self.cfg):
            self.saved = None
            return self.start_rth("battery too low to resume")
        self.route, self.i, self._leg, self.mode, self.saved = self.saved, 0, None, "auto", None
        return [{"type": "mode", "mode": "auto", "reason": "resumed"}]

    def hold(self) -> None:
        if self.airborne:
            self.mode, self.status = "hold", "hold"; self._leg = None
            self.manual.update(vx=0.0, vy=0.0, vz=0.0, yr=0.0)

    def manual_input(self, vx, vy, vz, yr, wall_now: float, assist: bool = True) -> None:
        self.manual.update(vx=float(vx), vy=float(vy), vz=float(vz), yr=float(yr), t=wall_now)
        self.assist = bool(assist)

    # ------------------------------------------------------------------ physics
    def e_home(self) -> float:
        return E.energy_to_home(self.cfg, self.pos[0], self.pos[1], self.pos[2], self.home[0], self.home[1])

    def step(self, dt: float, sim_t: float, wall_now: float, world=None) -> list[dict]:
        """Advance by dt simulated seconds (dt <= max_sim_substep_s). Returns events."""
        ev: list[dict] = []
        if not self.airborne or dt <= 0:
            return ev
        cfg = self.cfg
        self.flight_s += dt
        self.assist_active = False
        # --- failsafes first: they override every mode
        e_home = self.e_home()
        if self.mode != "rth" and self.status != "landing" and E.must_return(cfg, self.battery_wh, e_home):
            ev.append({"type": "alarm", "code": "RTH_FORCED", "message": "Battery reached the return reserve: returning home"})
            ev += self.start_rth("battery reserve")
        elif not self.low_warned and E.low_battery_warning(cfg, self.battery_wh, e_home) and self.mode != "rth":
            self.low_warned = True
            ev.append({"type": "alarm", "code": "LOW_BATTERY", "message": "Battery low: return soon"})
        if self.mode == "manual":
            age = wall_now - self.manual["t"]
            if age > cfg.linklost_s:
                ev.append({"type": "alarm", "code": "LINK_LOST", "message": "Control link lost: returning home"})
                ev += self.start_rth("link lost")
            else:
                self._manual(dt, wall_now, age, world, ev, sim_t)
        elif self.mode == "hold":
            self._drain(0.0, 0.0, dt); self.vel[:] = 0
        if self.mode in ("auto", "assisted", "rth"):
            self._follow(dt, ev, sim_t)
        # --- INS drift of the believed position
        self.bias += cfg.slam_drift_per_sqrt_s * math.sqrt(dt) * self.rng.standard_normal(3)
        if self.battery_wh <= 0 and self.airborne:
            self.battery_wh, self.status, self.vel[:] = 0.0, "lost", 0.0
            ev.append({"type": "alarm", "code": "LOST", "message": "Battery empty: the drone came down"})
            ev.append({"type": "status", "status": "lost"})
        return ev

    def _drain(self, vh: float, vz: float, dt: float, extra_wh: float = 0.0):
        self.battery_wh = max(0.0, self.battery_wh - E.power_w(self.cfg, vh, vz) * dt / 3600.0 - extra_wh)

    def _follow(self, dt: float, ev: list, sim_t: float):
        remaining = dt
        cfg = self.cfg
        guard = 0
        while remaining > 1e-9 and self.airborne and self.mode in ("auto", "assisted", "rth") and guard < 200:
            guard += 1
            if self.dwell > 0:
                use = min(self.dwell, remaining)
                self._drain(0.0, 0.0, use); self.dwell -= use; remaining -= use; self.vel[:] = 0
                self.status = "surveying"
                continue
            if self.i >= len(self.route):
                if self.mode == "assisted":                      # an operator's 'fly to a point' ends by hovering there, not by landing in the field
                    self.mode, self.status, self._leg = "hold", "hold", None
                    self.vel[:] = 0.0
                    ev.append({"type": "mode", "mode": "hold", "reason": "arrived"})
                    break
                self._finish(ev); break
            wp = self.route[self.i]
            self.status = KIND_STATUS.get(wp.get("kind"), "transit")
            self.task_id = wp.get("task_id")
            if self._leg is None:
                tgt = np.array([wp["x"], wp["y"], wp["z"]], float)
                d = tgt - self.pos
                e, t, vh, vz = E.leg(cfg, d[0], d[1], d[2], wp.get("speed", cfg.cruise_speed_ms), cfg.wind_ms)
                self._leg = {"start": self.pos.copy(), "end": tgt, "t": t, "el": 0.0, "vh": vh, "vz": vz}
                if t <= 1e-9:
                    self.pos[:] = tgt; self._leg = None; self._arrive(wp, ev, sim_t); continue
            L = self._leg
            use = min(remaining, L["t"] - L["el"])
            land_extra = cfg.landing_wh * use / L["t"] if wp.get("kind") == "land" else 0.0
            self._drain(L["vh"], L["vz"], use, land_extra)
            L["el"] += use; remaining -= use
            self.pos[:] = L["start"] + (L["end"] - L["start"]) * min(L["el"] / L["t"], 1.0)
            dvec = (L["end"] - L["start"]) / L["t"]
            self.vel[:] = dvec
            self.dist_m += math.hypot(dvec[0], dvec[1]) * use
            if abs(dvec[0]) + abs(dvec[1]) > 0.2:
                self.yaw = math.atan2(dvec[1], dvec[0])
            if L["el"] >= L["t"] - 1e-9:
                self.pos[:] = L["end"]; self._leg = None
                self._arrive(wp, ev, sim_t)
                if self.status in ("landed", "lost", "crashed"):
                    break

    def _arrive(self, wp, ev, sim_t):
        nxt = self.route[self.i + 1] if self.i + 1 < len(self.route) else None
        k, tid = wp.get("kind"), wp.get("task_id")
        if k == "survey" and (nxt is None or nxt.get("task_id") != tid or nxt.get("kind") != "survey"):
            ev.append({"type": "task_done", "task_id": tid})
        elif k in ("dwell", "goto"):
            ev.append({"type": "task_done", "task_id": tid})
        if k == "transit" and nxt is not None and nxt.get("kind") in ("descend", "survey", "dwell", "goto"):
            ev.append({"type": "sortie", "phase": "arrived", "task_id": tid})
        if wp.get("dwell_s"):
            self.dwell = float(wp["dwell_s"])
        self.i += 1
        if k == "land":
            self._finish(ev)

    def _finish(self, ev):
        if self.status in ("landed", "lost", "crashed"):
            return
        self.pos[2], self.vel[:] = 0.0, 0.0
        self.status, self.mode, self.owner = "landed", "auto", "auto"
        self.route, self._leg, self.task_id, self.saved = [], None, None, None
        ev.append({"type": "status", "status": "landed"})
        ev.append({"type": "sortie", "phase": "landed", "sortie_id": self.sortie_id})

    # ------------------------------------------------------------------ manual flight
    def _integrate(self, wvx, wvy, vz, dt):
        """Where the drone ends up after dt seconds of commanded velocity (L0: straight-line; the physics drone overrides this)."""
        self.vel[:] = [wvx, wvy, vz]
        return self.pos + self.vel * dt

    def _manual(self, dt, wall_now, age, world, ev, sim_t):
        cfg = self.cfg
        m = self.manual
        dead = age > cfg.deadman_s
        vx, vy, vz, yr = (0.0, 0.0, 0.0, 0.0) if dead else (m["vx"], m["vy"], m["vz"], m["yr"])
        sp = math.hypot(vx, vy)
        cap = cfg.manual_speed_ms
        if sp > cap:
            vx, vy = vx / sp * cap, vy / sp * cap
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        wvx, wvy = c * vx - s * vy, s * vx + c * vy
        vz = float(np.clip(vz, -cfg.climb_rate_ms, cfg.climb_rate_ms))
        if world is not None:
            near, _ = world.clearance(self.pos[0], self.pos[1], self.pos[2], r=40.0)
            self.nearest_m = near
            if self.assist and (wvx or wvy or vz):
                look = np.array([self.pos[0] + wvx * 1.0, self.pos[1] + wvy * 1.0, self.pos[2] + vz * 1.0])
                d_look, _ = world.clearance(look[0], look[1], look[2], r=40.0)
                if d_look < cfg.safe_radius_m and d_look <= near + 0.05:                   # heading into something
                    k = max(0.0, (d_look - 3.0) / max(cfg.safe_radius_m - 3.0, 0.1))
                    wvx, wvy, vz = wvx * k, wvy * k, vz * k if vz < 0 else vz
                    self.assist_active = True
                    if sim_t - self._last_avoid_alarm > 5.0:
                        self._last_avoid_alarm = sim_t
                        ev.append({"type": "alarm", "code": "COLLISION_AVOIDED", "message": "Collision assist slowed the drone"})
        new = self._integrate(wvx, wvy, vz, dt)
        new[2] = float(np.clip(new[2], 0.5, cfg.max_alt_m))
        if world is not None and not self.assist:
            d_new, _ = world.clearance(new[0], new[1], new[2], r=40.0)
            if d_new <= 0.3:
                self.pos[:] = new; self.vel[:] = 0
                self.status = "crashed"
                ev.append({"type": "alarm", "code": "CRASHED", "message": "The drone hit an obstacle"})
                ev.append({"type": "status", "status": "crashed"})
                return
        self.dist_m += math.hypot(self.vel[0], self.vel[1]) * dt
        self.pos[:] = new
        if yr:
            self.yaw += yr * dt
        elif sp > 1.0:
            self.yaw = math.atan2(wvy, wvx)
        self._drain(math.hypot(wvx, wvy), vz, dt)
        self.status = "hold" if (not (wvx or wvy or vz)) else "transit"

    # ------------------------------------------------------------------ reporting
    @property
    def scanning(self) -> bool:
        if not self.airborne or self.status in ("landing",):
            return False
        if self.mode == "manual":
            return self.pos[2] > 2.0
        if self.mode in ("auto", "assisted") and self.i < len(self.route):
            return bool(self.route[self.i].get("scan")) and self.pos[2] > 2.0
        return False
