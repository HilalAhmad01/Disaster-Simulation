"""One small JSON document for the Kindle dashboard: GET /api/kindle/feed.

The kindle-dash server (a separate project that draws a 600x800 e-ink page) polls this every few seconds. It carries what the Kindle
shows and nothing else: the latest simulation run, the worst-hit areas, the SOS calls, responder capacity and the drones. Read-only,
the same openness as /api/live/state. SOS calls leave out the phone's device id and exact position.
"""
import math, time

COMPASS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")
LEVEL_RANK = {"critical": 3, "high": 2, "medium": 1, "low": 0}
MAX_SOS_LISTED = 8


def bearing(lat0, lon0, lat1, lon1):
    """Compass point (N, NE, ...) of the second place as seen from the first."""
    y = math.sin(math.radians(lon1 - lon0)) * math.cos(math.radians(lat1))
    x = math.cos(math.radians(lat0)) * math.sin(math.radians(lat1)) - math.sin(math.radians(lat0)) * math.cos(math.radians(lat1)) * math.cos(math.radians(lon1 - lon0))
    return COMPASS[int(((math.degrees(math.atan2(y, x)) + 360) % 360 + 22.5) // 45) % 8]


def run_block(model):
    """The latest scenario: what was run, the headline numbers, and the five worst-hit 500 m areas."""
    if model is None or getattr(model, "last", None) is None:
        return None
    s, sc = dict(getattr(model, "summary", None) or {}), dict(getattr(model, "scenario", None) or {})
    cas = s.get("casualties") or {}
    inj = [cas.get(k) for k in ("injury_1", "injury_2", "injury_3")]
    out = {
        "id": int(getattr(model, "run_id", 0) or 0), "hazard": s.get("hazard") or sc.get("hazard") or "earthquake",
        "magnitude": sc.get("mag"), "depth_km": sc.get("depth_km"), "time_of_day": sc.get("time_of_day") or s.get("time_of_day"),
        "lat": sc.get("lat"), "lon": sc.get("lon"),
        "people_total": s.get("people_total"), "people_at_risk": s.get("people_at_risk"), "expected_collapses": s.get("expected_collapses"),
        "expected_heavy": s.get("expected_heavy"), "buildings": s.get("buildings"), "max_mmi": s.get("max_mmi"), "max_pga": s.get("max_pga"),
        "deaths": cas.get("deaths"), "injured": int(sum(inj)) if all(v is not None for v in inj) else None,
    }
    lat0, lon0 = sc.get("lat"), sc.get("lon")
    hoods = []
    for f in sorted((x for x in (getattr(model, "cells", None) or []) if x["properties"].get("rank")), key=lambda x: x["properties"]["rank"])[:5]:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        hoods.append({"rank": p["rank"], "risk": p.get("risk"), "collapse": p.get("collapse"), "dist_km": p.get("dist_km"),
                      "dir": bearing(lat0, lon0, lat, lon) if lat0 is not None and lon0 is not None else None})
    out["hoods"] = hoods
    return out


def sos_block(hub, now):
    calls = list(hub.sos.values())
    openc = [s for s in calls if s["status"] != "resolved"]
    out = {"total": len(calls), "open": len(openc), "new": sum(1 for s in openc if s["status"] == "new"),
           "assigned": sum(1 for s in openc if s["status"] == "assigned"), "resolved": len(calls) - len(openc),
           "critical": sum(1 for s in openc if s.get("level") == "critical"), "trapped": sum(1 for s in openc if s.get("trapped")),
           "people": sum(int(s.get("people") or 0) for s in openc), "last_id": max((s["id"] for s in calls), default=0)}
    ranked = sorted(openc, key=lambda s: (-LEVEL_RANK.get(s.get("level"), 0), -float(s.get("score") or 0), s["id"]))[:MAX_SOS_LISTED]
    rows = []
    for s in ranked:
        b = s.get("building") or {}
        rows.append({"id": s["id"], "level": s.get("level"), "status": s["status"], "people": int(s.get("people") or 0), "trapped": bool(s.get("trapped")),
                     "injured": bool(s.get("injured")), "note": (s.get("note") or "")[:48], "age_s": max(0, int(now - s["created"])),
                     "eta_min": s.get("eta_min"), "team": s.get("team_kind"), "building": {"use": b.get("use"), "levels": b.get("levels")},
                     "sim": bool(s.get("sim"))})
    out["calls"] = rows
    return out


def drones_block(svc):
    """The simulated drones, if the drone service has been started (the Drone page starts it)."""
    if svc is None or not getattr(svc, "bound", False):
        return None
    try:
        fleet = [{"label": d["label"], "status": d["status"], "mode": d["mode"], "pct": round(d["pct"]), "alt_m": round(d["z"])}
                 for d in (svc.drone_msg(u) for u in svc.units.values())]
        sc = svc.scan_summary()
        return {"fleet": fleet, "scanned_pct": sc.get("pct_scanned"), "sightings": sc.get("sightings"), "confirmed": sc.get("confirmed")}
    except Exception:
        return None


def resources_block(res):
    if not res or not res.get("loaded"):
        return None
    h = res.get("hospitals") or {}
    return {"units": [{"kind": k["kind"], "free": k["free"], "total": k["units"]} for k in res.get("kinds", [])],
            "hospitals": {"working": h.get("working"), "total": h.get("total"), "beds": h.get("beds_working"), "serious": h.get("serious_injuries_expected")}}


def attach(app, st):
    @app.get("/api/kindle/feed")
    async def kindle_feed():
        now = time.time()
        res = None
        try:                                                   # reuse the existing route: it already knows how to count free units
            route = next(r for r in app.routes if getattr(r, "path", "") == "/api/live/resources")
            res = await route.endpoint()
        except Exception:
            res = None
        hub = app.state.live
        return {"t": now, "area": (st.active_id or "loaded area") if st.model is not None else None, "run": run_block(st.model),
                "sos": sos_block(hub, now), "resources": resources_block(res), "drones": drones_block(getattr(app.state, "drone", None)),
                "speed": hub.speed}
