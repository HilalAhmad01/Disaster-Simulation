"""Where is scanning most valuable per unit of flight energy? Not simply where predicted damage is highest:
a building already scanned is worth little, a building whose damage is uncertain (collapse chance near 50%) is worth scanning to learn,
and an open SOS call outweighs the model's estimate.

  victims_i = w_v * O_i * Pc_i * U_i              people we might find           (O occupants, Pc collapse chance, U = 1 - scan confidence)
  info_i    = w_d * 4 * Pc_i * (1 - Pc_i) * U_i   uncertainty we would resolve   (peaks at Pc = 0.5)
  sos_b     = w_sos * sum of weights of open SOS calls / unverified drone calls inside block b
  value(block) = sum_i (victims_i + info_i) + sos_b          score = value / energy it costs (planner.py)
"""
import numpy as np

_OFF = 5000


class Blocks:
    """The area cut into survey blocks (a grid aligned with the chunk grid). Blocks with too few buildings are not tasks."""

    def __init__(self, index, cfg):
        self.index, self.cfg = index, cfg
        s = cfg.block_m
        bi = np.floor(index.x / s).astype(np.int64); bj = np.floor(index.y / s).astype(np.int64)
        key = (bi + _OFF) * 100000 + (bj + _OFF)
        uk, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
        self.n = len(uk)
        self.ij = np.column_stack([uk // 100000 - _OFF, uk % 100000 - _OFF])
        self.inv, self.count = inv, cnt
        self.active = cnt >= cfg.min_buildings_per_block
        self.rect = np.column_stack([self.ij[:, 0] * s, self.ij[:, 1] * s, (self.ij[:, 0] + 1) * s, (self.ij[:, 1] + 1) * s])
        self.centre = np.column_stack([(self.rect[:, 0] + self.rect[:, 2]) / 2, (self.rect[:, 1] + self.rect[:, 3]) / 2])
        self.tallest = np.zeros(self.n); np.maximum.at(self.tallest, inv, index.height)
        self._at = {(int(a), int(b)): k for k, (a, b) in enumerate(self.ij)}

    def block_of(self, x: float, y: float) -> int | None:
        s = self.cfg.block_m
        return self._at.get((int(np.floor(x / s)), int(np.floor(y / s))))

    def id(self, k: int) -> str:
        return f"b{int(self.ij[k, 0])}_{int(self.ij[k, 1])}"

    def values(self, pc: np.ndarray, occ: np.ndarray, conf: np.ndarray, sos: list[tuple[float, float, float]] = ()) -> dict:
        """pc, occ, conf: per-building arrays (current collapse probability, occupants, scan confidence). sos: (x, y, weight) points."""
        cfg = self.cfg
        U = 1.0 - np.asarray(conf, float)
        victims_i = cfg.w_v * occ * pc * U
        info_i = cfg.w_d * 4.0 * pc * (1.0 - pc) * U
        victims = np.bincount(self.inv, weights=victims_i, minlength=self.n)
        info = np.bincount(self.inv, weights=info_i, minlength=self.n)
        sosv = np.zeros(self.n)
        for x, y, w in sos:
            k = self.block_of(x, y)
            if k is not None:
                sosv[k] += cfg.w_sos * w
        return {"value": victims + info + sosv, "victims": victims, "info": info, "sos": sosv}

    def top_buildings(self, k: int, pc, occ, conf, n: int = 3) -> list[int]:
        """The buildings contributing most to a block's value (for the 'Why' explanation)."""
        ids = np.flatnonzero(self.inv == k)
        U = 1.0 - conf[ids]
        c = occ[ids] * pc[ids] * U + 2 * 4 * pc[ids] * (1 - pc[ids]) * U
        return [int(ids[i]) for i in np.argsort(-c)[:n]]
