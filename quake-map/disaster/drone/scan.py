"""What the drone has seen: per-building scan confidence, observed damage (from the hidden truth through a noisy classifier), a 50 m
coverage grid, and probabilistic detection of people. Analytic (L0): a building is 'seen' while it is inside the camera footprint.

SIMPLIFICATIONS: a constant swath; no line-of-sight test for detections; the damage classifier is the truth through a confusion matrix
(`classify_building` is the single place a real point-cloud classifier would replace).
"""
import math
import numpy as np
from .truth import COLLAPSED, PARTIAL, INTACT

UNSCANNED, SEEN_PARTIAL, SCANNED = 0, 1, 2
STATE_NAME = ("unscanned", "partial", "scanned")
CELL_M = 50.0


def footprint_radius(cfg, alt: float) -> float:
    """Ground radius the camera covers: the constant swath/2 at working altitude, smaller when very low."""
    return float(min(cfg.swath_m / 2.0, 8.0 + max(alt, 0.0)))


def classify_building(true_state: int, accuracy: float, rng) -> int:
    """Observed damage class. Right with probability `accuracy`; errors go to an adjacent class."""
    if rng.random() < accuracy:
        return int(true_state)
    if true_state == INTACT:
        return PARTIAL
    if true_state == COLLAPSED:
        return PARTIAL
    return INTACT if rng.random() < 0.5 else COLLAPSED


def p_detect(cfg, agl: float) -> float:
    """Chance per detection tick of spotting an exposed person: linear from p_detect_low at <= 30 m to p_detect_high at >= 120 m."""
    f = min(max((agl - 30.0) / 90.0, 0.0), 1.0)
    return cfg.p_detect_low + (cfg.p_detect_high - cfg.p_detect_low) * f


class ScanState:
    def __init__(self, index, truth, cfg, seed: int = 0):
        self.index, self.truth, self.cfg = index, truth, cfg
        n = index.n
        self.conf = np.zeros(n, np.float32)
        self.state = np.zeros(n, np.int8)
        self.observed = np.full(n, -1, np.int8)               # -1 not observed, else 0 intact / 1 partial / 2 collapsed
        self.observed_t: dict[int, float] = {}
        self.observed_by: dict[int, str] = {}
        self.cells: set[tuple[int, int]] = set()
        self.rng = np.random.default_rng([cfg.seed, 99, seed])
        self.dist_flown_m = 0.0

    # ------------------------------------------------------------------ viewing
    def view(self, x: float, y: float, alt: float, speed: float, dt: float, sim_t: float, drone_id: str) -> dict:
        """The drone at (x, y, alt) looks for `dt` simulated seconds. Returns what changed: buildings, observations, new coverage cells."""
        cfg = self.cfg
        r = footprint_radius(cfg, alt)
        ids = self.index.within(x, y, r)
        changed, newly_partial, newly_scanned, obs = [], [], [], []
        if len(ids):
            d = np.hypot(self.index.x[ids] - x, self.index.y[ids] - y)
            q = np.clip(1.0 - 0.5 * d / r, 0.4, 1.0) * float(np.clip(1.25 - speed / 20.0, 0.5, 1.0))
            new = np.minimum(1.0, self.conf[ids] + cfg.scan_rate_per_s * dt * q).astype(np.float32)
            up = new > self.conf[ids] + 1e-6
            ids, new = ids[up], new[up]
            self.conf[ids] = new
            for b, c in zip(ids.tolist(), new.tolist()):
                s = SCANNED if c >= cfg.scanned_threshold else SEEN_PARTIAL if c >= cfg.partial_threshold else UNSCANNED
                if s > self.state[b]:
                    (newly_scanned if s == SCANNED else newly_partial).append(b)
                    self.state[b] = s
                    if s == SCANNED and self.observed[b] < 0:
                        o = classify_building(int(self.truth.state[b]), cfg.observation_accuracy, self.rng)
                        self.observed[b] = o
                        self.observed_t[b] = sim_t
                        self.observed_by[b] = drone_id
                        obs.append((b, o))
                changed.append(b)
        return {"radius": r, "changed": changed, "partial": newly_partial, "scanned": newly_scanned, "observations": obs,
                "cells": self._cover(x, y, r)}

    def _cover(self, x, y, r) -> list[tuple[int, int]]:
        out = []
        for ci in range(int((x - r) // CELL_M), int((x + r) // CELL_M) + 1):
            for cj in range(int((y - r) // CELL_M), int((y + r) // CELL_M) + 1):
                cx, cy = (ci + 0.5) * CELL_M, (cj + 0.5) * CELL_M
                if (cx - x) ** 2 + (cy - y) ** 2 <= (r + CELL_M * 0.35) ** 2 and (ci, cj) not in self.cells:
                    self.cells.add((ci, cj)); out.append((ci, cj))
        return out

    # ------------------------------------------------------------------ detection
    def detect(self, x: float, y: float, agl: float, speed: float, area_m2: float, sim_t: float, rng) -> list[dict]:
        """One detection tick: for each exposed survivor site in the footprint, a Bernoulli draw; plus false alarms at the configured
        rate over the area covered. Returns candidate dicts in local coordinates."""
        cfg, tr = self.cfg, self.truth
        r = footprint_radius(cfg, agl)
        out = []
        p = p_detect(cfg, agl) * cfg.detect_scale
        for k in tr.survivors_near(x, y, r):
            if rng.random() < p:
                tr.s_found[k] = True
                conf = float(np.clip(0.5 + 0.5 * p_detect(cfg, agl) + rng.normal(0, 0.04), 0.5, 0.99))
                out.append({"x": float(tr.s_x[k] + rng.normal(0, 1.5)), "y": float(tr.s_y[k] + rng.normal(0, 1.5)), "building": int(tr.s_building[k]),
                            "confidence": conf, "est_people": int(max(1, round(tr.s_count[k] * rng.uniform(0.7, 1.2)))), "true": True, "site": int(k)})
        lam = cfg.false_alarms_per_km2 * area_m2 / 1e6
        for _ in range(rng.poisson(lam)):
            ang, rad = rng.uniform(0, 2 * math.pi), r * math.sqrt(rng.uniform())
            fx, fy = x + rad * math.cos(ang), y + rad * math.sin(ang)
            near = self.index.within(fx, fy, 30.0)
            out.append({"x": float(fx), "y": float(fy), "building": int(near[0]) if len(near) else None, "confidence": float(rng.uniform(0.2, 0.5)),
                        "est_people": 1, "true": False, "site": -1})
        return out

    # ------------------------------------------------------------------ summaries
    def counts(self) -> dict:
        return {"unscanned": int((self.state == 0).sum()), "partial": int((self.state == 1).sum()), "scanned": int((self.state == 2).sum()),
                "observed_collapsed": int((self.observed == COLLAPSED).sum()), "observed_partial": int((self.observed == PARTIAL).sum()),
                "observed_intact": int((self.observed == INTACT).sum()), "cells": len(self.cells)}


class Neighbourhoods:
    """The priority neighbourhoods (the model's top 8 cells of 500 m by people at risk) and how much of each has been scanned."""

    def __init__(self, model, top: int = 8):
        L = model.last
        risk = L["risk"].to_numpy(float)
        key = (model.x // 500).astype(np.int64) * 100000 + (model.y // 500).astype(np.int64)
        uk, inv = np.unique(key, return_inverse=True)
        cell_risk = np.bincount(inv, weights=risk, minlength=len(uk))
        order = np.argsort(-cell_risk, kind="stable")[:top]
        self.rank_of_cell = {int(c): r + 1 for r, c in enumerate(order)}
        self.members = {r + 1: np.flatnonzero(inv == c) for r, c in enumerate(order)}
        self.centre = {r + 1: (float(model.lat[self.members[r + 1]].mean()), float(model.lon[self.members[r + 1]].mean())) for r in range(len(order))}

    def percent(self, conf: np.ndarray) -> dict[int, dict]:
        out = {}
        for r, ids in self.members.items():
            sc = int((conf[ids] >= 0.6).sum())
            out[r] = {"rank": r, "buildings": int(len(ids)), "scanned": sc, "pct": float(conf[ids].mean()) if len(ids) else 0.0,
                      "lat": self.centre[r][0], "lon": self.centre[r][1]}
        return out
