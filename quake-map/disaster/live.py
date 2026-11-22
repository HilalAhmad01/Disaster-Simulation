"""Live demo layer: phones open /sos, responders on the dashboard see their SOS calls, send alerts to phones near a point,
send teams, and phones get a walking route to the nearest safe place.

Everything lives in memory and is lost when the server restarts. It is a demo of the idea, not an emergency service:
no accounts, no push notifications (a phone hears an alert only while the page is open), no SMS fallback.

Connections: one WebSocket per open page (/ws/live?role=phone&device=<id> or role=dashboard). Phones send their position over
it; the server sends alerts and SOS updates to phones and every change to the dashboards. Heavy work (routes, building
lookups) runs in a worker thread; the hub itself is only touched on the event loop, so it needs no locks.
"""
import asyncio, itertools, math, pathlib, random, threading, time, urllib.parse
import numpy as np
from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree
import responders as P, roads as R

PAGE = pathlib.Path(__file__).resolve().parent / "sos.html"
WALK_MS = 1.3                        # walking speed, m/s
WALK_LAMBDA = 5.0                    # walking cost penalty per unit P(blocked): people can pick their way past some rubble, cars cannot
BUILDING_M = 40.0                    # an SOS within this distance of a building centre is taken to come from that building
ALERT_REPLAY_S = 30 * 60             # a phone that connects within this time of an alert, inside its circle, still gets it (once)
SOS_MOVE_M = 15.0                    # an open SOS moves with its phone when the phone moves farther than this
HTTPS_PORT = 8443                    # where tools/https_proxy.py serves the app for phones
MAX_PHONES, MAX_SOS = 5000, 5000     # the page can be opened by anyone with the link; keep memory bounded
SAFE_KINDS = {"earthquake": ("open_space", "shelter"),          # after shaking: open ground away from buildings
              "flood": ("shelter", "school"),                   # raised, solid buildings (damaged ones are filtered out)
              "cyclone": ("shelter", "school")}
ANY_SAFE = ("open_space", "shelter", "school")
TEAM_POOL = {"fire_station": ("fire_station",), "ambulance": ("ambulance", "hospital"), "police": ("police",)}
TEAM_NAME = {"fire_station": "Fire team", "ambulance": "Ambulance", "police": "Police team"}
LEVELS = ("low", "medium", "high", "critical")

# ---------------------------------------------------------------- pure helpers (tested without a server)

def metres(lat0, lon0, lat, lon):
    """Local east/north metres from (lat0, lon0); accurate to well under 1% across a city."""
    k = math.cos(math.radians(lat0))
    return (np.asarray(lon) - lon0) * 111320.0 * k, (np.asarray(lat) - lat0) * 110540.0

def dist_m(lat1, lon1, lat2, lon2):
    x, y = metres(lat1, lon1, lat2, lon2)
    return float(np.hypot(x, y))

def priority(trapped, injured, people, p_collapse=0.0, p_extensive=0.0):
    """Score and level for an SOS. Who is in trouble (trapped > injured > just asking for help, more people > fewer), scaled up
    when the model says the building it came from is likely damaged. Trapped people are never below 'high'."""
    base = 1.0 + 4.0 * bool(trapped) + 2.0 * bool(injured) + 0.4 * min(max(int(people), 1), 10)
    score = base * (1.0 + 3.0 * float(p_collapse) + float(p_extensive))
    lvl = 3 if score >= 12 else 2 if score >= 6 else 1 if score >= 3 else 0
    if trapped:
        lvl = max(lvl, 2)
    return round(score, 2), LEVELS[lvl]

def lan_ip():
    """This machine's address on the local network (for the phone link). A UDP connect sends nothing; it only picks the route."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("10.255.255.255", 1))
            ip = sk.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None

def port_open(port):
    """True when something listens on this port here (the https proxy for phones)."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False

def gmaps_link(lat, lon, olat=None, olon=None, mode="walking"):
    q = {"api": "1", "destination": f"{lat:.6f},{lon:.6f}", "travelmode": mode}
    if olat is not None:
        q["origin"] = f"{olat:.6f},{olon:.6f}"
    return "https://www.google.com/maps/dir/?" + urllib.parse.urlencode(q)

class BuildingIndex:
    """Nearest building to a point, for 'this SOS comes from a building with a 30% chance of collapse'."""
    def __init__(self, model):
        self.model = model
        self.lat0, self.lon0 = float(np.mean(model.lat)), float(np.mean(model.lon))
        x, y = metres(self.lat0, self.lon0, model.lat, model.lon)
        self.tree = cKDTree(np.column_stack([x, y]))

    def at(self, lat, lon):
        x, y = metres(self.lat0, self.lon0, lat, lon)
        d, i = self.tree.query([float(x), float(y)])
        if not np.isfinite(d) or d > BUILDING_M:
            return None
        m, i = self.model, int(i)
        g = m.g.iloc[i]
        lv, occ = g.get("levels"), g.get("occupants")
        out = {"index": i, "distance_m": round(float(d)), "use": str(g.get("use", "") or ""),
               "levels": None if lv is None or lv != lv else int(lv), "occupants": 0 if occ is None or occ != occ else int(round(float(occ)))}
        if m.last is not None:
            out.update(p_collapse=round(float(m.last["p_collapse"].iat[i]), 3), p_extensive=round(float(m.last["p_extensive"].iat[i]), 3))
        return out

def _index_for(model):
    ix = getattr(model, "_live_index", None)
    if ix is None:
        ix = model._live_index = BuildingIndex(model)
    return ix

_pedge_lock = threading.Lock()

def blockage_now(layer):
    """P(blocked) per street edge for the run on the map (zeros before any run), reusing the access result when there is one."""
    m = layer.model
    if m.last is None:
        return np.zeros(layer.net.n_edges)
    a = layer.access.get(m.run_id)
    if a is not None:
        return a["p_edge"]
    with _pedge_lock:
        c = getattr(layer, "_live_pedge", None)
        if c is None or c[0] != m.run_id:
            c = layer._live_pedge = (m.run_id, np.asarray(layer.blockage(), float))
        return c[1]

def _node_of(layer, lat, lon):
    xy = layer._tr.transform(lon, lat) if layer._tr is not None else (lon, lat)
    n = int(P.nearest_nodes(layer.net, [xy])[0])
    return n, float(np.hypot(*(np.asarray(xy) - layer.net.node_xy[n])))

def _to_ll(layer, xy):
    if layer.net.utm is None:
        return [list(map(float, p)) for p in xy]
    from pyproj import Transformer
    tr = getattr(layer, "_live_inv", None)
    if tr is None:
        tr = layer._live_inv = Transformer.from_crs(layer.net.utm, 4326, always_xy=True)
    xy = np.asarray(xy, float)
    lon, lat = tr.transform(xy[:, 0], xy[:, 1])
    return [[round(a, 6), round(b, 6)] for a, b in zip(lon, lat)]

def _edge_of(layer):
    """(min node, max node) -> the shortest street edge joining them, for drawing walking paths (which ignore one-way rules)."""
    em = getattr(layer, "_live_edges", None)
    if em is None:
        net = layer.net
        lo, hi = np.minimum(net.u, net.v), np.maximum(net.u, net.v)
        order = np.argsort(net.len, kind="stable")[::-1]          # shortest written last, so it wins
        em = layer._live_edges = {(int(lo[e]), int(hi[e])): int(e) for e in order}
    return em

FRIENDLY = {"open_space": "Open ground nearby", "shelter": "Shelter", "school": "School used as a shelter"}

def place_name(layer, i):
    n = layer.fac.name[i]
    return n.strip() if isinstance(n, str) and n.strip() else FRIENDLY.get(layer.fac.kind[i], layer.label(i))

def safe_route(layer, lat, lon, hazard=None):
    """Walking route from (lat, lon) to the nearest working safe place for the hazard on the map, avoiding streets debris may
    block. Returns a GeoJSON Feature (LineString) whose properties name the place and give distance, minutes and risk."""
    net, state = layer.net, layer.facility_state()
    kinds = SAFE_KINDS.get(hazard or "earthquake", ANY_SAFE)
    usable = (~state.may_not_work.to_numpy()) & (layer.fnode >= 0)
    cand = np.flatnonzero(state.kind.isin(kinds).to_numpy() & usable)
    if len(cand) == 0:
        cand = np.flatnonzero(state.kind.isin(ANY_SAFE).to_numpy() & usable)
    if len(cand) == 0:
        raise ValueError("No safe places (open spaces, shelters or schools) are known in this area.")
    p_edge = blockage_now(layer)
    start, off = _node_of(layer, lat, lon)
    M = layer.graph.matrix(net.len / WALK_MS * (1.0 + WALK_LAMBDA * p_edge))
    dist, pred = dijkstra(M, directed=False, indices=start, return_predecessors=True)
    d = dist[layer.fnode[cand]]
    if not np.isfinite(d).any():
        raise ValueError("No walking route was found from here.")
    best = int(cand[int(np.argmin(d))])
    goal = int(layer.fnode[best])
    nodes = [goal]
    while nodes[-1] != start:
        nodes.append(int(pred[nodes[-1]]))
    nodes.reverse()
    em, xy, length, risky, p_road = _edge_of(layer), [], off, 0, {}
    for a, b in zip(nodes[:-1], nodes[1:]):
        e = em[(min(a, b), max(a, b))]
        c = np.asarray(net.geom[e].coords) if net.geom[e] is not None else np.array([net.node_xy[a], net.node_xy[b]])
        if np.hypot(*(c[0] - net.node_xy[a])) > np.hypot(*(c[-1] - net.node_xy[a])):
            c = c[::-1]
        xy.extend(c if not xy else c[1:])
        length += float(net.len[e])
        p = float(p_edge[e])
        risky += p >= 0.3
        p_road[net.road_id[e]] = max(p_road.get(net.road_id[e], 0.0), p)
    f = state.iloc[best]
    if not xy:                                                     # already at the street corner nearest the place
        xy = [net.node_xy[start]]
    fxy = layer._tr.transform(float(f.lon), float(f.lat)) if layer._tr is not None else (float(f.lon), float(f.lat))
    length += float(np.hypot(*(np.asarray(fxy) - net.node_xy[goal])))     # last stretch, street corner to the place
    coords = [[round(lon, 6), round(lat, 6)]] + _to_ll(layer, xy) + [[round(float(f.lon), 6), round(float(f.lat), 6)]]
    p_open = float(np.prod([1 - p for p in p_road.values()])) if p_road else 1.0
    return {"type": "Feature", "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"name": place_name(layer, best), "kind": str(f.kind), "dest": [float(f.lon), float(f.lat)], "distance_m": round(length),
                           "walk_min": max(1, round(length / WALK_MS / 60)), "p_open": round(p_open, 3), "risky_streets": int(risky),
                           "hazard": hazard or "earthquake", "gmaps": gmaps_link(float(f.lat), float(f.lon), lat, lon)}}

def _team_pool(layer, state, kind):
    return np.flatnonzero(state.kind.isin(TEAM_POOL[kind]).to_numpy() & ~state.may_not_work.to_numpy() & (layer.fnode >= 0))

def team_options(layer, lat, lon, per_kind=2):
    """The nearest working fire stations, ambulance points (or hospitals) and police stations to (lat, lon), by driving time on
    streets weighted by the chance debris blocks them. One Dijkstra on the reversed graph from the caller gives every
    facility's time at once. Returns a list of {kind, facility_id, name, eta_min} (nearest first within each kind)."""
    state = layer.facility_state()
    dst, _ = _node_of(layer, lat, lon)
    M = layer.graph.matrix(P.expected_cost(layer.net, blockage_now(layer)))
    d = dijkstra(M.T.tocsr(), directed=True, indices=dst)
    out = []
    for kind in TEAM_POOL:
        pool = _team_pool(layer, state, kind)
        t = d[layer.fnode[pool]] if len(pool) else np.array([])
        for j in np.argsort(t, kind="stable")[:per_kind]:
            if np.isfinite(t[j]):
                i = int(pool[j])
                out.append({"kind": kind, "team": TEAM_NAME[kind], "facility_id": int(layer.fac.id[i]), "name": layer.label(i),
                            "facility_kind": str(layer.fac.kind[i]), "eta_min": round(float(t[j]) / 60, 1)})
    return out

def team_route(layer, kind, lat, lon, facility_id=None):
    """Driving route to (lat, lon) from facility `facility_id`, or from the nearest working facility of `kind`."""
    state = layer.facility_state()
    p_edge = blockage_now(layer)
    dst, _ = _node_of(layer, lat, lon)
    M = layer.graph.matrix(P.expected_cost(layer.net, p_edge))
    if facility_id is not None:
        hit = np.flatnonzero(layer.fac.id.to_numpy() == facility_id)
        if len(hit) == 0 or layer.fnode[hit[0]] < 0:
            raise ValueError("That station is not on the street map.")
        best = int(hit[0])
    else:
        pool = _team_pool(layer, state, kind)
        if len(pool) == 0:
            raise ValueError(f"No working {kind.replace('_', ' ')} found in this area.")
        d, _, src = dijkstra(M, directed=True, indices=np.unique(layer.fnode[pool]), min_only=True, return_predecessors=True)
        if not np.isfinite(d[dst]):
            raise ValueError("No open road route reaches this spot.")
        best = int(pool[layer.fnode[pool] == src[dst]][0])
    r = P.route(layer.net, int(layer.fnode[best]), dst, p_edge, layer.graph)
    if r is None:
        raise ValueError("No open road route reaches this spot.")
    gj = P.route_geojson(layer.net, r)
    gj["geometry"]["coordinates"].append([round(lon, 6), round(lat, 6)])
    gj["properties"].update(facility=layer.label(best), facility_id=int(layer.fac.id[best]), kind=kind)
    return gj

# ---------------------------------------------------------------- the hub (in-memory state + sockets)

class SOSIn(BaseModel):
    device: str = Field(min_length=4, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(None, ge=0, le=100000)
    trapped: bool = False
    injured: bool = False
    people: int = Field(1, ge=1, le=500)
    note: str = Field("", max_length=280)
    demo_location: bool = False                  # the phone was placed on the map by hand (its GPS is outside the area)

class Device(BaseModel):
    device: str = Field(min_length=4, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")

class AssignIn(BaseModel):
    kind: str = Field("fire_station", pattern="^(fire_station|ambulance|police)$")
    facility_id: int | None = None               # a station picked from the suggestions; None: the nearest working one of `kind`

class AlertIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    radius_km: float = Field(10, gt=0, le=500)
    message: str = Field(min_length=1, max_length=400)
    level: str = Field("danger", pattern="^(warning|danger)$")
    title: str = Field("", max_length=60)

class SimIn(BaseModel):
    n: int = Field(20, ge=1, le=200)

class Hub:
    def __init__(self):
        self.phones = {}            # device -> {"device","lat","lon","acc","demo","online","safe","seen"}
        self.socks = {}             # device -> set of phone sockets (a phone may have the page open twice)
        self.dash = set()           # dashboard sockets
        self.sos = {}               # id -> SOS dict (public fields only)
        self.alerts = []            # newest last
        self.ids = itertools.count(1)

    # -- views --
    def phone_view(self, p):
        return {k: p.get(k) for k in ("device", "lat", "lon", "acc", "demo", "online", "safe", "seen")}

    def snapshot(self):
        return {"type": "snapshot", "sos": sorted(self.sos.values(), key=lambda s: s["id"]),
                "phones": [self.phone_view(p) for p in self.phones.values()], "alerts": self.alerts[-20:], "now": time.time()}

    def open_sos_of(self, device):
        return next((s for s in self.sos.values() if s["device"] == device and s["status"] != "resolved"), None)

    # -- sending --
    async def _send(self, ws, msg):
        try:
            await ws.send_json(msg)
            return True
        except Exception:
            return False

    async def to_dash(self, msg):
        dead = [ws for ws in list(self.dash) if not await self._send(ws, msg)]
        for ws in dead:
            self.dash.discard(ws)

    async def to_phone(self, device, msg):
        n = 0
        for ws in list(self.socks.get(device, ())):
            if await self._send(ws, msg):
                n += 1
            else:
                self.socks[device].discard(ws)
        return n

    # -- changes --
    async def phone_changed(self, device):
        p = self.phones.get(device)
        if p is not None:
            await self.to_dash({"type": "phone", "phone": self.phone_view(p)})

    async def sos_changed(self, s):
        await self.to_dash({"type": "sos", "sos": s})
        if not s.get("sim"):
            await self.to_phone(s["device"], {"type": "sos", "sos": s})

    def alerts_for(self, lat, lon, since, seen=()):
        return [a for a in self.alerts if a["sent"] >= since and a["id"] not in seen and dist_m(a["lat"], a["lon"], lat, lon) <= a["radius_km"] * 1000]

    async def deliver(self, device, al, lat, lon):
        """Send one alert to a phone and remember it, so a reconnect or reload does not sound the same alarm again."""
        p = self.phones.get(device)
        if p is None or al["id"] in p.setdefault("alerts_seen", set()):
            return 0
        n = await self.to_phone(device, {"type": "alert", "alert": {**al, "distance_km": round(dist_m(al["lat"], al["lon"], lat, lon) / 1000, 1)}})
        if n:
            p["alerts_seen"].add(al["id"])
        return n

def attach(app, st, get_layer):
    """Add the live routes to the FastAPI app. get_layer(): the responder layer for the active area, or None."""
    hub = app.state.live = Hub()

    def model_or_409():
        return st.need()

    def hazard_now():
        m = st.model
        return getattr(m, "hazard", None) if m is not None and m.last is not None else None

    def building_at(lat, lon):
        m = st.model
        if m is None:
            return None
        try:
            return _index_for(m).at(lat, lon)
        except Exception:
            return None

    def score(s):
        b = s.get("building") or {}
        s["score"], s["level"] = priority(s["trapped"], s["injured"], s["people"], b.get("p_collapse", 0.0), b.get("p_extensive", 0.0))

    def touch_phone(device, **kw):
        p = hub.phones.get(device)
        if p is None:
            if len(hub.phones) >= MAX_PHONES:
                raise HTTPException(429, "Too many phones are connected.")
            p = hub.phones[device] = {"device": device, "lat": None, "lon": None, "acc": None, "demo": False, "online": False, "safe": False}
        p.update(kw, seen=time.time())
        return p

    @app.get("/sos")
    def sos_page():
        return FileResponse(PAGE, headers={"Cache-Control": "no-store"})        # phones must not keep an old copy of the page

    @app.get("/api/live/state")
    def live_state():
        return hub.snapshot()

    @app.post("/api/live/sos")
    async def sos_create(q: SOSIn):
        b = await asyncio.to_thread(building_at, q.lat, q.lon)
        s = hub.open_sos_of(q.device)
        now = time.time()
        if s is None:
            if len(hub.sos) >= MAX_SOS:
                raise HTTPException(429, "Too many SOS calls are open.")
            s = {"id": next(hub.ids), "device": q.device, "created": now, "status": "new", "team": None, "eta_min": None, "route": None, "sim": False}
            hub.sos[s["id"]] = s
        s.update(lat=q.lat, lon=q.lon, accuracy_m=q.accuracy_m, trapped=q.trapped, injured=q.injured, people=q.people, note=q.note.strip(),
                 demo_location=q.demo_location, building=b, updated=now)
        score(s)
        touch_phone(q.device, lat=q.lat, lon=q.lon, acc=q.accuracy_m, demo=q.demo_location, safe=False)
        await hub.sos_changed(s)
        await hub.phone_changed(q.device)
        return s

    def get_sos(sid):
        s = hub.sos.get(sid)
        if s is None:
            raise HTTPException(404, "No such SOS.")
        return s

    @app.post("/api/live/sos/{sid}/cancel")
    async def sos_cancel(sid: int, d: Device):
        s = get_sos(sid)
        if s["device"] != d.device:
            raise HTTPException(403, "Only the phone that sent this SOS can cancel it.")
        s.update(status="resolved", resolution="cancelled by the sender", updated=time.time())
        await hub.sos_changed(s)
        return s

    @app.post("/api/live/sos/{sid}/assign")
    async def sos_assign(sid: int, a: AssignIn):
        s = get_sos(sid)
        if s["status"] == "resolved":
            raise HTTPException(409, "This SOS is already resolved.")
        layer = await asyncio.to_thread(get_layer)
        note, gj = None, None
        if layer is None:
            note = "Streets and facilities are not loaded, so no route was drawn (Responders tab: Show hospitals, fire stations and roads)."
        else:
            try:
                gj = await asyncio.to_thread(team_route, layer, a.kind, s["lat"], s["lon"], a.facility_id)
            except ValueError as ex:
                note = str(ex)
        s.update(status="assigned", updated=time.time(), team=(f"{TEAM_NAME[a.kind]} from {gj['properties']['facility']}" if gj else TEAM_NAME[a.kind]),
                 team_kind=a.kind, route=gj, eta_min=(gj["properties"]["expected_time_min"] if gj else None), route_note=note)
        await hub.sos_changed(s)
        return s

    @app.get("/api/live/sos/{sid}/options")
    async def sos_options(sid: int):
        """Suggested teams for one SOS: the nearest working stations of each kind with driving times around likely debris."""
        s = get_sos(sid)
        layer = await asyncio.to_thread(get_layer)
        if layer is None:
            return {"options": [], "note": "Load streets and facilities to get suggested teams with travel times (Responders tab: Show hospitals, fire stations and roads)."}
        try:
            return {"options": await asyncio.to_thread(team_options, layer, s["lat"], s["lon"]), "note": None}
        except ValueError as ex:
            return {"options": [], "note": str(ex)}

    @app.post("/api/live/sos/{sid}/resolve")
    async def sos_resolve(sid: int):
        s = get_sos(sid)
        s.update(status="resolved", resolution="resolved by responders", updated=time.time())
        await hub.sos_changed(s)
        return s

    @app.post("/api/live/alert")
    async def send_alert(a: AlertIn):
        hz = hazard_now() or "earthquake"
        # the id is unique across server restarts, because phones remember the ids of alerts they already showed
        al = {"id": time.time_ns() // 1000, "lat": a.lat, "lon": a.lon, "radius_km": a.radius_km, "message": a.message.strip(), "level": a.level,
              "title": a.title.strip() or {"earthquake": "Earthquake alert", "flood": "Flood alert", "cyclone": "Cyclone alert"}.get(hz, "Emergency alert"),
              "hazard": hz, "sent": time.time()}
        hub.alerts.append(al)
        del hub.alerts[:-100]
        reached, inside = 0, 0
        for dev, p in list(hub.phones.items()):
            if not p.get("online") or p.get("lat") is None or dist_m(a.lat, a.lon, p["lat"], p["lon"]) > a.radius_km * 1000:
                continue
            inside += 1
            if await hub.deliver(dev, al, p["lat"], p["lon"]):
                reached += 1
        al.update(reached=reached, inside=inside)
        await hub.to_dash({"type": "alert", "alert": al})
        return al

    @app.get("/api/live/safe-route")
    async def safe_route_ep(lat: float, lon: float):
        layer = await asyncio.to_thread(get_layer)
        if layer is None:
            raise HTTPException(409, "Safe places are not loaded yet. The responders need to load streets and facilities first.")
        try:
            return await asyncio.to_thread(safe_route, layer, lat, lon, hazard_now())
        except ValueError as ex:
            raise HTTPException(404, str(ex))

    @app.get("/api/live/area")
    def live_area():
        m = st.model
        if m is None:
            return {"loaded": False}
        w, s, e, n = (float(x) for x in m.bounds)
        return {"loaded": True, "name": st.active_id or "loaded area", "bounds": [w, s, e, n], "center": [(w + e) / 2, (s + n) / 2],
                "hazard": hazard_now(), "run": m.run_id if m.last is not None else 0, "lan_ip": lan_ip(), "https_port": HTTPS_PORT if port_open(HTTPS_PORT) else None}

    def pick_buildings(m, k, rng):
        occ = m.g["occupants"].to_numpy(float)
        if m.last is not None:
            w = occ * (0.02 + m.last["p_extensive"].to_numpy(float))
        else:
            w = occ.copy()
            # away from the edges of the box, where nobody is likely to stand on stage
            w *= (np.abs(m.lat - m.lat.mean()) < 0.35 * np.ptp(m.lat)) & (np.abs(m.lon - m.lon.mean()) < 0.35 * np.ptp(m.lon))
        w = np.where(np.isfinite(w) & (w > 0), w, 0)
        if w.sum() <= 0:
            w = np.ones(len(occ))
        return rng.choice(len(w), size=k, p=w / w.sum())

    @app.get("/api/live/demo-spot")
    def demo_spot():
        """A believable place inside the area for a phone whose real location is elsewhere (people at the demo are not in the city)."""
        m = model_or_409()
        i = int(pick_buildings(m, 1, np.random.default_rng())[0])
        return {"lat": float(m.lat[i]) + 0.0002, "lon": float(m.lon[i]) + 0.0002}

    @app.post("/api/live/simulate")
    async def simulate_sos(q: SimIn):
        """Scatter made-up SOS calls over the area (more where the model expects damage), flagged sim=True."""
        m = model_or_409()
        rng = np.random.default_rng()
        idx = pick_buildings(m, q.n, rng)
        notes = ["", "", "Wall fell on the stairs", "Elderly person cannot walk", "Smell of gas", "Child with us", "Water rising", "Door jammed", ""]
        out = []
        for i in idx:
            i = int(i)
            pc = float(m.last["p_collapse"].iat[i]) if m.last is not None else 0.0
            lat, lon = float(m.lat[i]) + rng.normal(0, 0.00008), float(m.lon[i]) + rng.normal(0, 0.00008)
            b = building_at(lat, lon)
            s = {"id": next(hub.ids), "device": f"sim-{rng.integers(1e9):09d}", "created": time.time() - float(rng.uniform(0, 600)),
                 "status": "new", "team": None, "eta_min": None, "route": None, "sim": True, "lat": lat, "lon": lon, "accuracy_m": float(rng.uniform(5, 40)),
                 "trapped": bool(rng.random() < min(0.8, 0.1 + 2 * pc)), "injured": bool(rng.random() < 0.35), "people": int(rng.integers(1, 7)),
                 "note": str(rng.choice(notes)), "demo_location": False, "building": b, "updated": time.time()}
            score(s)
            if len(hub.sos) >= MAX_SOS:
                break
            hub.sos[s["id"]] = s
            out.append(s)
        await hub.to_dash(hub.snapshot())
        return {"added": len(out)}

    @app.post("/api/live/clear")
    async def clear(sim_only: bool = False):
        for sid in [k for k, s in hub.sos.items() if s.get("sim") or not sim_only]:
            s = hub.sos.pop(sid)
            if not s.get("sim"):
                await hub.to_phone(s["device"], {"type": "sos", "sos": {**s, "status": "resolved", "resolution": "cleared by responders"}})
        if not sim_only:
            hub.alerts.clear()
            for p in hub.phones.values():
                p["safe"] = False
        await hub.to_dash(hub.snapshot())
        return {"ok": True}

    @app.websocket("/ws/live")
    async def live_ws(ws: WebSocket, role: str = "phone", device: str = ""):
        await ws.accept()
        if role == "dashboard":
            hub.dash.add(ws)
            await hub._send(ws, hub.snapshot())
            try:
                while True:
                    await ws.receive_text()          # dashboards only listen; reading keeps the socket and its close event alive
            except WebSocketDisconnect:
                pass
            finally:
                hub.dash.discard(ws)
            return
        if not (4 <= len(device) <= 64) or not device.replace("-", "").replace("_", "").isalnum():
            await ws.close(code=1008)
            return
        try:
            p = touch_phone(device, online=True)
        except HTTPException:
            await ws.close(code=1013)
            return
        hub.socks.setdefault(device, set()).add(ws)
        await hub._send(ws, {"type": "hello", "sos": hub.open_sos_of(device), "safe": p["safe"]})
        if p.get("lat") is not None:
            for al in hub.alerts_for(p["lat"], p["lon"], time.time() - ALERT_REPLAY_S, p.get("alerts_seen", ()))[-1:]:
                await hub.deliver(device, al, p["lat"], p["lon"])
        await hub.phone_changed(device)
        try:
            while True:
                msg = await ws.receive_json()
                t = msg.get("type") if isinstance(msg, dict) else None
                if t == "loc":
                    try:
                        lat, lon = float(msg["lat"]), float(msg["lon"])
                        acc = None if msg.get("acc") is None else float(msg["acc"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                        continue
                    p = touch_phone(device, lat=lat, lon=lon, acc=acc, demo=bool(msg.get("demo")))
                    # a recent alert for this spot that the phone has not had yet (it just got a position, or moved into the circle)
                    for al in hub.alerts_for(lat, lon, time.time() - ALERT_REPLAY_S, p.get("alerts_seen", ()))[-1:]:
                        await hub.deliver(device, al, lat, lon)
                    # an open SOS follows the phone, so responders go where the person is now
                    s = hub.open_sos_of(device)
                    if s is not None and dist_m(s["lat"], s["lon"], lat, lon) > SOS_MOVE_M:
                        b = await asyncio.to_thread(building_at, lat, lon)
                        s.update(lat=lat, lon=lon, accuracy_m=acc, building=b, moved=time.time())
                        score(s)
                        await hub.sos_changed(s)
                    await hub.phone_changed(device)
                elif t == "safe":
                    touch_phone(device, safe=bool(msg.get("safe", True)))
                    await hub.phone_changed(device)
        except (WebSocketDisconnect, RuntimeError, ValueError):     # ValueError: a message that is not JSON
            pass
        finally:
            hub.socks.get(device, set()).discard(ws)
            if not hub.socks.get(device):
                hub.socks.pop(device, None)
                if device in hub.phones:
                    hub.phones[device]["online"] = False
                    await hub.phone_changed(device)
    return hub
