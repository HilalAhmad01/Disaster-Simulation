"""The hidden outcome the drone discovers: one sampled damage state per building, survivors, blocked roads.

The simulation produces probabilities; a drone can only observe outcomes. So we draw ONE outcome from the model's own probabilities
(seeded, cached) and the drone finds it. In simulation this tests the *workflow* (finding, dispatching, updating), NOT the accuracy of
the fragility curves. The browser never receives this object (information hygiene, plan 9.5).

SIMPLIFICATION: spatial correlation comes from a shared normal draw per 500 m cell (`truth_cell_corr`), not from the Monte Carlo's
correlation field. Survivors are grouped per building. Outdoor people are only those who escaped a collapsed building.
"""
import numpy as np
from scipy.special import ndtr
from scipy.spatial import cKDTree

INTACT, PARTIAL, COLLAPSED = 0, 1, 2
STATE_NAME = ("intact", "partial", "collapsed")


class Truth:
    def __init__(self, index, model, cfg, seed: int | None = None, p_edge=None, road_id=None):
        self.cfg = cfg
        self.seed = cfg.seed if seed is None else int(seed)
        rng = np.random.default_rng(self.seed)
        L = model.last
        if L is None:
            raise ValueError("Run a simulation first.")
        n = len(L)
        pc = L["p_collapse"].to_numpy(float)
        pe = np.maximum(L["p_extensive"].to_numpy(float), pc)
        # ---- correlated uniform per building
        key = (model.x // 500).astype(np.int64) * 100000 + (model.y // 500).astype(np.int64)
        _, inv = np.unique(key, return_inverse=True)
        a = float(cfg.truth_cell_corr)
        z = np.sqrt(a) * rng.standard_normal(inv.max() + 1)[inv] + np.sqrt(1 - a) * rng.standard_normal(n)
        u = ndtr(z)
        self.state = np.where(u < pc, COLLAPSED, np.where(u < pe, PARTIAL, INTACT)).astype(np.int8)
        # ---- people
        tod = (getattr(model, "scenario", None) or {}).get("time_of_day", "night")
        occ = np.rint(model.occ_by.get(tod, model.occ_by["night"])).astype(np.int64)
        coll, part = self.state == COLLAPSED, self.state == PARTIAL
        trapped = np.zeros(n, np.int64)
        trapped[coll] = rng.binomial(occ[coll], cfg.p_trapped_collapsed)
        trapped[part] = rng.binomial(occ[part], cfg.p_trapped_partial)
        exposed = np.zeros(n, np.int64)
        exposed[coll] = rng.binomial(trapped[coll], cfg.p_exposed_given_trapped)
        exposed[part] = rng.binomial(trapped[part], cfg.p_exposed_given_trapped_partial)
        escaped = np.zeros(n, np.int64)
        escaped[coll] = rng.binomial(occ[coll] - trapped[coll], cfg.p_escape)
        self.occupants, self.trapped, self.exposed, self.escaped = occ, trapped, exposed, escaped
        # ---- survivors a drone can see: one entry per building and kind, with a head count
        sb, sk, sn = [], [], []
        for kind, arr in ((0, exposed), (1, np.minimum(escaped, 6))):
            ids = np.flatnonzero(arr > 0)
            sb.append(ids); sk.append(np.full(len(ids), kind)); sn.append(arr[ids])
        sb, sk, sn = (np.concatenate(v) if v else np.zeros(0, int) for v in (sb, sk, sn))
        r = np.where(sk == 0, 0.6, 1.4)                         # trapped sit in the rubble; escaped stand a little way out
        ang = rng.uniform(0, 2 * np.pi, len(sb))
        rad = r * np.sqrt(index.half_x[sb] ** 2 + index.half_y[sb] ** 2) * rng.uniform(0.2, 1.0, len(sb))
        self.s_building = sb.astype(np.int64)
        self.s_kind = sk.astype(np.int8)
        self.s_count = sn.astype(np.int64)
        self.s_x = index.x[sb] + np.cos(ang) * rad
        self.s_y = index.y[sb] + np.sin(ang) * rad
        self.s_found = np.zeros(len(sb), bool)
        self.s_tree = cKDTree(np.column_stack([self.s_x, self.s_y])) if len(sb) else None
        # ---- roads: one outcome per street (both directions of a street share road_id)
        self.road_id = None if road_id is None else np.asarray(road_id, int)
        if p_edge is not None and road_id is not None:
            p_edge = np.asarray(p_edge, float)
            nroad = int(self.road_id.max()) + 1
            p_road = np.zeros(nroad); np.maximum.at(p_road, self.road_id, p_edge)
            self.road_blocked = rng.random(nroad) < p_road
            self.edge_blocked = self.road_blocked[self.road_id]
        else:
            self.road_blocked = np.zeros(0, bool); self.edge_blocked = np.zeros(0, bool)

    # ---- statistics (debug and the evaluation harness only; never sent to a browser)
    def stats(self) -> dict:
        return {"collapsed": int((self.state == COLLAPSED).sum()), "partial": int((self.state == PARTIAL).sum()),
                "trapped": int(self.trapped.sum()), "exposed": int(self.exposed.sum()), "escaped": int(self.escaped.sum()),
                "survivor_sites": int(len(self.s_building)), "blocked_roads": int(self.road_blocked.sum())}

    def survivors_near(self, x: float, y: float, radius: float) -> np.ndarray:
        if self.s_tree is None:
            return np.zeros(0, int)
        return np.asarray(self.s_tree.query_ball_point([x, y], radius), dtype=int)
