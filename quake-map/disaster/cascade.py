"""Knock-on failures after an earthquake: fires, gas leaks, power cuts and bridge closures, derived from the damage of the run on the map.

Every rate here is an ASSUMPTION in the spirit of Hazus fire-following-earthquake and utility modules, not a calibrated value. The numbers are
for comparing scenarios and for showing responders what else may go wrong; they are not predictions. Results are seeded by the run number,
so the same run always shows the same fires."""
import json, pathlib
import numpy as np, pandas as pd
from scipy.spatial import cKDTree
from scipy.special import ndtr

IGN_EXT, IGN_COL = 6e-4, 1.2e-3          # chance a heavily damaged / collapsed building starts a fire
GAS_EXT, GAS_COL = 3e-4, 8e-4            # chance it breaks a gas service line
GAS_TO_FIRE = 0.12                       # share of gas leaks that ignite (later than the first fires)
FIRE_START_MIN = 30.0                    # first fires start within this many minutes of the shaking
FIRE_R0_M, FIRE_RMAX_M = 20.0, 300.0     # a fire starts at 20 m radius and stops spreading at 300 m (some suppression, firebreaks)
FIRE_V_M_MIN = 1.2                       # spread, metres per minute in average density; scaled 0.5 to 2 with local building density
FIRE_MARKS = (15, 30, 60, 120)           # minutes after the quake at which threatened buildings are counted
MAX_FIRES, MAX_GAS, MAX_BRIDGES_DRAWN = 120, 160, 150
POWER_MEDIAN_G, POWER_BETA = 0.35, 0.6   # chance a district's substations and lines fail by PGA (lognormal), assumed
POWER_DAMAGE_W = 0.8                     # weight of local building damage (falling poles and wires) in the outage chance
BRIDGE_MEDIAN_G, BRIDGE_BETA = 0.45, 0.6 # bridge out of service (extensive damage), in the spirit of Hazus typical highway bridges
BRIDGE_TAGS_NO = {"no", "nan", "none", ""}

ASSUMPTIONS = [
    "Ignition chances per damaged building, fire spread speed and the 300 m limit are assumptions.",
    "Power: the chance a district loses supply follows its peak ground acceleration (median 0.35 g) and its building damage; there is no grid map.",
    "Bridges come from OpenStreetMap; they close with a lognormal chance of ground shaking (median 0.45 g). Closed bridges are shown, not yet used when routing.",
]

def _bridges_cache(data_dir, area_id):
    return pathlib.Path(data_dir) / "roads" / f"{area_id}.bridges.json"

def load_bridges(data_dir, area_id, bounds, log=print):
    """Bridges of the area as [{lon, lat, name}], from the OpenStreetMap road graph already downloaded for the responder layer (cached
    next to it). [] when there is no road graph or osmnx is missing."""
    if not data_dir or not area_id:
        return []
    cache = _bridges_cache(data_dir, area_id)
    if cache.exists():
        try:
            return json.loads(cache.read_text())
        except (OSError, ValueError):
            pass
    import roads as R
    gp = R.graph_path(data_dir, area_id)
    if not pathlib.Path(gp).exists():
        return []
    try:
        import osmnx as ox
        log("Reading bridges from the road graph…")
        edges = ox.graph_to_gdfs(ox.load_graphml(gp), nodes=False)
        if "bridge" not in edges:
            out = []
        else:
            b = edges[~edges["bridge"].astype(str).str.lower().isin(BRIDGE_TAGS_NO)]
            pts = b.geometry.interpolate(0.5, normalized=True)
            nm = b["name"].astype(str).where(b.get("name").notna(), "") if "name" in b else pd.Series("", index=b.index)
            df = pd.DataFrame({"lon": pts.x.to_numpy(), "lat": pts.y.to_numpy(), "name": nm.to_numpy()})
            w, s, e, n = bounds
            df = df[(df.lon >= w) & (df.lon <= e) & (df.lat >= s) & (df.lat <= n)]
            # one bridge is many road segments: keep one point per name per ~330 m square
            df = df.assign(k=list(zip(df.name, (df.lon / 0.003).round(), (df.lat / 0.003).round()))).drop_duplicates("k").drop(columns="k")
            out = [{"lon": round(float(r.lon), 5), "lat": round(float(r.lat), 5), "name": ("" if r.name in ("nan", "None") else r.name)} for r in df.itertuples()]
        cache.write_text(json.dumps(out))
        return out
    except Exception as ex:                    # an odd graph file must not break the whole result
        log(f"Could not read bridges: {ex}")
        return []

def compute(model, bridges=None, seed=None):
    """Fires, gas leaks, power cuts and bridge closures for the earthquake run on the map. Returns a plain-types dict for the page."""
    if model.last is None:
        return {"supported": False, "reason": "Run a simulation first."}
    if getattr(model, "hazard", None) != "earthquake":
        return {"supported": False, "reason": "Knock-on failures are modelled for earthquakes only (fire, gas, power and bridges depend on shaking)."}
    L = model.last
    n = len(L)
    pe, pc = L["p_extensive"].to_numpy(float), L["p_collapse"].to_numpy(float)
    occ = L["occupants"].to_numpy(float)
    run = int(model.run_id)
    rng = np.random.default_rng(run if seed is None else seed)
    xy = np.column_stack([model.x, model.y])
    tree = cKDTree(xy)

    # ---- fires and gas ----
    def pick(q, cap):
        idx = np.flatnonzero(rng.random(n) < q)
        total = len(idx)
        if total > cap:
            idx = np.sort(rng.choice(idx, cap, replace=False))
        return idx, total
    fi, fire_total = pick(IGN_EXT * pe + IGN_COL * pc, MAX_FIRES)
    gi, gas_total = pick(GAS_EXT * pe + GAS_COL * pc, MAX_GAS)
    ignite = gi[rng.random(len(gi)) < GAS_TO_FIRE]
    fires_idx = np.concatenate([fi, ignite]).astype(int)
    t0 = np.concatenate([rng.uniform(0, FIRE_START_MIN, len(fi)), rng.uniform(10, 60, len(ignite))])
    from_gas = np.concatenate([np.zeros(len(fi), bool), np.ones(len(ignite), bool)])
    if len(fires_idx):
        ref = max(1.0, float(np.mean(tree.query_ball_point(xy[rng.choice(n, min(n, 1500), replace=False)], 150.0, return_length=True))))
        dens = np.asarray(tree.query_ball_point(xy[fires_idx], 150.0, return_length=True), float)
        v = FIRE_V_M_MIN * np.clip(dens / ref, 0.5, 2.0)
    else:
        v = np.array([])
    fires = [{"lon": round(float(model.lon[i]), 5), "lat": round(float(model.lat[i]), 5), "t0": round(float(t), 1), "v": round(float(s), 2), "gas": bool(g)}
             for i, t, s, g in zip(fires_idx, t0, v, from_gas)]
    threatened = {}
    for mark in FIRE_MARKS:
        hit = set()
        for k, i in enumerate(fires_idx):
            if t0[k] <= mark:
                r = min(FIRE_RMAX_M, FIRE_R0_M + v[k] * (mark - t0[k]))
                hit.update(tree.query_ball_point(xy[i], r))
        h = np.fromiter(hit, int, len(hit))
        threatened[str(mark)] = {"buildings": int(len(h)), "people": int(round(occ[h].sum())) if len(h) else 0}
    gas = [{"lon": round(float(model.lon[i]), 5), "lat": round(float(model.lat[i]), 5)} for i in gi]

    # ---- power: per 500 m cell ----
    key = (model.x // 500).astype(np.int64) * 100000 + (model.y // 500).astype(np.int64)
    pga = L["pga"].to_numpy(float)
    df = pd.DataFrame({"key": key, "lon": model.lon, "lat": model.lat, "pga": pga, "pe": pe, "occ": occ})
    c = df.groupby("key").agg(lon=("lon", "mean"), lat=("lat", "mean"), pga=("pga", "mean"), pe=("pe", "mean"), occ=("occ", "sum"))
    p_g = ndtr(np.log(np.maximum(c.pga.to_numpy(), 1e-6) / POWER_MEDIAN_G) / POWER_BETA)
    p_out = 1.0 - (1.0 - p_g) * (1.0 - POWER_DAMAGE_W * np.clip(c.pe.to_numpy(), 0, 1))
    dark = float((p_out * c.occ.to_numpy()).sum())
    power = [{"lon": round(float(a), 5), "lat": round(float(b), 5), "p": round(float(p), 2), "people": int(round(o))}
             for a, b, p, o in zip(c.lon, c.lat, p_out, c.occ) if p >= 0.05]

    # ---- bridges: shaking at a bridge is taken from the nearest building ----
    br = []
    if bridges:
        k = np.cos(np.radians(float(np.mean(model.lat))))
        ll = cKDTree(np.column_stack([model.lon * k, model.lat]))
        d, j = ll.query(np.column_stack([np.array([b["lon"] for b in bridges]) * k, [b["lat"] for b in bridges]]))
        for b, dist, i in zip(bridges, d, j):
            g = float(pga[int(i)]) if dist < 0.02 else 0.0           # farther than ~2 km from any building: outside the area, no result
            p = float(ndtr(np.log(max(g, 1e-6) / BRIDGE_MEDIAN_G) / BRIDGE_BETA)) if g > 0 else 0.0
            br.append({**b, "p": round(p, 2), "pga": round(g, 3)})
        br.sort(key=lambda x: -x["p"])

    n_br, n_closed, n_risk = len(br), sum(1 for b in br if b["p"] >= 0.5), sum(1 for b in br if b["p"] >= 0.15)
    br = br[:MAX_BRIDGES_DRAWN]                  # the page draws the most exposed ones; the totals below count them all
    return {"supported": True, "run": run, "fires": fires, "gas": gas, "power": power, "bridges": br,
            "marks": list(FIRE_MARKS), "fire_params": {"r0_m": FIRE_R0_M, "rmax_m": FIRE_RMAX_M},
            "summary": {"ignitions": fire_total + int(len(ignite)), "fires_shown": len(fires), "gas_leaks": gas_total, "gas_shown": len(gas),
                        "threatened": threatened, "people_without_power": int(round(dark)), "share_without_power": round(dark / max(float(occ.sum()), 1.0), 3),
                        "bridges": n_br, "bridges_likely_closed": n_closed, "bridges_at_risk": n_risk},
            "assumptions": ASSUMPTIONS}
