"""The drone's world, built on demand around where it flies: oriented boxes from building footprints, damage geometry (rubble) from the
hidden truth, road debris, and a numpy lidar. Nothing here needs torch.

A box row is [cx, cy, cz, hx, hy, hz, yaw, kind, src] in the area's local east-north-up frame (metres, z up, ground at 0):
kind 0 intact building, 1 partly collapsed building, 2 rubble, 3 road debris. `src` is the building id (or the road-edge id for debris).

SIMPLIFICATIONS: flat ground; a building is one rotated rectangle (97% of Noida footprints are within 40% of their rectangle);
rubble is random slabs, not simulated collapse; flat roofs.
"""
import math, random
from collections import OrderedDict
import numpy as np
import shapely
from scipy.spatial import cKDTree

K_INTACT, K_PARTIAL, K_RUBBLE, K_DEBRIS = 0, 1, 2, 3


class AreaIndex:
    """Local-frame arrays for every building of the loaded area (centroids, extents, heights) and a KD-tree over the centroids."""

    def __init__(self, model, frame, cfg):
        self.model, self.frame, self.cfg = model, frame, cfg
        g = model.g
        self.n = len(g)
        self.x, self.y = (np.asarray(v, float) for v in frame.to_local(model.lon, model.lat))
        b = g.geometry.bounds.to_numpy()
        x0, y0 = frame.to_local(b[:, 0], b[:, 1]); x1, y1 = frame.to_local(b[:, 2], b[:, 3])
        self.half_x = np.maximum(np.abs(np.asarray(x1) - np.asarray(x0)) / 2, 2.0)
        self.half_y = np.maximum(np.abs(np.asarray(y1) - np.asarray(y0)) / 2, 2.0)
        lv = g["levels"].to_numpy(float) if "levels" in g else np.full(self.n, np.nan)
        self.height = np.where(np.isfinite(lv) & (lv > 0), lv * cfg.storey_m, cfg.default_height_m)
        self.tree = cKDTree(np.column_stack([self.x, self.y]))
        self._obb: dict[int, tuple] = {}

    def within(self, x: float, y: float, r: float) -> np.ndarray:
        return np.asarray(self.tree.query_ball_point([x, y], r), dtype=np.int64)

    def in_chunk(self, i: int, j: int) -> np.ndarray:
        s = self.cfg.chunk_m
        c = self.within((i + 0.5) * s, (j + 0.5) * s, s * 0.7072 + 1.0)
        if not len(c):
            return c
        return c[(self.x[c] >= i * s) & (self.x[c] < (i + 1) * s) & (self.y[c] >= j * s) & (self.y[c] < (j + 1) * s)]

    def obb(self, ids) -> np.ndarray:
        """(len, 5) rows [cx, cy, hx, hy, yaw]: the minimum rotated rectangle of each footprint in the local frame (cached)."""
        ids = np.asarray(ids, dtype=np.int64)
        need = [int(i) for i in ids if int(i) not in self._obb]
        if need:
            geoms = np.asarray(self.model.g.geometry.values[need], dtype=object)
            loc = shapely.transform(geoms, lambda c: np.column_stack([np.asarray(v) for v in self.frame.to_local(c[:, 0], c[:, 1])]))
            rect = shapely.minimum_rotated_rectangle(loc)
            for i, r in zip(need, rect):
                self._obb[i] = self._rect_params(r, i)
        return np.array([self._obb[int(i)] for i in ids]).reshape(-1, 5)

    def _rect_params(self, r, i):
        if r.geom_type != "Polygon" or r.is_empty:
            return (self.x[i], self.y[i], self.half_x[i], self.half_y[i], 0.0)
        pts = np.asarray(r.exterior.coords)[:4]
        e1, e2 = pts[1] - pts[0], pts[2] - pts[1]
        if np.hypot(*e1) < np.hypot(*e2):
            e1, e2 = e2, e1
        c = pts.mean(0)
        return (float(c[0]), float(c[1]), max(np.hypot(*e1) / 2, 1.0), max(np.hypot(*e2) / 2, 1.0), float(np.arctan2(e1[1], e1[0])))


class RoadIndex:
    """Street edges in the local frame: midpoints (KD-tree) and polylines, for debris, scanning roads and the browser's road layer."""

    def __init__(self, net, frame):
        self.net = net
        self.n = net.n_edges
        ox, oy = frame.ox, frame.oy
        if net.utm is not None and str(net.utm).split(":")[-1] != str(frame.epsg):
            from pyproj import Transformer
            tr = Transformer.from_crs(net.utm, frame.epsg, always_xy=True)
            self._shift = lambda xy: np.column_stack(tr.transform(xy[:, 0], xy[:, 1])) - [ox, oy]
        else:
            self._shift = lambda xy: xy - [ox, oy]
        self._pts: dict[int, np.ndarray] = {}
        if self.n:
            mid = self._shift(shapely.get_coordinates(shapely.line_interpolate_point(np.asarray(net.geom, dtype=object), 0.5, normalized=True)))   # vectorised
        else:
            mid = np.zeros((0, 2))
        self.mx, self.my = mid[:, 0], mid[:, 1]
        self.tree = cKDTree(mid) if self.n else None
        self.road_id = np.asarray(net.road_id, int)

    def polyline(self, e: int) -> np.ndarray:
        p = self._pts.get(e)
        if p is None:
            p = self._pts[e] = self._shift(np.asarray(self.net.geom[e].coords, float)[:, :2])
        return p

    def near(self, x: float, y: float, r: float) -> np.ndarray:
        if self.tree is None:
            return np.zeros(0, np.int64)
        return np.asarray(self.tree.query_ball_point([x, y], r), dtype=np.int64)

    def in_chunk(self, i: int, j: int, size: float) -> np.ndarray:
        if self.tree is None:
            return np.zeros(0, np.int64)
        e = self.near((i + 0.5) * size, (j + 0.5) * size, size * 0.7072 + 1.0)
        if not len(e):
            return e
        return e[(self.mx[e] >= i * size) & (self.mx[e] < (i + 1) * size) & (self.my[e] >= j * size) & (self.my[e] < (j + 1) * size)]


class Chunk:
    def __init__(self, i, j):
        self.i, self.j = i, j
        self.boxes = np.zeros((0, 9))          # see the module docstring
        self.roads: list[dict] = []
        self.building_ids = np.zeros(0, np.int64)

    def json(self) -> dict:
        return {"id": f"c{self.i}_{self.j}", "i": self.i, "j": self.j, "boxes": np.round(self.boxes, 2).tolist(), "roads": self.roads}


class World:
    """Builds chunks on demand (cached, LRU) from the index, the truth and the road index."""

    def __init__(self, index: AreaIndex, truth, cfg, roads: RoadIndex | None = None, lru: int = 96):
        self.index, self.truth, self.cfg, self.roads = index, truth, cfg, roads
        self._cache: OrderedDict[tuple, Chunk] = OrderedDict()
        self.lru = lru

    def set_truth(self, truth):
        self.truth = truth
        self._cache.clear()

    # ------------------------------------------------------------------ chunk building
    def chunk(self, i: int, j: int) -> Chunk:
        k = (i, j)
        c = self._cache.get(k)
        if c is not None:
            self._cache.move_to_end(k)
            return c
        c = self._build(i, j)
        self._cache[k] = c
        while len(self._cache) > self.lru:
            self._cache.popitem(last=False)
        return c

    def _build(self, i, j) -> Chunk:
        cfg, idx, tr = self.cfg, self.index, self.truth
        ch = Chunk(i, j)
        ids = idx.in_chunk(i, j)
        ch.building_ids = ids
        rows = []
        if len(ids):
            obb = idx.obb(ids)
            for b, (cx, cy, hx, hy, yaw) in zip(ids, obb):
                rows.extend(self._building_rows(int(b), cx, cy, hx, hy, yaw))
        if self.roads is not None:
            for e in self.roads.in_chunk(i, j, cfg.chunk_m):
                e = int(e)
                pl = self.roads.polyline(e)
                width = float(self.roads.net.width[e])
                ch.roads.append({"edge": e, "pts": np.round(pl, 1).tolist(), "width": round(width, 1)})
                if len(tr.edge_blocked) and tr.edge_blocked[e]:
                    rows.extend(self._debris_rows(e, pl, width))
        ch.boxes = np.array(rows, float).reshape(-1, 9)
        return ch

    def _building_rows(self, b, cx, cy, hx, hy, yaw):
        cfg = self.cfg
        h = float(self.index.height[b])
        s = int(self.truth.state[b])
        if s == 0:
            return [[cx, cy, h / 2, hx, hy, h / 2, yaw, K_INTACT, b]]
        rng = random.Random(cfg.seed * 1_000_003 + b)               # cheap, deterministic per building (numpy generators cost ~30 us to create)
        out = []
        if s == 1:
            hh = h * rng.uniform(0.4, 0.7)
            out.append([cx, cy, hh / 2, hx, hy, hh / 2, yaw, K_PARTIAL, b])
            n_r = rng.randint(2, 4); spread = 2.0
        else:
            n_r = rng.randint(5, 12); spread = 5.0
        cap = 0.25 * h if s == 2 else 0.35 * h
        c, sn = math.cos(yaw), math.sin(yaw)
        for _ in range(n_r):
            ox, oy = rng.uniform(-hx - spread, hx + spread), rng.uniform(-hy - spread, hy + spread)
            wx, wy = cx + c * ox - sn * oy, cy + sn * ox + c * oy
            sx, sy = rng.uniform(0.6, 2.8), rng.uniform(0.6, 2.8)
            sz = max(0.15, min(rng.uniform(0.2, 1.1), cap / 2))
            out.append([wx, wy, sz, sx, sy, sz, rng.uniform(0, math.pi), K_RUBBLE, b])
        return out

    def _debris_rows(self, e, pl, width):
        rng = random.Random(self.cfg.seed * 1_000_003 + 7_000_000 + e)
        seg = np.diff(pl, axis=0); ln = np.hypot(seg[:, 0], seg[:, 1])
        if ln.sum() < 1:
            return []
        cum = np.cumsum(ln)
        out = []
        for _ in range(rng.randint(2, 4)):
            t = rng.uniform(0.2, 0.8) * ln.sum()
            k = min(int(np.searchsorted(cum, t)), len(seg) - 1)
            frac = (t - (cum[k] - ln[k])) / max(ln[k], 1e-6)
            p = pl[k] + seg[k] * frac
            yaw = math.atan2(seg[k][1], seg[k][0]) + math.pi / 2          # lies across the carriageway
            out.append([p[0], p[1], 0.7, max(width / 2, 2.0), rng.uniform(0.8, 1.6), 0.7, yaw, K_DEBRIS, e])
        return out

    # ------------------------------------------------------------------ queries
    def chunks_around(self, x: float, y: float, r: float) -> list[Chunk]:
        s = self.cfg.chunk_m
        i0, i1 = int(math.floor((x - r) / s)), int(math.floor((x + r) / s))
        j0, j1 = int(math.floor((y - r) / s)), int(math.floor((y + r) / s))
        return [self.chunk(i, j) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)]

    def boxes_near(self, x: float, y: float, r: float) -> np.ndarray:
        """All boxes whose footprint comes within r of (x, y) (conservative, by bounding circle)."""
        parts = [c.boxes for c in self.chunks_around(x, y, r) if len(c.boxes)]
        if not parts:
            return np.zeros((0, 9))
        b = np.concatenate(parts)
        rad = np.hypot(b[:, 3], b[:, 4])
        return b[np.hypot(b[:, 0] - x, b[:, 1] - y) - rad < r]

    def clearance(self, x: float, y: float, z: float, r: float = 40.0) -> tuple[float, int]:
        """Distance from a point to the nearest solid (box or ground) and the building id of that box (-1 ground)."""
        b = self.boxes_near(x, y, r)
        best, who = z, -1                       # ground at z = 0
        if len(b):
            d = box_distance(np.array([x, y, z]), b)
            k = int(np.argmin(d))
            if d[k] < best:
                best, who = float(d[k]), int(b[k, 8])
        return best, who

    def lidar(self, pos, yaw: float = 0.0, rng_m: float | None = None, n_az: int | None = None, el_deg=(-75, -55, -35, -15, 0)):
        """One 360-degree scan from `pos` (x, y, z). Returns hit points (N, 3), the box kind (N,) and its src (N,). Ground hits are kind -1."""
        cfg = self.cfg
        r = rng_m or cfg.lidar_range_m
        d = scan_dirs(n_az or cfg.lidar_az, el_deg, yaw)
        b = self.boxes_near(pos[0], pos[1], r)
        t, which = raycast(np.asarray(pos, float), d, b, r)
        hit = np.isfinite(t)
        pts = np.asarray(pos, float) + d[hit] * t[hit, None]
        w = which[hit]
        if len(b):
            safe = np.maximum(w, 0)
            kind = np.where(w >= 0, b[safe, 7], -1).astype(int)
            src = np.where(w >= 0, b[safe, 8], -1).astype(int)
        else:
            kind = np.full(len(pts), -1); src = np.full(len(pts), -1)
        return pts, kind, src


# ---------------------------------------------------------------------- geometry kernels
def scan_dirs(n_az: int, el_deg, yaw: float = 0.0) -> np.ndarray:
    az = yaw + np.linspace(0, 2 * np.pi, n_az, endpoint=False)
    el = np.deg2rad(np.asarray(el_deg, float))
    A, E = np.meshgrid(az, el, indexing="ij")
    return np.stack([np.cos(E) * np.cos(A), np.cos(E) * np.sin(A), np.sin(E)], -1).reshape(-1, 3)


def raycast(o: np.ndarray, d: np.ndarray, boxes: np.ndarray, max_range: float):
    """Rays (R, 3) from o against oriented boxes (B, 9) and the ground plane. Returns (t (R,), which (R,)): the nearest hit distance
    (inf = none within range) and the box row hit (-1 = ground, -2 = nothing)."""
    R = len(d)
    with np.errstate(divide="ignore", invalid="ignore"):
        tg = np.where(d[:, 2] < -1e-9, -o[2] / d[:, 2], np.inf)
    if not len(boxes):
        t = np.where(tg <= max_range, tg, np.inf)
        return t, np.where(np.isfinite(t), -1, -2)
    ctr, half, yaw = boxes[:, :3], boxes[:, 3:6], boxes[:, 6]
    c, s = np.cos(-yaw), np.sin(-yaw)
    rel = o[None, :] - ctr
    ox = c * rel[:, 0] - s * rel[:, 1]; oy = s * rel[:, 0] + c * rel[:, 1]; oz = rel[:, 2]
    dx = c[None] * d[:, 0:1] - s[None] * d[:, 1:2]; dy = s[None] * d[:, 0:1] + c[None] * d[:, 1:2]; dz = np.broadcast_to(d[:, 2:3], dx.shape)
    with np.errstate(divide="ignore", invalid="ignore"):
        def slab(o_, d_, h):
            t1, t2 = (-h - o_) / d_, (h - o_) / d_
            lo, hi = np.minimum(t1, t2), np.maximum(t1, t2)
            inside = (np.abs(o_) <= h) & (np.abs(d_) < 1e-12)          # parallel and inside the slab: no constraint
            return np.where(inside, -np.inf, lo), np.where(inside, np.inf, hi)
        tx0, tx1 = slab(ox[None], dx, half[None, :, 0])
        ty0, ty1 = slab(oy[None], dy, half[None, :, 1])
        tz0, tz1 = slab(oz[None], dz, half[None, :, 2])
    tmin = np.maximum.reduce([tx0, ty0, tz0]); tmax = np.minimum.reduce([tx1, ty1, tz1])
    ok = np.isfinite(tmin) & (tmax >= np.maximum(tmin, 0)) & (tmin <= max_range)
    t = np.where(ok, np.maximum(tmin, 0), np.inf)
    bi = t.argmin(1)
    tb = t[np.arange(R), bi]
    use_g = tg < tb
    tt = np.minimum(tb, tg)
    which = np.where(use_g, -1, np.where(np.isfinite(tb), bi, -2))
    tt = np.where(tt <= max_range, tt, np.inf)
    return tt, np.where(np.isfinite(tt), which, -2)


def box_distance(p: np.ndarray, boxes: np.ndarray) -> np.ndarray:
    """Euclidean distance from point p to each oriented box (0 inside)."""
    c, s = np.cos(-boxes[:, 6]), np.sin(-boxes[:, 6])
    rel = p[None, :] - boxes[:, :3]
    q = np.stack([c * rel[:, 0] - s * rel[:, 1], s * rel[:, 0] + c * rel[:, 1], rel[:, 2]], 1)
    out = np.maximum(np.abs(q) - boxes[:, 3:6], 0.0)
    return np.sqrt((out ** 2).sum(1))
