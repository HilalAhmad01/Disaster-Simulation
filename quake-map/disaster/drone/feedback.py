"""Drone observations improve a SECOND, drone-updated picture of the damage. The modelled baseline is never changed.

  observed building:  P'(d) ~ C[o | d] * P(d)  over d in {intact, partial, collapsed}, with an accuracy-based confusion matrix C.
  unscanned buildings near scanned ones: odds'_j = odds_j * clip(exp(lambda * z / sqrt(n)), lo, hi) where z compares observed with
      expected collapses among the scanned buildings of the 3x3 block of 500 m cells (a shared "fragility residual", conservative).
  aggregates: the same code as a normal run (Model.aggregate_cells) on the changed probabilities; casualties scale with the people
      expected in collapsed buildings (SIMPLIFICATION: not a full Hazus re-run).

The observations come from a hidden outcome drawn from this same model: this shows the workflow, not that the model is right.
"""
import numpy as np


def confusion(acc: float) -> np.ndarray:
    """C[o, d]: probability of observing class o when the truth is d (0 intact, 1 partial, 2 collapsed)."""
    e = 1.0 - acc
    return np.array([[acc, e / 2, 0.0],
                     [e, acc, e],
                     [0.0, e / 2, acc]])


def posterior(prior: np.ndarray, observed: int, acc: float) -> np.ndarray:
    post = confusion(acc)[observed] * prior
    s = post.sum()
    return post / s if s > 0 else prior


class DroneUpdated:
    def __init__(self, model, cfg):
        L = model.last
        self.model, self.cfg = model, cfg
        full = getattr(model, "p_last", None)
        self.base = np.array(full, float) if full is not None else np.column_stack([L["p_slight"].to_numpy(float), L["p_moderate"].to_numpy(float), L["p_extensive"].to_numpy(float), L["p_collapse"].to_numpy(float)])
        self.base.setflags(write=False)
        n = len(self.base)
        self.pc_obs = np.full(n, np.nan)                      # posterior P(collapsed) of observed buildings
        self.pe_obs = np.full(n, np.nan)
        key = (model.x // 500).astype(np.int64) * 100000 + (model.y // 500).astype(np.int64)
        self.cell_keys, self.cell = np.unique(key, return_inverse=True)
        a, b = self.cell_keys // 100000, self.cell_keys % 100000
        pos = {(int(i), int(j)): k for k, (i, j) in enumerate(zip(a, b))}
        self.nbrs = [[pos[(int(i) + di, int(j) + dj)] for di in (-1, 0, 1) for dj in (-1, 0, 1) if (int(i) + di, int(j) + dj) in pos] for i, j in zip(a, b)]
        self.factor = np.ones(len(self.cell_keys))
        self.dirty = False
        self.n_obs = 0

    def observe(self, b: int, observed: int) -> dict:
        """Strong update of one building. Returns before/after collapse probability."""
        pc0, pe0 = self.base[b, 3], max(self.base[b, 2], self.base[b, 3])
        prior = np.array([1 - pe0, pe0 - pc0, pc0])
        post = posterior(prior, observed, self.cfg.observation_accuracy)
        before = float(self.pc_obs[b]) if np.isfinite(self.pc_obs[b]) else float(pc0)
        if not np.isfinite(self.pc_obs[b]):
            self.n_obs += 1
        self.pc_obs[b] = float(post[2]); self.pe_obs[b] = float(post[1] + post[2])
        self.dirty = True
        return {"building": int(b), "before": before, "after": float(post[2])}

    def _neighbourhood_factors(self):
        obs = np.isfinite(self.pc_obs)
        pc0 = self.base[:, 3]
        nc = len(self.cell_keys)
        n_scan = np.bincount(self.cell, weights=obs, minlength=nc)
        exp = np.bincount(self.cell, weights=np.where(obs, pc0, 0.0), minlength=nc)
        var = np.bincount(self.cell, weights=np.where(obs, pc0 * (1 - pc0), 0.0), minlength=nc)
        got = np.bincount(self.cell, weights=np.where(obs, (self.pc_obs > 0.5).astype(float), 0.0), minlength=nc)
        cfg = self.cfg
        f = np.ones(nc)
        for c in range(nc):
            nb = self.nbrs[c]
            n = n_scan[nb].sum()
            if n < cfg.feedback_min_scanned:
                continue
            e, v, g = exp[nb].sum(), var[nb].sum(), got[nb].sum()
            z = (g - e) / np.sqrt(max(v, 1e-6))
            f[c] = float(np.clip(np.exp(cfg.feedback_lambda * z / np.sqrt(n)), cfg.feedback_clip_lo, cfg.feedback_clip_hi))
        return f

    def probabilities(self) -> np.ndarray:
        """The drone-updated (n, 4) exceedance probabilities [slight, moderate, extensive, collapse]; the baseline stays untouched."""
        p = self.base.copy()
        f = self._neighbourhood_factors() if self.cfg.feedback_enabled else np.ones(len(self.cell_keys))
        self.factor = f
        ff = f[self.cell]
        odds = p[:, 3] / np.maximum(1 - p[:, 3], 1e-9) * ff
        pc = odds / (1 + odds)
        scale = np.where(p[:, 3] > 1e-12, pc / np.maximum(p[:, 3], 1e-12), 1.0)
        p[:, 3] = pc
        p[:, 2] = np.maximum(np.minimum(p[:, 2] * np.where(ff > 1, np.minimum(scale, 1.0 / np.maximum(p[:, 2], 1e-9)), scale), 1.0), pc)
        p[:, 1] = np.maximum(p[:, 1], p[:, 2]); p[:, 0] = np.maximum(p[:, 0], p[:, 1])
        if self.cfg.feedback_enabled:
            o = np.isfinite(self.pc_obs)
            p[o, 3] = self.pc_obs[o]
            p[o, 2] = np.maximum(self.pe_obs[o], self.pc_obs[o])
            p[o, 1] = np.maximum(self.base[o, 1], p[o, 2]); p[o, 0] = np.maximum(self.base[o, 0], p[o, 1])
        return p

    def aggregates(self, updated: bool = True) -> dict:
        """Totals and map data for the drone-updated view, next to the modelled ones."""
        m = self.model
        L = m.last
        from hazards import registry
        hazard = registry()[m.hazard]
        tod = m.scenario.get("time_of_day", "night")
        occ = m.occ_by.get(tod, m.occ_by["night"])
        p = self.probabilities()
        shown = p if updated else self.base
        ds = shown.sum(1)
        cat = np.digitize(ds, [0.5, 1.5, 2.5])
        risk = occ * shown[:, 2]
        feats, heat = m.aggregate_cells(hazard, hazard.origin(m.scenario), ds, cat, risk, shown, getattr(m, "value_last", None) if getattr(m, "value_last", None) is not None else L["pga"].to_numpy(float))
        pb = self.base
        base = {"expected_collapses": float(pb[:, 3].sum()), "expected_heavy": float(pb[:, 2].sum()), "people_at_risk": float((occ * pb[:, 2]).sum())}
        upd = {"expected_collapses": float(p[:, 3].sum()), "expected_heavy": float(p[:, 2].sum()), "people_at_risk": float(risk.sum())}
        trapped_b, trapped_u = float((occ * pb[:, 3]).sum()), float((occ * p[:, 3]).sum())
        cas = (getattr(m, "summary", None) or {}).get("casualties")
        deaths = None
        if cas and trapped_b > 0:
            k = trapped_u / trapped_b
            deaths = {"modelled": cas.get("deaths"), "drone_updated": None if cas.get("deaths") is None else cas["deaths"] * k, "ratio": k}
        return {"modelled": base, "drone_updated": upd, "deaths": deaths, "observed_buildings": int(self.n_obs), "cells": feats, "heat": heat,
                "changed": float(np.abs(p - pb).sum())}
