"""The only module of the drone package that touches FastAPI. It owns: the tick loop, the websocket fan-out to the dashboard and to pilot
tablets, pairing and the control lease endpoints, the adapter to the live dispatch hub (`LiveCalls`), replay, and exports.

  /ws/drone  dashboard (commander): events down; keyboard control up
  /ws/pilot  tablet (token from pairing): events down; stick input, take/release control up
  /api/drone/*  REST for everything discrete
  /pilot  the tablet page; /fpv  the phone-camera view; /drone-static/*  the drone front end (not under /ui, which is a two-file whitelist)
"""
import asyncio, functools, json, pathlib, re, threading, time
from collections import deque
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from .config import from_env, public
from .service import (Calls, DroneService, ServiceError, coverage_geojson, events_ndjson, pointcloud_ply, victims_geojson)
from .sessions import COMMANDER, PairError, Sessions

WEB = pathlib.Path(__file__).resolve().parent.parent / "web" / "drone"
COALESCE = {"drone", "cone", "clock", "cam"}                # only the newest of these matters to a slow client
STATIC_OK = {".js", ".css", ".html", ".json", ".svg", ".png", ".ico", ".map", ".txt"}


def _json(o):
    import numpy as np
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (set, tuple)):
        return list(o)
    raise TypeError(type(o))


def dumps(m) -> str:
    return json.dumps(m, default=_json, separators=(",", ":"))


# ====================================================================== dispatch adapter
class LiveCalls(Calls):
    """The service's view of the existing dispatch (live.py): the demo clock, open calls, creating calls, tagging them, street overrides."""

    def __init__(self, hub, st, live, layer_getter):
        self.hub, self.st, self.live, self.layer_getter = hub, st, live, layer_getter

    def _spawn(self, coro):
        try:
            asyncio.get_running_loop().create_task(coro)
        except RuntimeError:                                    # called from a worker thread or a test without a loop
            coro.close()

    def speed(self) -> float:
        return float(self.hub.speed)

    def set_speed(self, v: float) -> None:
        self.hub.speed = float(v)
        self._spawn(self._push_speed())

    async def _push_speed(self):
        msg = {"type": "speed", "speed": self.hub.speed, "now": time.time()}
        await self.hub.to_dash(msg)
        for dev in list(self.hub.socks):
            await self.hub.to_phone(dev, msg)

    def open_calls(self) -> list[dict]:
        return [{"id": s["id"], "lat": s["lat"], "lon": s["lon"], "weight": self.live.LEVEL_W.get(s.get("level"), 1.0), "status": s["status"], "source": s.get("source", "phone")}
                for s in self.hub.sos.values() if s["status"] == "new" and s.get("lat") is not None]

    def create_call(self, lat, lon, people, confidence, note, building) -> int:
        L, hub, m = self.live, self.hub, self.st.model
        b = None
        try:
            b = L._index_for(m).at(lat, lon)
        except Exception:
            pass
        now = time.time()
        s = {"id": next(hub.ids), "device": f"drone-{int(now * 1000) % 10**9}", "created": now, "status": "new", "team": None, "eta_min": None, "route": None,
             "sim": True, "source": "drone", "lat": lat, "lon": lon, "accuracy_m": 15.0, "trapped": True, "injured": False, "people": int(max(1, people)), "note": note,
             "demo_location": False, "building": b, "updated": now, "drone_confidence": round(float(confidence), 2)}
        b = b or {}
        s["score"], s["level"] = L.priority(True, False, s["people"], b.get("p_collapse", 0.0), b.get("p_extensive", 0.0))
        hub.sos[s["id"]] = s
        self._spawn(hub.sos_changed(s))
        return s["id"]

    def _touch(self, call_id, **kw):
        s = self.hub.sos.get(call_id)
        if s is not None:
            s.update(kw, updated=time.time())
            self._spawn(self.hub.sos_changed(s))
        return s

    def corroborate(self, call_id, detection_id) -> None:
        s = self.hub.sos.get(call_id)
        if s is not None and not s.get("drone_corroborated"):
            self._touch(call_id, drone_corroborated=True, drone_detection=detection_id, note=(s.get("note") or "") + (" · " if s.get("note") else "") + "drone-corroborated")

    def tag(self, call_id, tag) -> None:
        if tag == "drone_checked_empty":
            s = self.hub.sos.get(call_id)
            self._touch(call_id, drone_tag=tag, note=(s.get("note") or "") + (" · " if s and s.get("note") else "") + "a drone looked and could not see anyone (people may be inside)")
        else:
            self._touch(call_id, drone_tag=tag)

    def get_call(self, call_id) -> dict | None:
        s = self.hub.sos.get(call_id)
        if s is None or s.get("lat") is None:
            return None
        return {"id": call_id, "lat": s["lat"], "lon": s["lon"], "weight": self.live.LEVEL_W.get(s.get("level"), 1.0)}

    def road_override(self, edge_blocked: dict) -> None:
        layer = self.layer_getter()
        if layer is None or not edge_blocked:
            return
        layer.set_edge_override(edge_blocked)
        self._spawn(self._reroute(layer, any(edge_blocked.values())))

    async def _reroute(self, layer, any_blocked: bool):
        """Teams already on their way are routed again around streets a drone found blocked ('re-routed, +N min')."""
        if not any_blocked:
            return
        L = self.live
        for s in list(self.hub.sos.values()):
            if s.get("status") != "assigned" or not s.get("route") or s.get("team_kind") is None:
                continue
            try:
                gj = await asyncio.to_thread(L.team_route, layer, s["team_kind"], s["lat"], s["lon"], s.get("team_fid"))
            except Exception:
                continue
            old = (s["route"].get("properties") or {}).get("expected_time_min")
            new = gj["properties"].get("expected_time_min")
            if old is not None and new is not None and new > old + 0.2 and gj["geometry"]["coordinates"] != s["route"]["geometry"]["coordinates"]:
                s.update(route=gj, route_km=round(L.route_km(gj), 2), eta_min=new, route_note=f"re-routed around a street a drone found blocked, +{new - old:.1f} min", updated=time.time())
                await self.hub.sos_changed(s)


# ====================================================================== websocket fan-out
class Client:
    def __init__(self, ws, role, session=None, who=None):
        self.ws, self.role, self.session, self.who = ws, role, session, who
        self.q, self.latest, self.ev = deque(), {}, asyncio.Event()
        self.dead, self.last_seq, self.last_cam = False, -1, 0.0

    def push(self, msg):
        if self.dead:
            return
        if msg["type"] in COALESCE:
            self.latest[(msg["type"], msg.get("drone") or msg.get("id"))] = msg          # per drone: "drone" messages carry the id as "id"
        else:
            self.q.append(msg)
            if len(self.q) > 4000:                          # a client this far behind reconnects and takes a fresh snapshot
                self.dead = True
        self.ev.set()

    async def run(self):
        try:
            while not self.dead:
                await self.ev.wait()
                self.ev.clear()
                while self.q:
                    await self.ws.send_text(dumps(self.q.popleft()))
                if self.latest:
                    batch, self.latest = list(self.latest.values()), {}
                    for m in batch:
                        await self.ws.send_text(dumps(m))
        except Exception as ex:
            if not isinstance(ex, (WebSocketDisconnect, RuntimeError)):      # a closed socket is normal; anything else is a bug worth seeing
                import traceback
                traceback.print_exc()
            self.dead = True


class Bus:
    def __init__(self):
        self.clients: set[Client] = set()
        self.replaying = False

    def publish(self, msg, force=False):
        if self.replaying and not force:
            return
        for c in list(self.clients):
            if c.dead:
                self.clients.discard(c)
            else:
                c.push(msg)


# ====================================================================== attach
def attach(app: FastAPI, st, layer_getter, cfg=None):
    import live as live_mod
    cfg = cfg or from_env()
    if not cfg.enabled:
        return None
    hub = app.state.live
    calls = LiveCalls(hub, st, live_mod, layer_getter)
    svc = DroneService(cfg, calls)
    if cfg.worker_python:                                         # Drone-SLAM physics mode; falls back to the simple model if it cannot start
        info = svc.start_worker()
        print("Drone physics worker:", info if info else "not started")
    app.router.on_shutdown.append(svc.close)
    sessions = Sessions(cfg)
    bus = Bus()
    lock = threading.Lock()
    state = {"wanted": False, "binding": False, "tick_ms": 0.0, "needs": None, "error": None, "last_lease": {}, "replay_task": None}
    app.state.drone, app.state.drone_sessions = svc, sessions

    def on_event(msg):
        if msg["type"] == "mode" and msg.get("mode") != "manual":
            sessions.clear_drone(msg["drone"])
            publish_lease(msg["drone"])
        bus.publish(msg)
    svc.subscribe(on_event)

    def publish_lease(did):
        info = sessions.lease_info(did)
        if state["last_lease"].get(did) != info:
            state["last_lease"][did] = info
            bus.publish({"type": "lease", **info})

    # ------------------------------------------------------------------ binding
    def needs():
        m = st.model
        if m is None:
            return "area"
        if m.last is None:
            return "run"
        return None

    def do_bind():
        with lock:
            m = st.model
            if m is None or m.last is None:
                raise ServiceError("needs_run", "Run a simulation first.")
            layer = layer_getter()
            if layer is None:
                raise ServiceError("needs_layer", "Load the streets and facilities first (Responders: Show hospitals, fire stations and roads).")
            p_edge = live_mod.blockage_now(layer)
            sessions.leases.clear()
            svc.bind(m, layer, calls, p_edge)
            state["needs"], state["error"] = None, None

    async def ensure_bound():
        if state["binding"]:
            raise ServiceError("binding", "The drone module is starting; try again in a moment.", 409)
        if svc.bound and svc.model is st.model and svc.run_id == st.model.run_id:
            return
        state["binding"] = True
        try:
            await asyncio.to_thread(do_bind)
        finally:
            state["binding"] = False

    def safe(fn):
        @functools.wraps(fn)
        async def wrapper(*a, **k):
            try:
                return await fn(*a, **k)
            except ServiceError as e:
                return JSONResponse(status_code=e.status, content={"detail": e.message, "code": e.code})
            except PairError as e:
                return JSONResponse(status_code=400, content={"detail": e.message, "code": e.code})
        return wrapper

    def need():
        if not svc.bound:
            raise ServiceError("not_started", "The drone module has not started.", 409)

    # ------------------------------------------------------------------ tick loop
    async def loop():
        last, nxt_check = time.monotonic(), 0.0
        period = 1.0 / cfg.telemetry_hz
        while True:
            await asyncio.sleep(period)
            now = time.monotonic()
            dt, last = now - last, now
            try:
                if state["wanted"] and now >= nxt_check:
                    nxt_check = now + 2.0
                    m = st.model
                    stale = svc.bound and (svc.model is not m or (m is not None and m.last is not None and svc.run_id != m.run_id))
                    if (not svc.bound or stale) and m is not None and m.last is not None and not state["binding"]:
                        try:
                            await ensure_bound()
                        except ServiceError as e:
                            state["needs"], state["error"] = e.code, e.message
                if svc.bound and not bus.replaying and not state["binding"] and lock.acquire(blocking=False):
                    try:
                        t0 = time.perf_counter()
                        svc.tick(dt, time.time())
                        state["tick_ms"] = 0.9 * state["tick_ms"] + 0.1 * (time.perf_counter() - t0) * 1000
                    finally:
                        lock.release()
                if svc.bound:
                    for did in svc.units:
                        publish_lease(did)
            except Exception as ex:                              # the loop must survive anything
                state["error"] = f"{type(ex).__name__}: {ex}"
                await asyncio.sleep(0.5)

    app.router.on_startup.append(lambda: asyncio.get_running_loop().create_task(loop()))

    # ------------------------------------------------------------------ static and pages
    @app.get("/pilot")
    def pilot_page():
        return FileResponse(WEB / "pilot" / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/fpv")
    def fpv_page():
        return FileResponse(WEB / "fpv" / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/drone")
    def drone_page():
        return FileResponse(WEB / "page" / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/drone-static/{path:path}")
    def drone_static(path: str):
        f = (WEB / path).resolve()
        if WEB.resolve() not in f.parents or f.suffix not in STATIC_OK or not f.is_file():
            raise HTTPException(404, "Unknown file.")
        long = "vendor" in f.parts
        return FileResponse(f, headers={"Cache-Control": "public, max-age=86400" if long else "no-store"})

    # ------------------------------------------------------------------ config and state
    @app.get("/api/drone/config")
    def get_config():
        return {"enabled": True, "sim": True, "bound": svc.bound, "needs": needs() or state["needs"], "error": state["error"], "config": public(cfg),
                "fidelity": ["L0"] + (["L1"] if svc.worker_ok else []), "protocol": 1}

    @app.get("/api/drone/health")
    def health():
        return {"ok": True, "bound": svc.bound, "tick_ms": round(state["tick_ms"], 2), "clients": len(bus.clients), "sessions": len(sessions.sessions),
                "sim_t": round(svc.clock.t, 1), "speed": svc.clock.speed, "units": len(svc.units), "replaying": bus.replaying, "error": state["error"]}

    @app.post("/api/drone/start")
    @safe
    async def start():
        state["wanted"] = True
        if needs():
            raise ServiceError("needs_" + needs(), "Run a simulation first." if needs() == "run" else "Load an area first.", 409)
        await ensure_bound()
        return {"ok": True, "snapshot": svc.snapshot()}

    @app.get("/api/drone/state")
    @safe
    async def get_state():
        need()
        return svc.snapshot()

    @app.get("/api/drone/reach")
    @safe
    async def reach():
        need()
        return svc.reachability()

    @app.post("/api/drone/clock")
    @safe
    async def clock(q: dict):
        need()
        if "paused" in q:
            svc.clock.paused = bool(q["paused"])
        svc.emit("clock", sim_t=round(svc.clock.t, 1), speed=svc.clock.speed, paused=svc.clock.paused)
        return {"paused": svc.clock.paused, "speed": svc.clock.speed, "sim_t": round(svc.clock.t, 1)}

    # ------------------------------------------------------------------ commands
    @app.post("/api/drone/auto")
    @safe
    async def auto(q: dict):
        need(); svc.set_auto(bool(q.get("enabled"))); return {"auto": svc.auto}

    @app.post("/api/drone/drones/{did}/launch")
    @safe
    async def launch(did: str):
        need(); return svc.launch_best(did)

    @app.post("/api/drone/drones/{did}/take_control")
    @safe
    async def take_control(did: str):
        need()
        sessions.acquire(did, COMMANDER, force=True)
        r = svc.take_control(did)
        publish_lease(did)
        return r

    class ReleaseIn(BaseModel):
        then: str = Field("auto", pattern="^(auto|hold|rth)$")

    @app.post("/api/drone/drones/{did}/release_control")
    @safe
    async def release_control(did: str, q: ReleaseIn):
        need(); sessions.clear_drone(did); r = svc.release(did, q.then); publish_lease(did); return r

    @app.post("/api/drone/drones/{did}/return_home")
    @safe
    async def return_home(did: str):
        need(); svc.return_home(did); return {"ok": True}

    @app.post("/api/drone/drones/{did}/hold")
    @safe
    async def hold(did: str):
        need(); svc.hold(did); return {"ok": True}

    class GotoIn(BaseModel):
        lon: float
        lat: float
        agl: float | None = None

    @app.post("/api/drone/drones/{did}/goto")
    @safe
    async def goto(did: str, q: GotoIn):
        need(); svc.goto(did, q.lon, q.lat, q.agl); return {"ok": True}

    # ------------------------------------------------------------------ tasks and queue
    class TaskIn(BaseModel):
        type: str
        lon: float | None = None
        lat: float | None = None
        building: int | None = None
        call_id: int | None = None
        edge: int | None = None
        agl: float | None = None
        polygon: list[list[float]] | None = None
        pinned: bool = True

    @app.get("/api/drone/queue")
    @safe
    async def queue(limit: int = 40):
        need(); return svc.queue(limit)

    @app.get("/api/drone/tasks/{tid}/why")
    @safe
    async def task_why(tid: str):
        need(); return {"why": svc.task_why(tid)}

    @app.post("/api/drone/tasks/preview")
    @safe
    async def task_preview(q: TaskIn):
        need(); return svc.preview(q.model_dump(exclude_none=True))

    @app.post("/api/drone/tasks")
    @safe
    async def task_create(q: TaskIn):
        need()
        d = q.model_dump(exclude_none=True)
        pinned = d.pop("pinned", True)
        return svc.create_task(d, pinned)

    class PatchIn(BaseModel):
        pinned: bool | None = None
        skipped: bool | None = None
        manual_rank: float | None = None

    @app.patch("/api/drone/tasks/{tid}")
    @safe
    async def task_patch(tid: str, q: PatchIn):
        need(); return svc.patch_task(tid, q.pinned, q.skipped, q.manual_rank)

    @app.delete("/api/drone/tasks/{tid}")
    @safe
    async def task_delete(tid: str):
        need(); svc.cancel_task(tid); return {"ok": True}

    class BanIn(BaseModel):
        polygon: list[list[float]]

    @app.post("/api/drone/zones/ban")
    @safe
    async def zone_ban(q: BanIn):
        need(); return {"id": svc.ban_zone(q.polygon)}

    @app.delete("/api/drone/zones/ban")
    @safe
    async def zone_clear():
        need(); svc.clear_bans(); svc._queue_dirty = True; return {"ok": True}

    # ------------------------------------------------------------------ scan, detections, calls
    @app.get("/api/drone/scan/summary")
    @safe
    async def scan_summary():
        need(); return svc.scan_summary()

    @app.get("/api/drone/scan/geojson")
    @safe
    async def scan_geojson():
        need()
        import numpy as np
        return svc.scan_polys(np.flatnonzero(svc.scan.state > 0)[:6000])

    @app.get("/api/drone/scan/buildings")
    @safe
    async def scan_buildings(ids: str = ""):
        need()
        import numpy as np
        want = [int(x) for x in ids.split(",") if x.strip().isdigit()][:800]
        return svc.scan_polys(np.asarray([b for b in want if 0 <= b < svc.index.n], dtype=np.int64))

    @app.get("/api/drone/detections")
    @safe
    async def detections():
        need(); return [svc._det_msg(d) for d in svc.detections.values()]

    @app.post("/api/drone/detections/{did}/confirm")
    @safe
    async def det_confirm(did: str):
        need(); return svc.confirm_detection(did)

    @app.post("/api/drone/detections/{did}/reject")
    @safe
    async def det_reject(did: str):
        need(); return svc.reject_detection(did)

    @app.post("/api/drone/calls/{call_id}/verify_with_drone")
    @safe
    async def verify(call_id: int):
        need(); return svc.create_task({"type": "verify_sos", "call_id": call_id}, True)

    @app.get("/api/drone/building_at")
    @safe
    async def building_at(lon: float, lat: float):
        need()
        b = svc.building_at(lon, lat)
        if b is None:
            raise ServiceError("no_building", "No building here.", 404)
        return b

    @app.get("/api/drone/chunk/{i}/{j}")
    @safe
    async def chunk(i: int, j: int):
        need(); return svc.chunk_json(i, j)

    # ------------------------------------------------------------------ model feedback and truth
    @app.get("/api/drone/model/summary")
    @safe
    async def model_summary():
        need()
        with lock:
            return await asyncio.to_thread(svc.model_summary)

    @app.get("/api/drone/model/map")
    @safe
    async def model_map(view: str = "updated"):
        need()
        with lock:
            return await asyncio.to_thread(svc.model_map, view != "modelled")

    @app.post("/api/drone/model/enable")
    @safe
    async def model_enable(q: dict):
        need()
        svc.cfg.feedback_enabled = bool(q.get("enabled"))
        svc.feedback.dirty, svc._p_cache, svc._queue_dirty = True, None, True
        svc.emit("model", **svc.model_totals_now(), enabled=svc.cfg.feedback_enabled)
        return {"enabled": svc.cfg.feedback_enabled}

    @app.post("/api/drone/truth/reroll")
    @safe
    async def reroll(q: dict = None):
        need()
        sessions.leases.clear()
        with lock:
            await asyncio.to_thread(svc.reroll, (q or {}).get("seed"))
        return {"ok": True, "seed": svc.cfg.seed}

    # ------------------------------------------------------------------ pairing and sessions
    @app.post("/api/drone/pair/code")
    @safe
    async def pair_code(q: dict = None):
        return sessions.new_code((q or {}).get("role", "pilot"))

    @app.post("/api/drone/pair/claim")
    @safe
    async def pair_claim(q: dict, request: Request):
        s = sessions.claim(str(q.get("code", "")), str(q.get("name", "tablet")), request.headers.get("user-agent", ""), request.client.host if request.client else "?")
        bus.publish({"type": "sessions", "sessions": sessions.list()})
        return {"token": s["token"], "id": s["id"], "role": s["role"], "name": s["name"]}

    @app.get("/api/drone/sessions")
    async def list_sessions():
        return sessions.list()

    @app.delete("/api/drone/sessions/{sid}")
    @safe
    async def revoke(sid: str):
        ok = sessions.revoke(sid)
        for c in list(bus.clients):
            if c.session and c.session["id"] == sid:
                c.dead = True
                c.ev.set()
        bus.publish({"type": "sessions", "sessions": sessions.list()})
        return {"ok": ok}

    # ------------------------------------------------------------------ exports
    @app.get("/api/drone/export/victims.geojson")
    @safe
    async def x_victims():
        need(); return victims_geojson(svc)

    @app.get("/api/drone/export/coverage.geojson")
    @safe
    async def x_cov():
        need(); return coverage_geojson(svc)

    @app.get("/api/drone/export/events.ndjson")
    @safe
    async def x_events():
        need(); return Response(events_ndjson(svc), media_type="application/x-ndjson")

    @app.get("/api/drone/export/pointcloud.ply")
    @safe
    async def x_ply(drone: str = ""):
        need(); return Response(pointcloud_ply(svc, drone), media_type="application/octet-stream", headers={"Content-Disposition": 'attachment; filename="drone_map.ply"'})

    # ------------------------------------------------------------------ replay
    def replay_dir():
        d = pathlib.Path(st.data) / "drone" / "replays"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def clean(name):
        return re.sub(r"[^A-Za-z0-9_-]", "", name or "")[:40] or "run"

    @app.post("/api/drone/replay/save")
    @safe
    async def replay_save(q: dict):
        need()
        name = clean(q.get("name"))
        f = replay_dir() / f"{name}.ndjson"
        with f.open("w") as fh:
            fh.write(dumps({"snapshot": svc.rec_snapshot}) + "\n")
            for t, m in svc.recording:
                fh.write(dumps({"w": t, "m": m}) + "\n")
        return {"saved": name, "messages": len(svc.recording)}

    @app.get("/api/drone/replay/list")
    async def replay_list():
        return [p.stem for p in sorted(replay_dir().glob("*.ndjson"))]

    async def play(frames, snapshot, speed, label):
        bus.replaying = True
        try:
            bus.publish({"type": "replay_start", "name": label}, force=True)
            bus.publish({**snapshot, "type": "snapshot", "replay": True}, force=True)
            t_prev = 0.0
            for t, m in frames:
                await asyncio.sleep(max(0.0, (t - t_prev) / speed))
                t_prev = t
                if m["type"] == "drone":
                    svc.extra_seen.update(svc._chunks_near(m["x"], m["y"], 200.0))
                bus.publish({**m, "replay": True}, force=True)
                if not bus.replaying:
                    break
        finally:
            bus.replaying = False
            bus.publish({"type": "replay_end", "name": label}, force=True)
            if svc.bound:
                bus.publish({"type": "snapshot", **svc.snapshot()}, force=True)

    @app.post("/api/drone/replay/start")
    @safe
    async def replay_start(q: dict):
        need()
        if bus.replaying:
            raise ServiceError("busy", "A replay is already playing.")
        name = q.get("name")
        speed = float(q.get("speed", 1.0))
        if name:
            f = replay_dir() / f"{clean(name)}.ndjson"
            if not f.exists():
                raise ServiceError("not_found", "No such replay.", 404)
            lines = [json.loads(x) for x in f.read_text().splitlines() if x.strip()]
            snap, frames = lines[0]["snapshot"], [(l["w"], l["m"]) for l in lines[1:]]
        else:
            if not svc.recording or svc.rec_snapshot is None:
                raise ServiceError("empty", "Nothing has been recorded yet.")
            snap, frames = svc.rec_snapshot, list(svc.recording)
        state["replay_task"] = asyncio.get_running_loop().create_task(play(frames, snap, max(speed, 0.1), clean(name) if name else "last run"))
        return {"ok": True, "messages": len(frames)}

    @app.post("/api/drone/replay/stop")
    async def replay_stop():
        bus.replaying = False
        return {"ok": True}

    # ------------------------------------------------------------------ websockets
    async def serve(ws: WebSocket, client: Client, handler):
        bus.clients.add(client)
        sender = asyncio.get_running_loop().create_task(client.run())
        try:
            while not client.dead:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(msg, dict):
                    await handler(client, msg)
        except WebSocketDisconnect:
            pass
        except Exception:
            import traceback
            traceback.print_exc()                                    # a bug in a handler must be visible, not a silently dropped connection
        finally:
            client.dead = True
            client.ev.set()
            bus.clients.discard(client)
            sender.cancel()
            if client.session:
                sessions.connected(client.session["token"], False)
                bus.publish({"type": "sessions", "sessions": sessions.list()})

    async def hello_payload(client: Client):
        out = {"type": "hello", "role": client.role, "sim": True, "config": public(cfg), "ready": svc.bound, "needs": needs() or state["needs"]}
        if client.session:
            out["session"] = sessions.public(client.session)
        return out

    CAM_MAX_CHARS, CAM_MIN_GAP_S = 160_000, 0.06                  # one JPEG frame (base64), and at most about 16 frames per second per phone

    def relay_cam(client: Client, did, msg: dict):
        """A pilot phone's camera frame goes to the commander's page only, never to other pilots, never stored or recorded.
        Only the newest frame is kept for a slow viewer (COALESCE), so a weak link drops frames instead of building up a delay."""
        if client.role in ("observer", COMMANDER) or did not in svc.units:
            return
        jpg = msg.get("jpg")
        if not isinstance(jpg, str) or len(jpg) > CAM_MAX_CHARS or not jpg.startswith("/9j/") or not jpg.isascii():      # base64 of a JPEG starts with /9j/
            return
        now = time.monotonic()
        if now - client.last_cam < CAM_MIN_GAP_S:
            return
        client.last_cam = now
        out = {"type": "cam", "drone": did, "w": int(msg.get("w") or 0), "h": int(msg.get("h") or 0), "jpg": jpg}
        for c in list(bus.clients):
            if c.role == COMMANDER and not c.dead:
                c.push(out)

    async def handle(client: Client, msg: dict):
        t = msg.get("type")
        who = client.who
        if t == "ping":
            client.push({"type": "pong", "t": msg.get("t"), "server": time.time()})
            return
        if t == "snapshot" and svc.bound:
            client.push({"type": "snapshot", **svc.snapshot()})
            return
        if not svc.bound:
            return
        did = msg.get("drone")
        try:
            if t == "cam":
                relay_cam(client, did, msg)
                return
            if t == "input":
                seq = int(msg.get("seq", 0))
                if seq <= client.last_seq and seq - client.last_seq > -1000:
                    return                                           # out of order: an older stick position must never overwrite a newer one
                client.last_seq = seq
                if sessions.renew(did, who):
                    svc.manual_input(did, float(msg.get("vx", 0)), float(msg.get("vy", 0)), float(msg.get("vz", 0)), float(msg.get("yr", 0)), assist=bool(msg.get("assist", True)))
                else:
                    client.push({"type": "lease", **sessions.lease_info(did), "you": False})
            elif t == "take_control":
                if not sessions.acquire(did, who, force=(who == COMMANDER)):
                    client.push({"type": "error", "code": "lease_taken", "message": f"{sessions.lease_info(did)['name'] or 'Someone'} is flying this drone."})
                    return
                svc.take_control(did)
                publish_lease(did)
            elif t == "release":
                sessions.release(did, who)
                svc.release(did, msg.get("then", "auto") if msg.get("then") in ("auto", "hold", "rth") else "auto")
                publish_lease(did)
            elif t in ("hold", "rth", "launch"):
                if client.role == "observer":
                    raise ServiceError("forbidden", "Observers cannot control the drone.", 403)
                if t == "hold":
                    svc.hold(did)
                elif t == "rth":
                    svc.return_home(did, "pilot")
                else:
                    svc.launch_best(did)
            elif t == "goto" and client.role != "observer":
                svc.goto(did, float(msg["lon"]), float(msg["lat"]), msg.get("agl"))
            elif t in ("confirm", "reject"):
                if client.role == COMMANDER or (client.role == "pilot" and getattr(cfg, "pilot_may_confirm", False)):
                    (svc.confirm_detection if t == "confirm" else svc.reject_detection)(msg["id"])
                else:
                    d = svc.detections.get(msg.get("id"))
                    if d is not None:
                        d["flagged"] = True
                        svc.emit("detection", **svc._det_msg(d))
        except ServiceError as e:
            client.push({"type": "error", "code": e.code, "message": e.message})
        except (KeyError, ValueError, TypeError):
            client.push({"type": "error", "code": "bad_message", "message": "Bad message."})

    @app.websocket("/ws/drone")
    async def ws_dashboard(ws: WebSocket):
        await ws.accept()
        c = Client(ws, COMMANDER, None, COMMANDER)
        c.push(await hello_payload(c))
        if svc.bound:
            c.push({"type": "snapshot", **svc.snapshot()})
            c.push({"type": "sessions", "sessions": sessions.list()})
            for did in svc.units:
                c.push({"type": "lease", **sessions.lease_info(did)})
        await serve(ws, c, handle)

    @app.websocket("/ws/pilot")
    async def ws_pilot(ws: WebSocket, token: str = ""):
        await ws.accept()
        s = sessions.authenticate(token)
        if s is None:
            await ws.send_text(dumps({"type": "error", "code": "bad_token", "message": "This tablet is not paired (or was removed). Pair it again."}))
            await ws.close(code=1008)
            return
        c = Client(ws, s["role"], s, token)
        sessions.connected(token, True)
        state["wanted"] = True
        c.push(await hello_payload(c))
        if svc.bound:
            c.push({"type": "snapshot", **svc.snapshot()})
            for did in svc.units:
                c.push({"type": "lease", **sessions.lease_info(did)})
        bus.publish({"type": "sessions", "sessions": sessions.list()})
        await serve(ws, c, handle)

    return svc
