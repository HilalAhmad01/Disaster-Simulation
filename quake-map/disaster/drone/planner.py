"""Turning tasks into flyable, energy-feasible sorties. Uses energy.py, so the plan's energy matches the simulated drain.

  survey path : lawnmower passes over a block at tallest-building + clearance (>= min_alt, <= max_alt)
  sortie      : greedy insertion by gain/cost, improved by 2-opt on energy; feasible if 1.1 * energy <= battery - reserve
  route       : takeoff, climb, [transit, descend, survey..., climb]*, return, land    (waypoint speed = horizontal speed of the leg INTO it)
"""
import math
from . import energy as E


def block_alt(cfg, tallest: float) -> tuple[float, bool]:
    """Survey altitude for a block and whether the tallest building is too tall to clear within max_alt."""
    want = tallest + cfg.clearance_m
    return float(min(max(want, cfg.min_alt_m), cfg.max_alt_m)), want > cfg.max_alt_m


def survey_path(cfg, rect, margin: float = 0.0) -> list[tuple[float, float]]:
    """Boustrophedon passes along x covering rect (x0, y0, x1, y1) with spacing swath * (1 - overlap). Returns the turning points."""
    x0, y0, x1, y1 = rect
    w = cfg.swath_m
    step = w * (1.0 - cfg.survey_overlap)
    h = y1 - y0
    n = max(1, math.ceil((h - w) / step) + 1) if h > w else 1
    ys = [y0 + w / 2 + k * (h - w) / (n - 1) for k in range(n)] if n > 1 else [(y0 + y1) / 2]
    pts, flip = [], False
    for y in ys:
        a, b = (x1, x0) if flip else (x0, x1)
        pts += [(a, y), (b, y)]
        flip = not flip
    return pts


def survey_wps(cfg, path, alt, task_id):
    return [dict(x=x, y=y, z=alt, speed=cfg.survey_speed_ms, scan=True, kind="survey", task_id=task_id) for x, y in path]


def block_task(cfg, blocks, k, vals, tid=None) -> dict:
    alt, too_tall = block_alt(cfg, float(blocks.tallest[k]))
    path = survey_path(cfg, blocks.rect[k])
    wps = survey_wps(cfg, path, alt, tid or blocks.id(k))
    return {"id": tid or blocks.id(k), "type": "survey_block", "block": int(k), "alt": alt, "too_tall": too_tall, "wps": wps,
            "entry": (path[0][0], path[0][1]), "exit": (path[-1][0], path[-1][1]), "centre": tuple(float(v) for v in blocks.centre[k]),
            "rect": [float(v) for v in blocks.rect[k]], "value": float(vals["value"][k]), "components": {"victims": float(vals["victims"][k]), "info": float(vals["info"][k]), "sos": float(vals["sos"][k])},
            "buildings": int(blocks.count[k])}


def point_task(cfg, kind, x, y, tid, alt=None, dwell_s=0.0, value=0.0, building=None, ring_r=None, sos_id=None) -> dict:
    """Small tasks: inspect_building (a ring), verify_sos (hover and look), goto, check_road (a line is built by the caller)."""
    alt = float(alt if alt is not None else cfg.min_alt_m)
    if kind == "inspect_building":
        r = ring_r or 30.0
        n = 10
        wps = [dict(x=x + r * math.cos(2 * math.pi * k / n), y=y + r * math.sin(2 * math.pi * k / n), z=alt, speed=cfg.survey_speed_ms * 0.6,
                    scan=True, kind="survey", task_id=tid) for k in range(n + 1)]
    else:
        wps = [dict(x=x, y=y, z=alt, speed=cfg.survey_speed_ms, scan=True, kind="dwell" if dwell_s else "goto", task_id=tid, dwell_s=dwell_s)]
    return {"id": tid, "type": kind, "alt": alt, "wps": wps, "entry": (wps[0]["x"], wps[0]["y"]), "exit": (wps[-1]["x"], wps[-1]["y"]), "centre": (x, y),
            "value": value, "components": {"victims": 0.0, "info": 0.0, "sos": value}, "building": building, "sos_id": sos_id, "rect": None, "too_tall": False, "buildings": 1}


def line_task(cfg, tid, pts, alt, value=0.0, edge=None) -> dict:
    wps = [dict(x=x, y=y, z=alt, speed=cfg.survey_speed_ms, scan=True, kind="survey", task_id=tid) for x, y in pts]
    return {"id": tid, "type": "check_road", "alt": alt, "wps": wps, "entry": (pts[0][0], pts[0][1]), "exit": (pts[-1][0], pts[-1][1]),
            "centre": (pts[len(pts) // 2][0], pts[len(pts) // 2][1]), "value": value, "components": {"victims": 0.0, "info": 0.0, "sos": value},
            "edge": edge, "rect": None, "too_tall": False, "buildings": 0}


def build_route(cfg, home, tasks: list[dict], Z: float) -> list[dict]:
    hx, hy = home
    wp = lambda x, y, z, speed, kind, **k: dict(x=x, y=y, z=z, speed=speed, scan=False, kind=kind, **k)
    r = [wp(hx, hy, 0.0, 0.0, "takeoff"), wp(hx, hy, Z, cfg.climb_rate_ms, "climb")]
    for t in tasks:
        ex, ey = t["entry"]
        zt = t["wps"][0]["z"]
        top = max(Z, zt)
        r.append(wp(ex, ey, top, cfg.cruise_speed_ms, "transit", task_id=t["id"]))
        if zt < top:
            r.append(wp(ex, ey, zt, cfg.climb_rate_ms, "descend", task_id=t["id"]))
        r += [dict(w) for w in t["wps"]]
        lx, ly = t["exit"]
        if zt < Z:
            r.append(wp(lx, ly, Z, cfg.climb_rate_ms, "climb", task_id=t["id"]))
    r.append(wp(hx, hy, Z, cfg.cruise_speed_ms, "return"))
    r.append(wp(hx, hy, 0.0, 2.0, "land"))
    return r


def energy_of(cfg, home, tasks, Z, wind=None) -> float:
    return E.route_energy(cfg, build_route(cfg, home, tasks, Z), wind=wind)[0]


def feasible(cfg, e_wh: float, battery_wh: float) -> bool:
    return e_wh * 1.1 <= battery_wh - E.reserve_wh(cfg)


def plan_sortie(cfg, home, battery_wh: float, candidates: list[dict], Z: float, wind=None, pinned: tuple = ()) -> dict | None:
    """Choose and order tasks for one drone. Returns None when nothing worthwhile fits."""
    order: list[dict] = []
    pool = list(candidates)
    pinned_ids = set(pinned)
    # pinned tasks go first if they fit on their own
    for t in [c for c in pool if c["id"] in pinned_ids]:
        trial = order + [t]
        if feasible(cfg, energy_of(cfg, home, trial, Z, wind), battery_wh):
            order = trial; pool.remove(t)
    fixed = len(order)                                   # the pinned prefix keeps its place
    base = energy_of(cfg, home, order, Z, wind) if order else 0.0
    while pool:
        best = None
        for t in pool:
            for p in range(fixed, len(order) + 1):
                trial = order[:p] + [t] + order[p:]
                e = energy_of(cfg, home, trial, Z, wind)
                if not feasible(cfg, e, battery_wh):
                    continue
                cost = max(e - base, 0.05)
                score = t["value"] / cost
                if best is None or score > best[0]:
                    best = (score, t, p, e)
        if best is None:
            break
        _, t, p, e = best
        order.insert(p, t); pool.remove(t); base = e
    if not order:
        return None
    # 2-opt on energy (never moves pinned tasks ahead of their order: they stay first)
    improved, it = True, 0
    while improved and it < 50:
        improved, it = False, it + 1
        for i in range(fixed, len(order) - 1):
            for j in range(i + 1, len(order)):
                cand = order[:i] + order[i:j + 1][::-1] + order[j + 1:]
                e = energy_of(cfg, home, cand, Z, wind)
                if e < base - 1e-6 and feasible(cfg, e, battery_wh):
                    order, base, improved = cand, e, True
    route = build_route(cfg, home, order, Z)
    e_wh, t_s, legs = E.route_energy(cfg, route, wind=wind)
    return {"tasks": [t["id"] for t in order], "task_objs": order, "route": route, "energy_wh": e_wh, "duration_s": t_s, "legs": legs,
            "value": sum(t["value"] for t in order), "remaining_wh": battery_wh - e_wh, "Z": Z}


def single_feasibility(cfg, home, task, battery_wh, Z, wind=None) -> dict:
    """Preview for one task from one station: energy, time, and whether it fits (with the reserve)."""
    route = build_route(cfg, home, [task], Z)
    e, t, _ = E.route_energy(cfg, route, wind=wind)
    ok = feasible(cfg, e, battery_wh)
    return {"energy_wh": e, "duration_s": t, "feasible": ok, "remaining_wh": battery_wh - e,
            "reason": None if ok else "out_of_range" if e * 1.1 > E.usable_wh(cfg) else "not_enough_battery"}
