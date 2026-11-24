"""The drone service: one object that owns the incident state (fleet, stations, tasks, scan state, detections, model feedback) and advances
it with `tick`. Pure Python (no web framework): `bridge.py` calls it and broadcasts what it emits.

Everything is simulated. Hidden truth (damage, survivors, blocked streets) is drawn from the model's own probabilities; the drone
discovers it. Nothing from `Truth` is ever put in an emitted message except what a scan has observed.
"""
import math, time
from dataclasses import dataclass, field
import numpy as np
import shapely
from shapely.geometry import Polygon, Point

from . import energy as E, planner as P
from .clock import SimClock
from .config import Config, public
from .feedback import DroneUpdated
from .georef import AreaFrame, heading_deg
from .priority import Blocks
from .scan import Neighbourhoods, ScanState, footprint_radius
from .sim import KDrone
from .physics import PhysicsDrone, WorkerClient
from .truth import Truth, STATE_NAME as TRUTH_NAME
from .world import AreaIndex, RoadIndex, World


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class Calls:
    """What the service needs from the dispatch side (live.py). `bridge.LiveCalls` implements it on the real hub; tests use `FakeCalls`."""
    def speed(self) -> float: return 1.0
    def set_speed(self, v: float) -> None: ...
    def open_calls(self) -> list[dict]: return []               # [{id, lat, lon, weight, status, source}]
    def create_call(self, lat, lon, people, confidence, note, building) -> int: raise NotImplementedError
    def corroborate(self, call_id: int, detection_id: str) -> None: ...
    def tag(self, call_id: int, tag: str) -> None: ...
    def get_call(self, call_id: int) -> dict | None: return None
    def road_override(self, edge_blocked: dict[int, bool]) -> None: ...   # drone-observed streets: {edge id: blocked}


@dataclass
class Unit:
    id: str
    station: dict
    kd: KDrone
    ground: str = "ready"
    launch_at: float | None = None
    swap_until: float | None = None
    plan: dict | None = None
    note: str | None = None
    role: str = "auto"                      # 'camera' = waits for a pilot; 'auto' = chooses its own sorties
    det_acc: float = 0.0
    det_area: float = 0.0
    last_pos_msg: tuple = ()
    slam_vox: set = field(default_factory=set)
    trail: list = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.kd.status in ("lost", "crashed"):
            return self.kd.status
        return self.kd.status if self.kd.airborne else self.ground


class DroneService:
    def __init__(self, cfg: Config | None = None, calls: Calls | None = None, wall=time.time):
        self.cfg = cfg or Config()
        self.calls = calls or Calls()
        self.wall = wall
        self.clock = SimClock(lambda: self.calls.speed())
        self.subs: list = []
        self.bound = False
        self.auto = self.cfg.auto_mode
        self.rec_on = True
        self.recording: list[tuple[float, dict]] = []
        self._rec_t0 = None
        self._seq = 0
        self.units: dict[str, Unit] = {}
        self.stations: list[dict] = []
        self.task_state: dict[str, dict] = {}
        self.extra: dict[str, dict] = {}
        self.bans: list[Polygon] = []
        self.detections: dict[str, dict] = {}
        self.queue_cache: list[dict] = []
        self._queue_dirty = True
        self._next_auto = 0.0
        self._ids = {"sortie": 0, "task": 0, "det": 0}
        self._prev_speed: float | None = None
        self.seen_chunks: set[tuple[int, int]] = set()
        self.extra_seen: set[tuple[int, int]] = set()          # chunks opened for a replay
        self.cloud: list[tuple] = []
        self.rec_snapshot: dict | None = None
        self.road_seen: dict[int, bool] = {}
        self._pending: dict = {}
        self._last_flush = 0.0
        self._last_cloud = 0.0
        self._last_agg = -1e9
        self.model_totals = None
        self.run_id = None
        self.rng = np.random.default_rng(self.cfg.seed)
        self.sessions_hint = None
        self._reach_cache: dict = {}
        self._last_refresh = -1e9
        self.worker: WorkerClient | None = None
        self.worker_ok = False

    # ================================================================== physics worker (Drone-SLAM mode)
    def start_worker(self) -> dict:
        """Start the Drone-SLAM physics process if configured. On any failure the module stays on the simple flight model."""
        if not self.cfg.worker_python:
            return {}
        try:
            w = WorkerClient(self.cfg.worker_python, self.cfg.worker_device)
            info = w.start()
        except Exception as ex:
            self.worker_ok = False
            return {"error": str(ex)}
        self.worker, self.worker_ok = w, True
        return info

    def close(self):
        if self.worker is not None:
            self.worker.close()
            self.worker_ok = False

    def _make_drone(self, fid, home):
        if self.worker_ok and self.worker is not None and self.worker.alive:
            return PhysicsDrone(self.cfg, f"drn-{fid}", fid, home, fid, self.worker)
        return KDrone(self.cfg, f"drn-{fid}", fid, home, seed=fid)

    # ================================================================== events
    def subscribe(self, fn):
        self.subs.append(fn)

    def emit(self, type_: str, **payload):
        self._seq += 1
        msg = {"type": type_, "seq": self._seq, "t": round(self.clock.t, 2), **payload}
        if self.rec_on and type_ != "snapshot":
            if self._rec_t0 is None:
                self._rec_t0 = self.wall()
            if len(self.recording) < 80_000 and not (type_ == "drone" and self._seq % 5):
                self.recording.append((round(self.wall() - self._rec_t0, 3), msg))
        for fn in list(self.subs):
            try:
                fn(msg)
            except Exception:                                  # a broken subscriber must never stop the simulation
                pass
        return msg

    # ================================================================== binding to a scenario
    def bind(self, model, layer, calls: Calls | None = None, p_edge=None):
        """(Re)build everything for the scenario now on the map. Called on first use and whenever a new run or area appears."""
        if calls is not None:
            self.calls = calls
        cfg = self.cfg
        if model.last is None:
            raise ServiceError("needs_run", "Run a simulation first.")
        was = self.bound
        self.bound = False                                        # handlers see 'not started' (not a half-built scenario) while we rebuild
        self.model, self.layer = model, layer
        b = model.bounds
        self.frame = AreaFrame((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
        self.index = AreaIndex(model, self.frame, cfg)
        self.blocks = Blocks(self.index, cfg)
        self.roads = RoadIndex(layer.net, self.frame) if layer is not None else None
        road_id = layer.net.road_id if layer is not None else None
        self.p_edge = p_edge if p_edge is not None else (np.zeros(layer.net.n_edges) if layer is not None else None)
        self.truth = Truth(self.index, model, cfg, cfg.seed, self.p_edge, road_id)
        self.world = World(self.index, self.truth, cfg, self.roads)
        self.scan = ScanState(self.index, self.truth, cfg)
        self.neigh = Neighbourhoods(model)
        self.feedback = DroneUpdated(model, cfg)
        tod = model.scenario.get("time_of_day", "night")
        self.occ = model.occ_by.get(tod, model.occ_by["night"])
        self.run_id = model.run_id
        self.clock.reset(); self.recording = []; self._rec_t0 = None
        self.task_state, self.extra, self.detections = {}, {}, {}
        self._reach_cache = {}
        self._last_refresh = -1e9
        self.seen_chunks, self.road_seen, self._pending = set(), {}, {}
        self._build_fleet()
        self.bound = True
        self._queue_dirty, self._next_auto, self.model_totals = True, 0.0, None
        if was:
            self.emit("reset", reason="scenario changed: drones recalled")
        self.cloud = []
        self.rec_snapshot = self.snapshot()
        self.emit("snapshot", **self.rec_snapshot)

    def _build_fleet(self):
        cfg = self.cfg
        self.units.clear(); self.stations.clear()
        layer = self.layer
        if layer is None:
            return
        st = layer.facility_state()
        pol = [i for i in range(len(st)) if st.kind.iat[i] == "police"]
        centres = [self.frame.to_local(lon, lat) for lat, lon in (self.neigh.centre[r] for r in sorted(self.neigh.centre))]
        cx = np.array([float(c[0]) for c in centres]) if centres else np.zeros(1)
        cy = np.array([float(c[1]) for c in centres]) if centres else np.zeros(1)
        rows = []
        for i in pol:
            x, y = (float(v) for v in self.frame.to_local(float(st.lon.iat[i]), float(st.lat.iat[i])))
            working = (not bool(st.may_not_work.iat[i])) and int(layer.fnode[i]) >= 0
            rows.append((float(np.min(np.hypot(cx - x, cy - y))), i, x, y, working))
        rows.sort()
        chosen, ok_chosen = [], []
        for d, i, x, y, working in rows:                        # best placed first, spread out
            if working and len(ok_chosen) < cfg.max_stations and all(math.hypot(x - a[2], y - a[3]) > 800 for a in ok_chosen):
                ok_chosen.append((d, i, x, y, working))
        bad = next(((d, i, x, y, w) for d, i, x, y, w in rows if not w), None)
        chosen = ok_chosen + ([bad] if bad and cfg.offline_example else [])
        for d, i, x, y, working in chosen:
            fid = int(st.id.iat[i])
            name = layer.label(i)
            lon, lat = float(st.lon.iat[i]), float(st.lat.iat[i])
            s = {"id": fid, "name": name, "lon": lon, "lat": lat, "x": x, "y": y, "available": working,
                 "reason": None if working else "station is in a building that is likely damaged",
                 "spare": cfg.spare_batteries, "charging": [], "drone_id": f"drn-{fid}", "p_damaged": float(st.p_damaged.iat[i])}
            self.stations.append(s)
            kd = self._make_drone(fid, (x, y))
            u = Unit(s["drone_id"], s, kd, ground="ready" if working else "offline",
                     role="camera" if (cfg.camera_drone and working and not any(x.role == "camera" for x in self.units.values())) else "auto")
            if not working:
                kd.status = "offline"
            self.units[u.id] = u

    # ================================================================== the tick
    def tick(self, wall_dt: float, wall_now: float | None = None):
        if not self.bound:
            return
        wall_now = self.wall() if wall_now is None else wall_now
        if self.worker_ok and self.worker is not None and not self.worker.alive:
            self.worker_ok = False
            self.emit("notice", level="warn", message="The physics worker stopped. Drones continue with the simple flight model.")
        self._check_manual_clock()
        dt = self.clock.advance(wall_dt)
        if dt > 0:
            n = max(1, math.ceil(dt / self.cfg.max_sim_substep_s))
            h = dt / n
            t0 = self.clock.t - dt
            for k in range(n):
                self._substep(h, t0 + (k + 1) * h, wall_now)
        self._ground(self.clock.t)
        self._auto(self.clock.t)
        self._bump(self.clock.t)
        self._housekeeping(self.clock.t)
        self._flush(wall_now)

    def _substep(self, h, t, wall_now):
        for u in self.units.values():
            kd = u.kd
            if not kd.airborne:
                continue
            self._charging_ready(t)
            for e in kd.step(h, t, wall_now, self.world):
                self._on_event(u, e, t)
            if kd.scanning:
                self._scan(u, h, t)
            self.seen_chunks.update(self._chunks_near(kd.pos[0], kd.pos[1], 150.0))

    def _chunks_near(self, x, y, r):
        s = self.cfg.chunk_m
        return [(i, j) for i in range(int(math.floor((x - r) / s)), int(math.floor((x + r) / s)) + 1)
                for j in range(int(math.floor((y - r) / s)), int(math.floor((y + r) / s)) + 1)]

    # ------------------------------------------------------------------ drone events
    def _on_event(self, u: Unit, e: dict, t: float):
        k = e["type"]
        if k == "alarm":
            self.emit("alarm", drone=u.id, code=e["code"], message=e["message"])
            if e["code"] == "RTH_FORCED":
                self._abort_tasks(u)
        elif k == "task_done":
            self._task_done(u, e["task_id"], t)
        elif k == "status":
            if e["status"] == "landed":
                self._on_landed(u, t)
            elif e["status"] in ("lost", "crashed"):
                self._abort_tasks(u)
                self.emit("drone_status", drone=u.id, status=e["status"])
        elif k == "mode":
            self.emit("mode", drone=u.id, mode=e["mode"], reason=e.get("reason"))
            if e["mode"] == "rth":
                self._abort_tasks(u)
        elif k == "sortie":
            self.emit("sortie_event", drone=u.id, **{kk: v for kk, v in e.items() if kk != "type"})

    def _task_done(self, u: Unit, tid: str, t: float):
        st = self.task_state.setdefault(tid, {})
        st.update(status="done", drone=u.id, done_t=t)
        self._queue_dirty = True
        task = self.extra.get(tid)
        if task and task["type"] == "verify_sos":
            hit = any(d["status"] in ("merged", "unverified", "confirmed") and d.get("sos_id") == task.get("sos_id") for d in self.detections.values())
            if not hit and task.get("sos_id") is not None:
                self.calls.tag(task["sos_id"], "drone_checked_empty")
                self.emit("verify_result", call=task["sos_id"], corroborated=False)
        self.emit("task", id=tid, status="done", drone=u.id)

    def _abort_tasks(self, u: Unit):
        if u.plan:
            for tid in u.plan["tasks"]:
                st = self.task_state.get(tid)
                if st and st.get("status") in ("planned", "active"):
                    st.update(status="queued", drone=None)
        self._queue_dirty = True

    def _on_landed(self, u: Unit, t: float):
        self._abort_tasks(u)
        u.plan = None
        u.kd.bias[:] = 0.0
        self.emit("drone_status", drone=u.id, status="landed")
        self._begin_swap(u, t)
        self._queue_dirty = True

    def _begin_swap(self, u: Unit, t: float):
        s = u.station
        self._charging_ready(t)
        u.ground = "swapping"
        u.swap_until = t + self.cfg.swap_time_s
        self.emit("station", **self._station_msg(s))

    def _charging_ready(self, t: float):
        for s in self.stations:
            ready = [c for c in s["charging"] if c <= t]
            if ready:
                s["charging"] = [c for c in s["charging"] if c > t]
                s["spare"] += len(ready)

    # ------------------------------------------------------------------ ground lifecycle
    def _ground(self, t: float):
        for u in self.units.values():
            if u.kd.airborne or u.kd.status in ("lost", "crashed", "offline"):
                continue
            if u.ground == "swapping" and t >= (u.swap_until or 0):
                self._charging_ready(t)
                if u.station["spare"] > 0:
                    u.station["spare"] -= 1
                    u.station["charging"].append(t + self.cfg.recharge_time_s)
                    u.kd.battery_wh = self.cfg.battery_wh
                    u.kd.status, u.ground = "ready", "ready"
                    self.emit("drone_status", drone=u.id, status="ready")
                else:
                    u.ground = "waiting_battery"
                self.emit("station", **self._station_msg(u.station))
            elif u.ground == "waiting_battery":
                self._charging_ready(t)
                if u.station["spare"] > 0:
                    u.station["spare"] -= 1
                    u.station["charging"].append(t + self.cfg.recharge_time_s)
                    u.kd.battery_wh = self.cfg.battery_wh
                    u.kd.status, u.ground = "ready", "ready"
                    self.emit("drone_status", drone=u.id, status="ready")
                    self.emit("station", **self._station_msg(u.station))
            elif u.ground == "launching" and u.launch_at is not None and t >= u.launch_at:
                sortie = u.plan
                u.launch_at = None
                if sortie is None:
                    u.ground = "ready"; continue
                u.ground = "airborne"
                u.kd.launch(sortie["route"], sortie["id"])
                if u.role == "camera" and not sortie["tasks"]:
                    u.kd.mode = "assisted"                       # a free launch ends by hovering over the station until a pilot takes over, not by flying home
                for tid in sortie["tasks"]:
                    self.task_state.setdefault(tid, {}).update(status="active", drone=u.id)
                self.emit("sortie_event", drone=u.id, phase="launched", sortie_id=sortie["id"])
                self.emit("route", **self._route_msg(u))
                self._queue_dirty = True

    def _station_msg(self, s):
        return {"station": s["id"], "name": s["name"], "available": s["available"], "reason": s["reason"], "spare": s["spare"],
                "charging": [round(c, 1) for c in s["charging"]], "drone": s["drone_id"], "lon": s["lon"], "lat": s["lat"]}

    # ================================================================== scanning, detections, roads
    def _scan(self, u: Unit, h: float, t: float):
        kd, cfg = u.kd, self.cfg
        x, y, z = (float(v) for v in kd.pos)
        speed = math.hypot(kd.vel[0], kd.vel[1])
        r = footprint_radius(cfg, z)
        res = self.scan.view(x, y, z, speed, h, t, u.id)
        pend = self._pending
        if res["changed"]:
            pend.setdefault("buildings", set()).update(res["changed"])
        if res["cells"]:
            pend.setdefault("cells", []).extend(res["cells"])
        for b, o in res["observations"]:
            info = self.feedback.observe(b, o)
            pend.setdefault("observations", []).append({"building": b, "observed": TRUTH_NAME[o], "by": u.id, "before": round(info["before"], 3), "after": round(info["after"], 3)})
            self._queue_dirty = True
        # roads under the camera
        if self.roads is not None and len(self.truth.road_blocked):
            for e in self.roads.near(x, y, r):
                rid = int(self.roads.road_id[e])
                if rid not in self.road_seen:
                    self.road_seen[rid] = bool(self.truth.road_blocked[rid])
                    pend.setdefault("roads", []).append(rid)
        # detections: one tick per detect_tick_s of simulated time
        u.det_acc += h; u.det_area += 2 * r * speed * h
        while u.det_acc >= cfg.detect_tick_s:
            u.det_acc -= cfg.detect_tick_s
            for c in self.scan.detect(x, y, z, speed, u.det_area, t, self.rng):
                self._add_detection(c, u.id, t)
            u.det_area = 0.0

    def _add_detection(self, c: dict, drone_id: str, t: float):
        cfg = self.cfg
        # duplicates merge into one
        for d in self.detections.values():
            if d["status"] == "rejected":
                continue
            near = math.hypot(d["x"] - c["x"], d["y"] - c["y"])
            same_b = c["building"] is not None and d["building"] == c["building"] and near < 25.0
            if (near <= cfg.merge_radius_m and t - d["t_last"] <= cfg.merge_window_s) or same_b:
                d["confidence"] = max(d["confidence"], c["confidence"]); d["est_people"] = max(d["est_people"], c["est_people"])
                d["t_last"] = t; d["sightings"] += 1
                self.emit("detection", **self._det_msg(d))
                return
        # a phone SOS call nearby is corroborated, not duplicated
        lat_lon = self.frame.to_lonlat(c["x"], c["y"])
        for oc in self.calls.open_calls():
            ox, oy = self.frame.to_local(oc["lon"], oc["lat"])
            if math.hypot(float(ox) - c["x"], float(oy) - c["y"]) <= cfg.sos_merge_radius_m and oc.get("source") != "drone":
                did = self._new_id("det", "d")
                d = {"id": did, "drone": drone_id, "t": t, "t_last": t, "x": c["x"], "y": c["y"], "lon": float(lat_lon[0]), "lat": float(lat_lon[1]),
                     "building": c["building"], "confidence": c["confidence"], "est_people": c["est_people"], "status": "merged", "call_id": oc["id"],
                     "sos_id": oc["id"], "sightings": 1, "true": c["true"]}
                self.detections[did] = d
                self.calls.corroborate(oc["id"], did)
                self.emit("detection", **self._det_msg(d))
                return
        did = self._new_id("det", "d")
        d = {"id": did, "drone": drone_id, "t": t, "t_last": t, "x": c["x"], "y": c["y"], "lon": float(lat_lon[0]), "lat": float(lat_lon[1]), "building": c["building"],
             "confidence": c["confidence"], "est_people": c["est_people"], "status": "unverified", "call_id": None, "sos_id": None, "sightings": 1, "true": c["true"]}
        self.detections[did] = d
        self._queue_dirty = True
        self.emit("detection", **self._det_msg(d))
        if cfg.auto_confirm_threshold and d["confidence"] >= cfg.auto_confirm_threshold:
            self.confirm_detection(did, auto=True)

    def _det_msg(self, d):      # the 'true' flag is deliberately NOT sent
        return {k: (round(v, 6) if isinstance(v, float) else v) for k, v in d.items() if k not in ("true", "x", "y")} | {"x": round(d["x"], 1), "y": round(d["y"], 1)}

    def confirm_detection(self, did: str, auto: bool = False) -> dict:
        d = self._det(did)
        if d["status"] != "unverified":
            raise ServiceError("bad_state", f"This detection is already {d['status']}.")
        b = d["building"]
        cid = self.calls.create_call(d["lat"], d["lon"], d["est_people"], d["confidence"],
                                     f"Drone sighting, confidence {round(d['confidence'] * 100)}%" + (" (auto-confirmed)" if auto else ""), b)
        d.update(status="confirmed", call_id=cid, sos_id=cid)
        self._queue_dirty = True
        self.emit("detection", **self._det_msg(d))
        return self._det_msg(d)

    def reject_detection(self, did: str) -> dict:
        d = self._det(did)
        if d["status"] not in ("unverified",):
            raise ServiceError("bad_state", f"This detection is already {d['status']}.")
        d["status"] = "rejected"
        self._queue_dirty = True
        self.emit("detection", **self._det_msg(d))
        return self._det_msg(d)

    def _det(self, did):
        d = self.detections.get(did)
        if d is None:
            raise ServiceError("not_found", "No such detection.", 404)
        return d

    def _new_id(self, kind, prefix):
        self._ids[kind] += 1
        return f"{prefix}-{self._ids[kind]:04d}"

    # ================================================================== priority queue
    def p_current(self):
        if getattr(self, "_p_cache", None) is None or self.feedback.dirty:
            self._p_cache = self.feedback.probabilities()
            self.feedback.dirty = False
        return self._p_cache

    def _sos_points(self):
        pts = []
        for c in self.calls.open_calls():
            x, y = self.frame.to_local(c["lon"], c["lat"])
            pts.append((float(x), float(y), float(c.get("weight", 1.0))))
        for d in self.detections.values():
            if d["status"] == "unverified":
                pts.append((d["x"], d["y"], d["confidence"]))
        return pts

    def refresh_queue(self, force=False):
        """Rebuild the priority queue. Scanning marks it dirty constantly, so unforced rebuilds are limited to about one a second."""
        now = self.wall()
        if not force and (not self._queue_dirty or now - self._last_refresh < 1.0):
            return self.queue_cache
        self._last_refresh = now
        cfg = self.cfg
        pc = self.p_current()[:, 3]
        vals = self.blocks.values(pc, self.occ, self.scan.conf, self._sos_points())
        avail = [s for s in self.stations if s["available"]]
        order = np.argsort(-vals["value"])
        cand, nearest = [], np.array([[s["x"], s["y"]] for s in avail]) if avail else None
        for k in order[:400]:
            if not self.blocks.active[k] or vals["value"][k] <= 0.05:
                continue
            tid = self.blocks.id(k)
            if tid in self.extra:
                continue
            stt = self.task_state.get(tid, {})
            if stt.get("status") == "done" and vals["value"][k] < 1.0:
                continue
            cx, cy = self.blocks.centre[k]
            if any(b.contains(Point(cx, cy)) for b in self.bans):
                continue
            d = float(np.min(np.hypot(nearest[:, 0] - cx, nearest[:, 1] - cy))) if nearest is not None else 1e9
            cand.append((vals["value"][k] / (d + 600.0), k))
            if len(cand) >= 70:
                break
        cand.sort(reverse=True)
        out = []
        for _, k in cand[:50]:
            task = P.block_task(cfg, self.blocks, int(k), vals)
            out.append(self._decorate(task, avail))
        for tid, task in self.extra.items():
            stt = self.task_state.get(tid, {})
            if stt.get("status") in ("done", "cancelled"):
                continue
            out.append(self._decorate(dict(task), avail))
        self.queue_cache = sorted(out, key=lambda t: (t["manual_rank"] if t["manual_rank"] is not None else 1e9, -t["score"]))
        self._queue_dirty = False
        return self.queue_cache

    def _decorate(self, task, avail):
        cfg = self.cfg
        st = self.task_state.get(task["id"], {})
        task["pinned"] = bool(st.get("pinned")); task["skipped"] = bool(st.get("skipped"))
        task["manual_rank"] = st.get("manual_rank", -1 if task["pinned"] else None)
        task["status"] = st.get("status", "queued"); task["drone"] = st.get("drone")
        key = (task["id"], tuple(sorted(s["id"] for s in avail)), round(task["alt"]))
        hit = self._reach_cache.get(key)
        if hit is None:                                            # feasibility from a full battery depends only on the task and the stations
            reach, best_e, reason = [], None, "no_station"
            Z = self._transit_alt([task])
            for s in avail:
                f = P.single_feasibility(cfg, (s["x"], s["y"]), task, cfg.battery_wh, Z, cfg.wind_ms)
                if f["feasible"]:
                    reach.append(s["id"])
                if best_e is None or f["energy_wh"] < best_e[0]:
                    best_e = (f["energy_wh"], f["duration_s"])
                    reason = None if f["feasible"] else f["reason"]
            hit = self._reach_cache[key] = (reach, best_e, reason)
            if len(self._reach_cache) > 4000:
                self._reach_cache.clear()
        reach, best_e, reason = hit
        task["reachable_from"] = reach
        task["reason"] = None if reach else reason
        e = best_e[0] if best_e else 0.0
        task["est_energy_wh"] = round(e, 1); task["est_duration_s"] = round(best_e[1] if best_e else 0.0)
        task["score"] = task["value"] / max(e, 5.0)
        return task

    def public_task(self, t: dict, why_on: bool = True) -> dict:
        x, y = t["centre"]
        lon, lat = self.frame.to_lonlat(x, y)
        why = None
        if why_on and t["type"] == "survey_block":
            pc = self.p_current()[:, 3]
            top = self.blocks.top_buildings(t["block"], pc, self.occ, self.scan.conf)
            why = [{"building": b, "p_collapse": round(float(pc[b]), 3), "occupants": int(self.occ[b]), "scan": round(float(self.scan.conf[b]), 2)} for b in top]
        return {"id": t["id"], "type": t["type"], "status": t["status"], "pinned": t["pinned"], "skipped": t["skipped"], "manual_rank": t["manual_rank"],
                "value": round(t["value"], 2), "score": round(t["score"], 4), "est_energy_wh": t["est_energy_wh"], "est_duration_s": t["est_duration_s"],
                "components": {k: round(v, 2) for k, v in t["components"].items()}, "reachable_from": t["reachable_from"], "reason": t["reason"],
                "drone": t["drone"], "lon": float(lon), "lat": float(lat), "buildings": t.get("buildings", 0), "too_tall": t["too_tall"], "alt": round(t["alt"]), "why": why}

    def queue(self, limit=40, why=True):
        return [self.public_task(t, why) for t in self.refresh_queue()[:limit]]

    def task_why(self, tid: str):
        t = next((t for t in self.refresh_queue() if t["id"] == tid), None)
        if t is None:
            raise ServiceError("not_found", "That task is no longer in the queue.", 404)
        return self.public_task(t, True)["why"]

    def emit_queue(self, force=False):
        """Broadcast the top of the queue without the 'why' detail, and only when something a viewer can see has changed."""
        q = self.queue(24, False)
        sig = hash(tuple((t["id"], t["status"], round(t["value"], 1), t["pinned"], t["skipped"], t["drone"], t["manual_rank"], t["est_energy_wh"]) for t in q))
        if force or sig != getattr(self, "_queue_sig", None):
            self._queue_sig = sig
            self.emit("queue", tasks=q)

    # ------------------------------------------------------------------ queue edits
    def patch_task(self, tid: str, pinned=None, skipped=None, manual_rank=None) -> dict:
        self._last_refresh = -1e9                                  # operator edits show at once
        st = self.task_state.setdefault(tid, {})
        if pinned is not None:
            st["pinned"] = bool(pinned)
            if pinned:
                st["skipped"] = False
        if skipped is not None:
            st["skipped"] = bool(skipped)
            if skipped:
                st["pinned"] = False
        if manual_rank is not None:
            st["manual_rank"] = manual_rank
        self._queue_dirty = True
        self.emit_queue(True)
        return {"ok": True}

    def cancel_task(self, tid: str):
        self._last_refresh = -1e9
        if tid in self.extra:
            self.task_state.setdefault(tid, {})["status"] = "cancelled"
        else:
            self.task_state.setdefault(tid, {})["skipped"] = True
        self._queue_dirty = True
        self.emit_queue(True)

    def ban_zone(self, lonlat_poly) -> int:
        self._last_refresh = -1e9
        self.bans.append(Polygon(self.frame.polygon_to_local(lonlat_poly)))
        self._queue_dirty = True
        self.emit_queue(True)
        return len(self.bans) - 1

    def clear_bans(self):
        self._last_refresh = -1e9
        self.bans.clear(); self._queue_dirty = True

    # ================================================================== task creation and launching
    def _transit_alt(self, tasks, home=None) -> float:
        cfg = self.cfg
        top = max([t["wps"][0]["z"] for t in tasks] + [cfg.min_alt_m + 10.0])
        pts = [t["centre"] for t in tasks] + ([home] if home else [])
        if pts:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            ids = self.index.within((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, max(max(xs) - min(xs), max(ys) - min(ys)) / 2 + 150.0)
            tall = float(self.index.height[ids].max()) if len(ids) else 0.0
            top = max(top, tall + cfg.clearance_m)
        return float(min(max(top, cfg.min_alt_m + 10.0), cfg.max_alt_m))

    def make_task(self, spec: dict) -> dict:
        """Build a task from a request. spec: {type, lon, lat, building?, call_id?, polygon?, edge?}."""
        cfg = self.cfg
        kind = spec["type"]
        tid = self._new_id("task", "t")
        if kind == "inspect_building":
            b = int(spec["building"])
            x, y = float(self.index.x[b]), float(self.index.y[b])
            ring = float(min(math.hypot(self.index.half_x[b], self.index.half_y[b]) + 20.0, 24.0))      # inside the camera footprint (30 m)
            alt = float(self.index.height[b] + cfg.clearance_m)
            return P.point_task(cfg, kind, x, y, tid, alt=max(alt, cfg.min_alt_m), value=cfg.w_d, building=b, ring_r=ring)
        if kind == "verify_sos":
            c = self.calls.get_call(int(spec["call_id"]))
            if c is None:
                raise ServiceError("not_found", "No such call.", 404)
            x, y = (float(v) for v in self.frame.to_local(c["lon"], c["lat"]))
            return P.point_task(cfg, kind, x, y, tid, alt=cfg.min_alt_m, dwell_s=cfg.verify_dwell_s, value=cfg.w_sos * c.get("weight", 1.0), sos_id=int(spec["call_id"]))
        if kind == "goto":
            x, y = (float(v) for v in self.frame.to_local(spec["lon"], spec["lat"]))
            return P.point_task(cfg, kind, x, y, tid, alt=float(spec.get("agl", cfg.min_alt_m)), value=1.0)
        if kind == "survey_block":
            if spec.get("polygon"):
                poly = Polygon(self.frame.polygon_to_local(spec["polygon"]))
                if poly.is_empty or poly.area < 400:
                    raise ServiceError("bad_area", "Draw a larger area.", 400)
                x0, y0, x1, y1 = poly.bounds
                ids = self.index.within((x0 + x1) / 2, (y0 + y1) / 2, math.hypot(x1 - x0, y1 - y0) / 2 + 1)
                inside = [i for i in ids if poly.contains(Point(self.index.x[i], self.index.y[i]))]
                if not inside:
                    raise ServiceError("no_buildings", "That area contains no buildings.", 400)
                alt, tall = P.block_alt(cfg, float(self.index.height[inside].max()))
                path = [(x, y) for x, y in P.survey_path(cfg, (x0, y0, x1, y1))]
                wps = P.survey_wps(cfg, path, alt, tid)
                v = float(sum(self.occ[inside] * self.p_current()[inside, 3]))
                return {"id": tid, "type": "survey_block", "block": None, "alt": alt, "too_tall": tall, "wps": wps, "entry": path[0], "exit": path[-1],
                        "centre": ((x0 + x1) / 2, (y0 + y1) / 2), "rect": [x0, y0, x1, y1], "value": max(v, 1.0),
                        "components": {"victims": v, "info": 0.0, "sos": 0.0}, "buildings": len(inside)}
            k = self.blocks.block_of(*(float(v) for v in self.frame.to_local(spec["lon"], spec["lat"])))
            if k is None:
                raise ServiceError("no_buildings", "No buildings there.", 400)
            vals = self.blocks.values(self.p_current()[:, 3], self.occ, self.scan.conf, self._sos_points())
            t = P.block_task(cfg, self.blocks, k, vals, tid=self.blocks.id(k))
            return t
        if kind == "check_road":
            e = int(spec["edge"])
            pl = self.roads.polyline(e)
            return P.line_task(cfg, tid, [tuple(p) for p in pl], cfg.min_alt_m, value=cfg.w_d, edge=e)
        raise ServiceError("bad_type", f"Unknown task type {kind}.", 400)

    def preview(self, spec: dict) -> dict:
        """Feasibility of a task from each available station with a ready (or soon ready) drone."""
        cfg = self.cfg
        task = self.make_task(spec)
        Z = self._transit_alt([task])
        options = []
        for u in self.units.values():
            if not u.station["available"] or u.kd.status in ("lost", "crashed"):
                continue
            if u.kd.airborne or u.role == "camera":                      # the camera drone is flown by hand, never sent on a sortie
                continue
            f = P.single_feasibility(cfg, (u.station["x"], u.station["y"]), task, u.kd.battery_wh if u.ground == "ready" else cfg.battery_wh, Z, cfg.wind_ms)
            wait = 0.0 if u.ground == "ready" else max(0.0, (u.swap_until or self.clock.t) - self.clock.t)
            options.append({"drone": u.id, "station": u.station["name"], "station_id": u.station["id"], "ground": u.ground, "wait_s": round(wait),
                            "distance_m": round(math.hypot(task["entry"][0] - u.station["x"], task["entry"][1] - u.station["y"])), **{k: (round(v, 1) if isinstance(v, float) else v) for k, v in f.items()}})
        options.sort(key=lambda o: (not o["feasible"], o["wait_s"], o["energy_wh"]))
        feasible = [o for o in options if o["feasible"]]
        reasons = []
        if not options:
            reasons.append("no station with a working drone")
        elif not feasible:
            reasons.append("out of range of every available station" if all(o["reason"] == "out_of_range" for o in options) else "not enough battery")
        return {"type": spec["type"], "task_id": task["id"], "feasible": bool(feasible), "best": feasible[0] if feasible else None, "options": options[:5],
                "reasons": reasons, "alt_m": round(task["alt"]), "too_tall": task.get("too_tall", False), "value": round(task["value"], 2)}

    def create_task(self, spec: dict, pinned: bool = True) -> dict:
        """Create an operator task and send the best ready drone. Raises ServiceError with a reason when it cannot fly."""
        pv = self.preview(spec)
        task = self.make_task(spec)
        if not pv["feasible"]:
            raise ServiceError("infeasible", "; ".join(pv["reasons"]) or "cannot fly this task", 409)
        best = next((o for o in pv["options"] if o["feasible"] and o["ground"] == "ready"), None)
        if best is None:
            wait = pv["best"]
            raise ServiceError("no_ready_drone", f"{wait['station']}'s drone is {wait['ground']}; try again in {wait['wait_s']} s" if wait else "no ready drone", 409)
        self.extra[task["id"]] = task
        self.task_state[task["id"]] = {"pinned": pinned, "status": "planned", "drone": best["drone"]}
        u = self.units[best["drone"]]
        self._launch_with(u, [task], pinned=(task["id"],))
        self._queue_dirty = True
        return {"task": task["id"], "drone": u.id, "preview": pv}

    def _launch_with(self, u: Unit, tasks, pinned=()):
        cfg = self.cfg
        home = (u.station["x"], u.station["y"])
        Z = self._transit_alt(tasks, home)
        s = P.plan_sortie(cfg, home, u.kd.battery_wh, tasks, Z, cfg.wind_ms, pinned=pinned)
        if s is None:
            raise ServiceError("infeasible", "That sortie does not fit the battery.", 409)
        self._commit_sortie(u, s)
        return s

    def _commit_sortie(self, u: Unit, s: dict):
        sid = self._new_id("sortie", "s")
        s["id"] = sid
        u.plan = s
        u.ground, u.launch_at = "launching", self.clock.t + self.cfg.launch_delay_s
        u.note = None
        for tid in s["tasks"]:
            self.task_state.setdefault(tid, {}).update(status="planned", drone=u.id)
        self.emit("sortie_plan", drone=u.id, sortie=self._sortie_msg(u, s))
        self.emit("drone_status", drone=u.id, status="launching")

    def _sortie_msg(self, u, s):
        return {"id": s["id"], "tasks": s["tasks"], "energy_wh": round(s["energy_wh"], 1), "duration_s": round(s["duration_s"]), "value": round(s["value"], 1),
                "remaining_wh": round(s["remaining_wh"], 1), "launch_t": round(u.launch_at or 0), "land_t": round((u.launch_at or 0) + s["duration_s"])}

    def launch_best(self, did: str) -> dict:
        """Plan the best sortie for one ready drone from the queue and launch it (works with auto mode off)."""
        u = self._unit(did)
        if u.ground != "ready":
            raise ServiceError("not_ready", f"{did} is {u.status}, not ready.")
        if u.role == "camera":
            return self.launch_free(did)
        s = self._plan_for(u)
        if s is None:
            u.note = "no worthwhile targets in range"
            raise ServiceError("nothing_to_do", "No worthwhile target is in range of this station.", 409)
        self._commit_sortie(u, s)
        return self._sortie_msg(u, s)

    def launch_free(self, did: str) -> dict:
        """Take off and climb to a hover over the station, with no survey task: the pilot (the phone) takes over from there."""
        u = self._unit(did)
        if u.ground != "ready":
            raise ServiceError("not_ready", f"{did} is {u.status}, not ready.")
        cfg = self.cfg
        route = [dict(x=u.station["x"], y=u.station["y"], z=cfg.min_alt_m + 10.0, speed=cfg.climb_rate_ms, scan=False, kind="climb", task_id=None)]
        s = {"route": route, "tasks": [], "task_objs": [], "energy_wh": 0.0, "duration_s": 60.0, "value": 0.0, "remaining_wh": u.kd.battery_wh}
        self._commit_sortie(u, s)
        return self._sortie_msg(u, s)

    def _plan_for(self, u: Unit):
        cfg = self.cfg
        q = [t for t in self.refresh_queue() if not t["skipped"] and t["status"] in ("queued",) and u.station["id"] in t["reachable_from"]]
        if not q:
            return None
        pinned = tuple(t["id"] for t in q if t["pinned"])
        cands = sorted(q, key=lambda t: (t["manual_rank"] if t["manual_rank"] is not None else 1e9, -t["score"]))[:cfg.planner_k]
        home = (u.station["x"], u.station["y"])
        Z = self._transit_alt(cands[:12], home)
        s = P.plan_sortie(cfg, home, u.kd.battery_wh, cands, Z, cfg.wind_ms, pinned=pinned)
        if s is None or (s["value"] < cfg.min_sortie_value and not pinned):
            return None
        return s

    def set_auto(self, on: bool):
        self.auto = bool(on)
        self.emit("auto", enabled=self.auto)

    def _auto(self, t: float):
        if not self.auto or t < self._next_auto:
            return
        self._next_auto = t + self.cfg.auto_tick_s
        for u in self.units.values():
            if u.role == "auto" and u.ground == "ready" and u.station["available"] and u.kd.status == "ready" and u.kd.battery_wh >= 0.95 * self.cfg.battery_wh:
                s = self._plan_for(u)
                if s is None:
                    u.note = "no worthwhile targets in range"
                    continue
                self._commit_sortie(u, s)

    def _bump(self, t: float):
        """A fresh SOS call outranks the model: an airborne auto drone detours to the best SOS task when the battery allows (never
        abandoning a task that is more than half done)."""
        if not self.cfg.sos_bump or t < getattr(self, "_next_bump", 0.0):
            return
        self._next_bump = t + 10.0
        cfg = self.cfg
        sos = [q for q in self.queue_cache if q["components"].get("sos", 0) > 0 and q["status"] == "queued" and not q["skipped"]]
        if not sos:
            return
        for u in self.units.values():
            kd = u.kd
            if kd.mode != "auto" or kd.status not in ("transit", "surveying") or not u.plan or kd.i >= len(kd.route):
                continue
            if any(tt["id"] == q["id"] for q in sos for tt in u.plan["task_objs"]):
                continue
            q = sos[0]
            cur = kd.route[kd.i]
            ids = [w.get("task_id") for w in kd.route if w.get("task_id") == cur.get("task_id")]
            done = sum(1 for w in kd.route[:kd.i] if w.get("task_id") == cur.get("task_id"))
            if cur.get("kind") == "survey" and ids and done / max(done + len(ids), 1) > 0.5:
                continue
            Z = max(float(kd.pos[2]), q["alt"], cfg.min_alt_m + 10.0)
            ex, ey = q["entry"]
            frag = [dict(x=ex, y=ey, z=Z, speed=cfg.cruise_speed_ms, scan=False, kind="transit", task_id=q["id"])]
            if q["wps"][0]["z"] < Z:
                frag.append(dict(x=ex, y=ey, z=q["wps"][0]["z"], speed=cfg.climb_rate_ms, scan=False, kind="descend", task_id=q["id"]))
            frag += [dict(w) for w in q["wps"]]
            lx, ly = q["exit"]
            frag.append(dict(x=lx, y=ly, z=Z, speed=cfg.climb_rate_ms, scan=False, kind="climb", task_id=q["id"]))
            new = frag + [dict(w) for w in kd.route[kd.i:]]
            e = E.route_energy(cfg, new, start=tuple(kd.pos), wind=cfg.wind_ms)[0]
            if kd.battery_wh - e * 1.1 < E.reserve_wh(cfg):
                continue
            kd.route, kd.i, kd._leg = new, 0, None
            u.plan["tasks"].append(q["id"]); u.plan["task_objs"].append(q)
            self.extra.setdefault(q["id"], q) if q["type"] != "survey_block" else None
            self.task_state.setdefault(q["id"], {}).update(status="active", drone=u.id)
            self.emit("sos_bump", drone=u.id, task=q["id"], message="An SOS call jumped the queue")
            self.emit("route", **self._route_msg(u))
            self._queue_dirty = True
            return

    # ================================================================== flying: control and commands
    def _unit(self, did) -> Unit:
        u = self.units.get(did)
        if u is None:
            raise ServiceError("not_found", "No such drone.", 404)
        return u

    def take_control(self, did: str, owner: str = "operator") -> dict:
        u = self._unit(did)
        if not u.kd.airborne:
            raise ServiceError("not_airborne", "The drone is on the ground.")
        if u.kd.owner == "operator" and u.kd.mode == "manual":
            return {"ok": True}
        u.kd.take_control()
        u.kd.manual["t"] = self.wall()                     # the dead-man and link-lost clocks start now, not at the epoch
        self._abort_tasks(u)
        self._check_manual_clock()
        self.emit("mode", drone=u.id, mode="manual", reason="operator took control")
        return {"ok": True}

    def release(self, did: str, then: str = "auto") -> dict:
        u = self._unit(did)
        for e in u.kd.release(then):
            self._on_event(u, e, self.clock.t)
        self._check_manual_clock()
        self.emit("mode", drone=u.id, mode=u.kd.mode, reason="released")
        return {"ok": True, "mode": u.kd.mode}

    def manual_input(self, did, vx, vy, vz, yr, wall_now=None, assist=True):
        u = self._unit(did)
        if u.kd.mode != "manual":
            return False
        if self.clock.paused:
            return False
        u.kd.manual_input(vx, vy, vz, yr, self.wall() if wall_now is None else wall_now, assist)
        return True

    def return_home(self, did: str, reason="operator"):
        u = self._unit(did)
        if not u.kd.airborne:
            raise ServiceError("not_airborne", "The drone is on the ground.")
        if u.kd.status == "landing" or (u.kd.mode == "rth" and reason != "battery reserve"):
            return                                                 # already on its way down / home
        for e in u.kd.start_rth(reason):
            self._on_event(u, e, self.clock.t)
        self.emit("route", **self._route_msg(u))
        self._check_manual_clock()

    def hold(self, did: str):
        u = self._unit(did)
        u.kd.hold()
        self.emit("mode", drone=u.id, mode=u.kd.mode, reason="hold")

    def goto(self, did: str, lon: float, lat: float, agl: float | None = None):
        u = self._unit(did)
        if not u.kd.airborne:
            raise ServiceError("not_airborne", "The drone is on the ground.")
        if u.kd.mode == "rth":
            raise ServiceError("returning", "The drone is returning home and cannot take a new target.")
        x, y = (float(v) for v in self.frame.to_local(lon, lat))
        z = float(agl if agl is not None else max(u.kd.pos[2], self.cfg.min_alt_m))
        route = [dict(x=x, y=y, z=z, speed=self.cfg.cruise_speed_ms, scan=True, kind="transit")]
        e_go = E.route_energy(self.cfg, route, start=tuple(u.kd.pos), wind=self.cfg.wind_ms)[0]
        e_back = E.energy_to_home(self.cfg, x, y, z, u.station["x"], u.station["y"])
        if u.kd.battery_wh - e_go - e_back < E.reserve_wh(self.cfg):
            raise ServiceError("infeasible", "Not enough battery to go there and come back.")
        u.kd.follow(route, "assisted"); u.kd.owner = "auto"
        self.emit("route", **self._route_msg(u))

    def _check_manual_clock(self):
        """The demo clock has to slow down while a pilot flies (manual) or while a physics (L1) drone is in the air; it is restored after."""
        air = [u for u in self.units.values() if u.kd.airborne]
        need, why = None, ""
        if any(u.kd.mode == "manual" for u in air):
            need, why = self.cfg.manual_max_clock, "a pilot is flying"
        if any(getattr(u.kd, "fidelity", "L0") == "L1" and getattr(u.kd, "phys_ok", False) for u in air):
            cap = self.cfg.l1_clock_cap
            if need is None or cap < need:
                need, why = cap, "the physics drone is flying"
        cur = self.calls.speed()
        if need is not None and cur > need:
            if self._prev_speed is None:
                self._prev_speed = cur
            self.calls.set_speed(need)
            self.emit("notice", level="info", message=f"Demo clock set to {need:g}x while {why}.")
        elif need is None and self._prev_speed is not None:
            self.calls.set_speed(self._prev_speed)
            self._prev_speed = None
            self.emit("notice", level="info", message="Demo clock restored.")

    def recall_all(self, reason="scenario changed"):
        for u in self.units.values():
            if u.kd.airborne:
                self.return_home(u.id, reason)

    # ================================================================== reporting and flushing
    def drone_msg(self, u: Unit) -> dict:
        kd, cfg = u.kd, self.cfg
        x, y, z = (float(v) for v in kd.pos)
        lon, lat = self.frame.to_lonlat(x, y)
        e_home = kd.e_home() if kd.airborne else 0.0
        dist_home = math.hypot(x - kd.home[0], y - kd.home[1])
        return {"id": u.id, "role": u.role, "label": "Camera drone" if u.role == "camera" else "Auto drone", "station": u.station["id"], "status": u.status, "mode": kd.mode if kd.airborne else "idle", "owner": kd.owner,
                "x": round(x, 2), "y": round(y, 2), "z": round(z, 2), "yaw": round(kd.yaw, 3), "hdg": round(heading_deg(kd.yaw), 1), "lon": float(lon), "lat": float(lat),
                "vx": round(float(kd.vel[0]), 2), "vy": round(float(kd.vel[1]), 2), "vz": round(float(kd.vel[2]), 2), "speed": round(math.hypot(kd.vel[0], kd.vel[1]), 2),
                "battery_wh": round(kd.battery_wh, 2), "pct": round(100 * kd.battery_wh / cfg.battery_wh, 1), "e_home_wh": round(e_home, 1),
                "mins_left": round(max(0.0, kd.battery_wh - E.reserve_wh(cfg)) / max(E.power_w(cfg, max(math.hypot(kd.vel[0], kd.vel[1]), 0.0), 0.0), 1.0) * 60.0, 1) if kd.airborne else None,
                "no_return_m": round(E.no_return_radius_m(cfg, kd.battery_wh, dist_home), 0) if kd.airborne else None,
                "sortie": u.plan["id"] if u.plan else None, "task": kd.task_id, "assist": kd.assist, "assist_active": kd.assist_active, "nearest_m": round(kd.nearest_m, 1),
                "bias": [round(float(v), 2) for v in kd.bias], "dist_home_m": round(dist_home), "note": u.note, "fidelity": kd.fidelity,
                "launch_in_s": round(max(0.0, (u.launch_at or 0) - self.clock.t)) if u.ground == "launching" else None,
                "swap_in_s": round(max(0.0, (u.swap_until or 0) - self.clock.t)) if u.ground == "swapping" else None, "home": [round(kd.home[0], 1), round(kd.home[1], 1)]}

    def _route_msg(self, u: Unit) -> dict:
        kd = u.kd
        pts = [(kd.pos[0], kd.pos[1], kd.pos[2])] + [(w["x"], w["y"], w["z"]) for w in kd.route[kd.i:]] if kd.airborne else [(w["x"], w["y"], w["z"]) for w in (u.plan or {}).get("route", [])]
        if not pts:
            return {"drone": u.id, "pts": []}
        a = np.asarray(pts, float)
        lon, lat = self.frame.to_lonlat(a[:, 0], a[:, 1])
        return {"drone": u.id, "pts": [[round(float(p), 6), round(float(q), 6), round(float(z), 1)] for p, q, z in zip(lon, lat, a[:, 2])],
                "xy": [[round(float(x), 1), round(float(y), 1)] for x, y in zip(a[:, 0], a[:, 1])]}

    def cone_msg(self, u: Unit):
        kd = u.kd
        r = footprint_radius(self.cfg, float(kd.pos[2]))
        a = np.linspace(0, 2 * np.pi, 20, endpoint=False)
        lon, lat = self.frame.to_lonlat(kd.pos[0] + r * np.cos(a), kd.pos[1] + r * np.sin(a))
        ring = [[round(float(p), 6), round(float(q), 6)] for p, q in zip(lon, lat)]
        return {"drone": u.id, "ring": ring + [ring[0]], "radius": round(r, 1), "scanning": bool(kd.scanning)}

    def _flush(self, wall_now: float):
        period = 1.0 / max(self.cfg.telemetry_hz, 1.0)
        if wall_now - self._last_flush >= period:
            self._last_flush = wall_now
            self._n_flush = getattr(self, "_n_flush", 0) + 1
            if self._n_flush % 5 == 1:
                self.emit("clock", sim_t=round(self.clock.t, 1), speed=self.clock.speed, paused=self.clock.paused)
            for u in self.units.values():
                if u.kd.airborne or u.ground in ("launching", "swapping", "waiting_battery") or u.last_pos_msg != (u.status,):
                    self.emit("drone", **self.drone_msg(u))
                    if u.kd.airborne and self._n_flush % 3 == 0:
                        self.emit("cone", **self.cone_msg(u))
                u.last_pos_msg = (u.status,)
        p = self._pending
        if p and wall_now - getattr(self, "_last_scan_flush", 0.0) >= 0.5:
            self._last_scan_flush = wall_now
            self._pending = {}
            if p.get("buildings") or p.get("cells"):
                self.emit("scan", cells=self._cells_ll(p.get("cells", [])), buildings=self._building_states(sorted(p.get("buildings", ())))[:600], summary=self.scan_summary())
            for o in p.get("observations", []):
                self.emit("observation", **o)
            if p.get("observations"):
                self._queue_dirty = True
                self.emit("model", **self.model_totals_now())
            if p.get("roads"):
                self._road_update(p["roads"])
        if wall_now - self._last_cloud >= 0.5:
            self._last_cloud = wall_now
            for u in self.units.values():
                if u.kd.scanning:
                    self._cloud(u)
        if self._queue_dirty and wall_now - getattr(self, "_last_queue_emit", 0.0) >= 1.0:
            self._last_queue_emit = wall_now
            self.emit_queue()

    def _housekeeping(self, t):
        pass

    def _cloud(self, u: Unit):
        kd = u.kd
        pts, kind, src = self.world.lidar(tuple(kd.pos), kd.yaw)
        if not len(pts):
            return
        bias = kd.bias
        out = []
        for p, k in zip(pts, kind):
            key = (int(p[0] // 1.0), int(p[1] // 1.0), int(p[2] // 1.0))
            if key in u.slam_vox:
                continue
            u.slam_vox.add(key)
            q = (round(float(p[0] + bias[0]), 1), round(float(p[1] + bias[1]), 1), round(float(p[2] + bias[2]), 1))
            out += [*q, int(k)]
            if len(self.cloud) < 300_000:
                self.cloud.append((*q, int(k), u.id))
        if out:
            self.emit("points", drone=u.id, pts=out[:4 * 400])

    def _cells_ll(self, cells):
        """50 m coverage cells as [ci, cj, lon, lat of the cell centre] so a map layer can draw them."""
        if not len(cells):
            return []
        a = np.asarray(cells, float)
        lon, lat = self.frame.to_lonlat((a[:, 0] + 0.5) * 50.0, (a[:, 1] + 0.5) * 50.0)
        return [[int(c[0]), int(c[1]), round(float(lo), 6), round(float(la), 6)] for c, lo, la in zip(cells, lon, lat)]

    def _building_states(self, ids):
        res = []
        for b in ids:
            s = int(self.scan.state[b])
            o = int(self.scan.observed[b])
            res.append({"id": int(b), "s": s, "o": o if o >= 0 else None, "c": round(float(self.scan.conf[b]), 2)})
        return res

    def building_at(self, lon: float, lat: float, r: float = 45.0) -> dict | None:
        """The building nearest a map click, with what the model and the drones know about it (never the hidden outcome)."""
        x, y = (float(v) for v in self.frame.to_local(lon, lat))
        ids = self.index.within(x, y, r)
        if not len(ids):
            return None
        d = np.hypot(self.index.x[ids] - x, self.index.y[ids] - y)
        i = int(ids[int(np.argmin(d))])
        lv = self.model.g["levels"].iat[i] if "levels" in self.model.g else None
        o = int(self.scan.observed[i])
        return {"id": i, "levels": None if lv is None or not np.isfinite(lv) else int(lv), "occupants": int(self.occ[i]), "p_collapse": round(float(self.feedback.base[i, 3]), 3),
                "p_collapse_updated": round(float(self.p_current()[i, 3]), 3), "scan": ("unscanned", "partial", "scanned")[int(self.scan.state[i])], "scan_pct": round(float(self.scan.conf[i]), 2),
                "observed": TRUTH_NAME[o] if o >= 0 else None, "distance_m": round(float(d.min()), 1)}

    def scan_polys(self, ids) -> dict:
        """GeoJSON of the given scanned buildings (never unscanned ones: that would leak the map of the area)."""
        feats = []
        g = self.model.g.geometry.values
        for b in ids:
            if self.scan.state[b] == 0:
                continue
            feats.append({"type": "Feature", "geometry": shapely.geometry.mapping(g[int(b)]),
                          "properties": {"id": int(b), "s": int(self.scan.state[b]), "o": int(self.scan.observed[b]) if self.scan.observed[b] >= 0 else None}})
        return {"type": "FeatureCollection", "features": feats}

    def scan_summary(self) -> dict:
        c = self.scan.counts()
        n = self.index.n
        c["pct_scanned"] = round(100.0 * c["scanned"] / max(n, 1), 2)
        c["neighbourhoods"] = list(self.neigh.percent(self.scan.conf).values())
        c["sightings"] = sum(1 for d in self.detections.values() if d["status"] == "unverified")
        c["confirmed"] = sum(1 for d in self.detections.values() if d["status"] in ("confirmed", "merged"))
        return c

    def _road_msg(self, rid):
        blocked = self.road_seen[rid]
        out = {"road": int(rid), "blocked": blocked}
        if blocked:
            es = np.flatnonzero(self.roads.road_id == rid)
            if len(es):
                pl = self.roads.polyline(int(es[0]))
                lon, lat = self.frame.to_lonlat(pl[:, 0], pl[:, 1])
                out["line"] = [[round(float(a), 6), round(float(b), 6)] for a, b in zip(lon, lat)]
        return out

    def _road_update(self, rids):
        edges = {}
        for rid in rids:
            for e in np.flatnonzero(self.roads.road_id == rid):
                edges[int(e)] = self.road_seen[rid]
        self.calls.road_override(edges)
        self.emit("road", roads=[self._road_msg(r) for r in rids])

    def model_totals_now(self) -> dict:
        p = self.p_current()
        occ = self.occ
        base = self.feedback.base
        return {"observed_buildings": int(self.feedback.n_obs), "modelled": {"expected_collapses": round(float(base[:, 3].sum()), 1), "people_at_risk": round(float((occ * base[:, 2]).sum()))},
                "drone_updated": {"expected_collapses": round(float(p[:, 3].sum()), 1), "people_at_risk": round(float((occ * p[:, 2]).sum()))}}

    # ================================================================== snapshot, reachability, chunks
    def reachability(self) -> list[dict]:
        cfg = self.cfg
        r = E.reach(cfg, cfg.wind_ms)
        out = []
        for s in self.stations:
            rings = []
            for name, rad in (("r_max", r["r_max_m"]), ("10 min", r["r_10min_m"]), ("5 min", r["r_5min_m"])):
                a = np.linspace(0, 2 * np.pi, 72, endpoint=False)
                lon, lat = self.frame.to_lonlat(s["x"] + rad * np.cos(a), s["y"] + rad * np.sin(a))
                ring = [[round(float(p), 5), round(float(q), 5)] for p, q in zip(lon, lat)]
                rings.append({"name": name, "radius_m": round(rad), "ring": ring + [ring[0]]})
            out.append({"station": s["id"], "available": s["available"], "rings": rings})
        return out

    def chunk_allowed(self, i: int, j: int) -> bool:
        if (i, j) in self.seen_chunks or (i, j) in self.extra_seen:
            return True
        s = self.cfg.chunk_m
        cx, cy = (i + 0.5) * s, (j + 0.5) * s
        return any(u.kd.airborne and math.hypot(u.kd.pos[0] - cx, u.kd.pos[1] - cy) < 250 for u in self.units.values())

    def chunk_json(self, i: int, j: int) -> dict:
        if not self.chunk_allowed(i, j):
            raise ServiceError("not_seen", "The drone has not been near that chunk.", 403)
        return self.world.chunk(i, j).json()

    def snapshot(self) -> dict:
        return {"enabled": True, "sim": True, "config": public(self.cfg), "clock": {"sim_t": round(self.clock.t, 1), "speed": self.clock.speed, "paused": self.clock.paused},
                "fleet": [self.drone_msg(u) for u in self.units.values()], "stations": [self._station_msg(s) for s in self.stations],
                "routes": [self._route_msg(u) for u in self.units.values() if u.kd.airborne], "queue": self.queue(24, False), "detections": [self._det_msg(d) for d in self.detections.values()],
                "scan": self.scan_summary(), "model": self.model_totals_now(), "auto": self.auto, "fidelity": ["L0"] + (["L1"] if self.worker_ok else []), "reach": self.reachability(),
                "roads": [self._road_msg(rid) for rid in self.road_seen], "cells": self._cells_ll(sorted(self.scan.cells)[:40000]), "run": self.run_id, "origin": {"lon": self.frame.lon0, "lat": self.frame.lat0}}

    # ================================================================== model and truth
    def model_summary(self) -> dict:
        a = self.feedback.aggregates()
        return {k: a[k] for k in ("modelled", "drone_updated", "deaths", "observed_buildings", "changed")} | {
            "note": "Observations come from a simulated disaster drawn from this model; they test the workflow, not the model's accuracy."}

    def model_map(self, updated: bool = True) -> dict:
        a = self.feedback.aggregates(updated)
        return {"heat": a["heat"], "cells": {"type": "FeatureCollection", "features": a["cells"]}}

    def reroll(self, seed: int | None = None):
        self.recall_all("truth re-rolled")
        self.cfg.seed = int(seed if seed is not None else self.cfg.seed + 1)
        self.bind(self.model, self.layer, self.calls, self.p_edge)

    def truth_stats(self) -> dict:                  # debug and evaluation only
        return self.truth.stats()


class FakeCalls(Calls):
    """An in-memory stand-in for the dispatch hub, for tests and for running the service without the web app."""
    def __init__(self, speed=10.0):
        self._speed, self.calls, self.next_id, self.tags, self.corroborated, self.overrides = speed, {}, 1, {}, {}, {}

    def speed(self): return self._speed
    def set_speed(self, v): self._speed = v

    def add_phone_call(self, lat, lon, weight=3.0):
        i = self.next_id; self.next_id += 1
        self.calls[i] = {"id": i, "lat": lat, "lon": lon, "weight": weight, "status": "new", "source": "phone"}
        return i

    def open_calls(self): return [c for c in self.calls.values() if c["status"] == "new"]

    def create_call(self, lat, lon, people, confidence, note, building):
        i = self.next_id; self.next_id += 1
        self.calls[i] = {"id": i, "lat": lat, "lon": lon, "weight": 3.0, "status": "new", "source": "drone", "people": people, "note": note, "building": building}
        return i

    def corroborate(self, call_id, detection_id): self.corroborated[call_id] = detection_id
    def tag(self, call_id, tag): self.tags[call_id] = tag
    def get_call(self, call_id): return self.calls.get(call_id)
    def road_override(self, edge_blocked): self.overrides.update(edge_blocked)


# ====================================================================== exports (what a drone has seen; never the hidden truth)
def victims_geojson(svc: DroneService) -> dict:
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [d["lon"], d["lat"]]},
              "properties": {"id": d["id"], "status": d["status"], "confidence": round(d["confidence"], 2), "est_people": d["est_people"], "drone": d["drone"],
                             "sim_time_s": round(d["t"]), "call_id": d["call_id"], "building": d["building"]}} for d in svc.detections.values()]
    return {"type": "FeatureCollection", "features": feats, "properties": {"simulated": True}}


def coverage_geojson(svc: DroneService) -> dict:
    s = 50.0
    feats = []
    for ci, cj in sorted(svc.scan.cells):
        ring = svc.frame.polygon_to_lonlat([(ci * s, cj * s), ((ci + 1) * s, cj * s), ((ci + 1) * s, (cj + 1) * s), (ci * s, (cj + 1) * s), (ci * s, cj * s)])
        feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": {"kind": "cell"}})
    ids = np.flatnonzero(svc.scan.state > 0)[:20000]
    feats += svc.scan_polys(ids)["features"]
    return {"type": "FeatureCollection", "features": feats, "properties": {"simulated": True}}


def pointcloud_ply(svc: DroneService, drone: str | None = None) -> str:
    pts = [p for p in svc.cloud if drone in (None, "", p[4])]
    head = ("ply\nformat ascii 1.0\ncomment simulated drone SLAM map (believed positions, local east-north-up metres)\n"
            f"element vertex {len(pts)}\nproperty float x\nproperty float y\nproperty float z\nproperty uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n")
    col = {-1: (90, 110, 90), 0: (150, 160, 175), 1: (220, 170, 90), 2: (200, 90, 80), 3: (230, 60, 60)}
    return head + "\n".join(f"{x} {y} {z} {' '.join(map(str, col.get(k, (200, 200, 200))))}" for x, y, z, k, _ in pts) + "\n"


def events_ndjson(svc: DroneService) -> str:
    import json
    return "\n".join(json.dumps({"wall_s": t, **m}, separators=(",", ":")) for t, m in svc.recording) + "\n"
