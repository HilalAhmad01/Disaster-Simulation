"""Spike B (drone Phase 0): oriented-box raycast on real footprints, numpy only.

Builds oriented boxes (minimum rotated rectangle + height) for the densest 250 m chunks of a saved area, casts the 80-ray
scan the Drone-SLAM sim uses (16 azimuth x 5 elevation), checks hits against shapely on random horizontal rays, and times
one scan with and without a pre-cull. This function is the seed of the future drone/sim/raycast.py.

    python tools/spikes/spike_b_raycast.py [--area noida] [--range 60]
"""
import argparse, pathlib, sys, time
import numpy as np, shapely

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
STOREY_M, DEFAULT_H = 3.0, 6.0


def boxes_from_footprints(geoms_utm, levels, x0, y0):
    """-> centre (B,2), half extents (B,2), yaw (B,), height (B,) in a frame shifted by (x0, y0)."""
    rect = shapely.minimum_rotated_rectangle(geoms_utm)
    cx, cy, hx, hy, yaw = [], [], [], [], []
    for r in rect:
        pts = np.asarray(r.exterior.coords)[:4]
        e1, e2 = pts[1] - pts[0], pts[2] - pts[1]
        if np.hypot(*e1) < np.hypot(*e2):
            e1, e2 = e2, e1
        c = pts.mean(0)
        cx.append(c[0] - x0); cy.append(c[1] - y0)
        hx.append(np.hypot(*e1) / 2); hy.append(np.hypot(*e2) / 2)
        yaw.append(np.arctan2(e1[1], e1[0]))
    h = np.where(np.isfinite(levels) & (levels > 0), levels * STOREY_M, DEFAULT_H)
    return np.column_stack([cx, cy]), np.column_stack([hx, hy]), np.array(yaw), h


def raycast(o, d, ctr, half, yaw, height, max_range):
    """o (3,), d (R,3) unit -> t (R,) nearest hit distance (inf = none within range), id (R,) box index (-1 ground, -2 none)."""
    c, s = np.cos(-yaw), np.sin(-yaw)                                  # rotate world -> box frame
    rel = o[None, :2] - ctr                                           # (B,2)
    ox = c * rel[:, 0] - s * rel[:, 1]; oy = s * rel[:, 0] + c * rel[:, 1]                       # (B,)
    dx = c[None] * d[:, 0:1] - s[None] * d[:, 1:2]; dy = s[None] * d[:, 0:1] + c[None] * d[:, 1:2]   # (R,B)
    with np.errstate(divide="ignore", invalid="ignore"):
        def slab(o_, d_, lo, hi):
            t1, t2 = (lo - o_) / d_, (hi - o_) / d_
            return np.minimum(t1, t2), np.maximum(t1, t2)
        tx0, tx1 = slab(ox[None], dx, -half[None, :, 0], half[None, :, 0])
        ty0, ty1 = slab(oy[None], dy, -half[None, :, 1], half[None, :, 1])
        dz = d[:, 2:3]
        tz0, tz1 = slab(np.full_like(ox, o[2])[None], np.broadcast_to(dz, dx.shape), np.zeros_like(height)[None], height[None])
    # parallel rays: nan from 0/0 means "inside slab" when the origin is within it, else a miss
    tmin = np.fmax.reduce([tx0, ty0, tz0]); tmax = np.fmin.reduce([tx1, ty1, tz1])
    hit = (tmax >= np.maximum(tmin, 0)) & (tmin <= max_range)
    t = np.where(hit, np.maximum(tmin, 0), np.inf)
    bi = t.argmin(1); tb = t[np.arange(len(d)), bi]
    with np.errstate(divide="ignore"):
        tg = np.where(d[:, 2] < 0, -o[2] / d[:, 2], np.inf)
    use_g = tg < tb
    t_out = np.minimum(tb, tg)
    ids = np.where(use_g, -1, np.where(np.isfinite(tb), bi, -2))
    t_out = np.where(t_out <= max_range, t_out, np.inf)
    ids = np.where(np.isfinite(t_out), ids, -2)
    return t_out, ids


def scan_dirs(yaw=0.0, n_az=16, n_el=5, el_min=-60, el_max=15):
    az = yaw + np.linspace(0, 2 * np.pi, n_az, endpoint=False)
    el = np.deg2rad(np.linspace(el_min, el_max, n_el))
    A, E = np.meshgrid(az, el, indexing="ij")
    return np.stack([np.cos(E) * np.cos(A), np.cos(E) * np.sin(A), np.sin(E)], -1).reshape(-1, 3)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default="noida")
    ap.add_argument("--range", type=float, default=60.0)
    a = ap.parse_args()
    from areas import Registry
    g = Registry(ROOT / "data").load_exposure(a.area)
    utm = g.estimate_utm_crs()
    gu = g.geometry.to_crs(utm)
    cen = gu.centroid
    x, y = cen.x.to_numpy(), cen.y.to_numpy()
    lv = g["levels"].to_numpy(float)
    key = (x // 250).astype(np.int64) * 100000 + (y // 250).astype(np.int64)
    ck, cnt = np.unique(key, return_counts=True)
    top = ck[np.argsort(cnt)[::-1][:1]][0]
    cx0, cy0 = (top // 100000) * 250.0 + 125, (top % 100000) * 250.0 + 125
    near = np.flatnonzero((np.abs(x - cx0) < 250 + a.range) & (np.abs(y - cy0) < 250 + a.range))   # 3x3-ish neighbourhood
    print(f"densest chunk centre ({cx0:.0f},{cy0:.0f}); {int((key == top).sum())} buildings in chunk, {len(near)} within the active set")
    ctr, half, yaw, h = boxes_from_footprints(gu.geometry.to_numpy()[near], lv[near], cx0, cy0)
    print(f"box heights: median {np.median(h):.1f} m, p95 {np.percentile(h,95):.1f} m, max {h.max():.1f} m")

    # ---- correctness: horizontal rays at 1.5 m height vs shapely
    rng = np.random.default_rng(1)
    polys = shapely.box(-half[:, 0], -half[:, 1], half[:, 0], half[:, 1])
    polys = np.array([shapely.affinity.translate(shapely.affinity.rotate(p, np.degrees(yw), origin=(0, 0)), c[0], c[1]) for p, yw, c in zip(polys, yaw, ctr)])
    tree = shapely.STRtree(polys)
    errs, n_hit, n_tot = [], 0, 0
    for _ in range(1500):
        o = np.array([rng.uniform(-100, 100), rng.uniform(-100, 100), 1.5])
        if tree.query(shapely.Point(o[0], o[1]), predicate="within").size:
            continue
        ang = rng.uniform(0, 2 * np.pi)
        d = np.array([[np.cos(ang), np.sin(ang), 0.0]])
        t, ids = raycast(o, d, ctr, half, yaw, h, a.range)
        line = shapely.LineString([o[:2], o[:2] + d[0, :2] * a.range])
        cand = tree.query(line, predicate="intersects")
        n_tot += 1
        if cand.size:
            ref = min(shapely.distance(shapely.Point(o[:2]), polys[i].intersection(line)) for i in cand)
            n_hit += 1
            errs.append(abs(ref - t[0]) if np.isfinite(t[0]) else 1e9)
        elif np.isfinite(t[0]):
            errs.append(1e9)
    errs = np.array(errs)
    print(f"correctness vs shapely: {n_tot} rays, {n_hit} hit a building; max error {errs.max()*100:.2f} cm, share within 1 cm {np.mean(errs < 0.01):.1%}")

    # ---- timing: one 80-ray scan
    dirs = scan_dirs()
    for label, oz in (("above roofs (z=45 m)", 45.0), ("in a street (z=8 m)", 8.0)):
        o = np.array([5.0, 5.0, oz])
        for cull in (False, True):
            if cull:
                dist = np.hypot(ctr[:, 0] - o[0], ctr[:, 1] - o[1]) - np.hypot(half[:, 0], half[:, 1])
                sel = dist < a.range
            else:
                sel = np.ones(len(ctr), bool)
            ts = []
            for _ in range(200):
                t0 = time.perf_counter()
                if cull:
                    dist = np.hypot(ctr[:, 0] - o[0], ctr[:, 1] - o[1]) - np.hypot(half[:, 0], half[:, 1]); sel = dist < a.range
                raycast(o, dirs, ctr[sel], half[sel], yaw[sel], h[sel], a.range)
                ts.append(time.perf_counter() - t0)
            print(f"80-ray scan {label:22s} cull={'on ' if cull else 'off'} boxes={int(sel.sum()):4d}  median {np.median(ts)*1000:6.2f} ms  p95 {np.percentile(ts,95)*1000:6.2f} ms")
    print("\nbudget in the plan: <= 10 ms per scan on CPU; the app only scans at 2 Hz.")


if __name__ == "__main__":
    main()
