"""Monte Carlo ranges for a scenario (MASTER_PLAN Phase 4).

Sources of uncertainty, sampled once per realisation:
 1. between-event ground-motion term  eta ~ N(0, tau^2)  (one draw for the whole event; tau from the model mix)
 2. within-event field                 phi * spatially correlated N(0,1), Jayaram & Baker (2009): rho(h) = exp(-3 h / b)
 3. between-model spread is already inside sigma/phi (gmm.pga adds it)
 4. fragility parameters: a draw from the Phase 3 bootstrap samples when the class has them, otherwise a lognormal
    perturbation of the medians (FRAG_LN_SD, an ASSUMPTION flagged in the result because the shipped curves are uncalibrated)
 5. construction type: each building's type is drawn from its probabilities (draw_types=False keeps the expected mix)
 6. occupancy: one lognormal factor (OCC_LN_SD, an assumption: GHS-POP total uncertainty) on the people numbers
Damage is kept as probabilities (expected counts per realisation), so the spread reflects the uncertain inputs, not
binomial sampling noise. Seeded: the same seed gives the same numbers.
"""
import numpy as np, pandas as pd
from scipy.linalg import cholesky
from scipy.special import ndtr
import simulate as S, casualty as K

B_KM = 8.5            # correlation range (km) for PGA; Jayaram & Baker 2009 give ~8.5 km for clustered Vs30 (~40.7 km if not)
FRAG_LN_SD = 0.25     # ASSUMPTION used when no fragility bootstrap samples exist: ln-sd of each median (one common draw per class)
OCC_LN_SD = 0.20      # ASSUMPTION: ln-sd of the people multiplier (GHS-POP vs reality)
MAX_NODES = 6000      # above this the correlation grid coarsens (Cholesky cost grows as nodes^3)
PCT = (10, 50, 90)

class CorrelatedField:
    """Stationary unit-variance Gaussian field with correlation exp(-3 h / b) on a regular grid (Cholesky, built once),
    read at arbitrary points by bilinear interpolation of the 4 surrounding nodes. The interpolated value is divided by its
    exact standard deviation (from the corner correlations) so the variance stays 1 between nodes."""
    def __init__(self, x, y, b_km=B_KM, spacing=500.0, max_nodes=MAX_NODES):
        x, y = np.asarray(x, float), np.asarray(y, float)
        self.b = b_km * 1000.0
        w, h = x.max() - x.min() + 2 * spacing, y.max() - y.min() + 2 * spacing
        while (w / spacing + 1) * (h / spacing + 1) > max_nodes:
            spacing *= 1.5
        self.spacing = spacing
        self.x0, self.y0 = x.min() - spacing, y.min() - spacing
        self.nx, self.ny = int(w / spacing) + 2, int(h / spacing) + 2
        gx, gy = np.meshgrid(self.x0 + spacing * np.arange(self.nx), self.y0 + spacing * np.arange(self.ny))
        P = np.column_stack([gx.ravel(), gy.ravel()])
        d = np.sqrt(((P[:, None, :] - P[None, :, :]) ** 2).sum(-1))
        self.L = cholesky(np.exp(-3.0 * d / self.b) + 1e-9 * np.eye(len(P)), lower=True)
        fx, fy = (x - self.x0) / spacing, (y - self.y0) / spacing
        i0, j0 = np.floor(fx).astype(int), np.floor(fy).astype(int)
        tx, ty = fx - i0, fy - j0
        self.idx = [(j0 + dj) * self.nx + (i0 + di) for dj in (0, 1) for di in (0, 1)]       # corners (0,0) (1,0) (0,1) (1,1)
        self.w = [(1 - tx) * (1 - ty), tx * (1 - ty), (1 - tx) * ty, tx * ty]
        r1, r2 = np.exp(-3.0 * spacing / self.b), np.exp(-3.0 * spacing * np.sqrt(2) / self.b)
        rho = np.array([[1, r1, r1, r2], [r1, 1, r2, r1], [r1, r2, 1, r1], [r2, r1, r1, 1]])
        var = sum(self.w[a] * self.w[b] * rho[a, b] for a in range(4) for b in range(4))
        self.norm = 1.0 / np.sqrt(var)

    def sample(self, rng, n=1):
        """n independent fields at the points, shape (n, N)."""
        z = self.L @ rng.standard_normal((self.L.shape[0], n))
        out = sum(self.w[k][None, :] * z[self.idx[k], :].T for k in range(4)) * self.norm[None, :]
        return out

def _fragility_draws(rng, frag, beta, samples):
    """One draw of (medians, beta) per class. Bootstrap samples (rows theta_1..4, beta) when available, else lognormal noise."""
    f, b, src = {}, {}, {}
    for t in S.TYPES:
        s = samples.get(t) if samples else None
        if s:
            row = np.asarray(s)[rng.integers(len(s))]
            f[t], b[t], src[t] = tuple(row[:4]), float(row[4]), "bootstrap"
        else:
            k = float(np.exp(FRAG_LN_SD * rng.standard_normal()))
            f[t], b[t], src[t] = tuple(np.asarray(frag[t]) * k), float(beta[t]), f"assumed +-{FRAG_LN_SD} ln-sd"
    return f, b, src

def run_mc(model, n=200, seed=0, b_km=B_KM, draw_types=True, scales=None, progress=None, snapshot=None, field=None):
    """Monte Carlo over the last scenario of `model` (or `snapshot` = model.mc_snapshot()). scales: optional dict multiplying
    the sources (keys tau, phi, frag, occ, between) to switch them off or inflate them in tests. Returns a JSON-friendly dict."""
    sn = snapshot or model.mc_snapshot()
    sc = {"tau": 1.0, "phi": 1.0, "frag": 1.0, "occ": 1.0}
    sc.update(scales or {})
    rng = np.random.default_rng(seed)
    N = len(sn["mu"])
    W, cum = sn["W"], np.cumsum(sn["W"], axis=1)
    occ = sn["occ"]
    field = field if field is not None else (None if sc["phi"] == 0 else model.correlation_field(b_km))
    cell = sn["cell"]                                                   # int cell index per building
    ncell = int(cell.max()) + 1
    tot = {k: np.zeros(n) for k in ("people_at_risk", "expected_collapses", "moderate_or_worse", "deaths", "injuries")}
    rates, cparams = sn.get("casualty") or K.load()
    cell_risk = np.zeros((n, ncell), np.float32)
    pe_sum, pc_sum = np.zeros(N), np.zeros(N)
    pe_store = np.zeros((n, N), np.float16)
    fsrc = {}
    for r in range(n):
        eta = sc["tau"] * rng.standard_normal() * sn["tau"]
        eps = sc["phi"] * sn["phi"] * field.sample(rng, 1)[0] if field is not None else 0.0
        pga = np.exp(sn["mu"] + eta + eps)
        frag, beta, fsrc = _fragility_draws(rng, sn["frag"], sn["beta"], sn["samples"]) if sc["frag"] else (sn["frag"], sn["beta"], {})
        if sc["frag"] and sc["frag"] != 1.0:
            frag = {t: tuple(np.asarray(sn["frag"][t]) * (np.asarray(frag[t]) / np.asarray(sn["frag"][t])) ** sc["frag"]) for t in frag}
        if draw_types:
            u = rng.random(N)
            typ = (u[:, None] > cum).sum(1).clip(0, len(S.TYPES) - 1)
            Wr = np.zeros_like(W); Wr[np.arange(N), typ] = 1.0
        else:
            Wr = W
        p = S.damage_probs(pga, Wr, frag, beta)
        m = np.exp(sc["occ"] * OCC_LN_SD * rng.standard_normal() - 0.5 * (sc["occ"] * OCC_LN_SD) ** 2)    # mean-one multiplier
        risk = occ * m * p[:, 2]
        tot["people_at_risk"][r], tot["expected_collapses"][r], tot["moderate_or_worse"][r] = risk.sum(), p[:, 3].sum(), p[:, 1].sum()
        ct, _ = K.casualties(pga, Wr, occ * m, frag, beta, S.TYPES, rates, cparams)
        tot["deaths"][r], tot["injuries"][r] = ct[3], ct[:3].sum()
        cell_risk[r] = np.bincount(cell, weights=risk, minlength=ncell)
        pe_sum += p[:, 2]; pc_sum += p[:, 3]; pe_store[r] = p[:, 2]
        if progress and (r % 5 == 4 or r == n - 1):
            progress((r + 1) / n)
    pct = lambda a: {f"p{q}": float(np.percentile(a, q)) for q in PCT}
    cr = np.percentile(cell_risk, [10, 50, 90], axis=0)
    return {"n": n, "seed": seed, "b_km": b_km, "draw_types": draw_types,
            "totals": {k: {**pct(v), "mean": float(v.mean())} for k, v in tot.items()},
            "cell_risk": {"p10": cr[0], "p50": cr[1], "p90": cr[2]},
            "p_extensive_mean": pe_sum / n, "p_collapse_mean": pc_sum / n, "p_extensive_p90": np.percentile(pe_store, 90, axis=0).astype(np.float32),
            "assumptions": {"fragility": fsrc, "frag_ln_sd": FRAG_LN_SD, "occ_ln_sd": OCC_LN_SD, "tau_phi_from": "ground-motion model mix",
                            "field": f"exp(-3h/{b_km} km), grid {field.spacing:.0f} m" if field is not None else "off"}}

def report(snapshot, res, deterministic=None, top=8):
    """Small JSON-friendly summary for the API/UI: totals with 10/50/90% ranges, the deterministic (expected) values next to
    them, and the worst-case priority cells (top by 90th-percentile people at risk)."""
    cell, lat, lon = snapshot["cell"], snapshot["lat"], snapshot["lon"]
    nc = len(res["cell_risk"]["p90"])
    cnt = np.bincount(cell, minlength=nc)
    clat, clon = np.bincount(cell, weights=lat, minlength=nc) / cnt, np.bincount(cell, weights=lon, minlength=nc) / cnt
    order = np.argsort(-res["cell_risk"]["p90"])[:top]
    worst = [{"rank": i + 1, "lat": float(clat[c]), "lon": float(clon[c]), "n": int(cnt[c]), "risk_p10": int(round(res["cell_risk"]["p10"][c])),
              "risk_p50": int(round(res["cell_risk"]["p50"][c])), "risk_p90": int(round(res["cell_risk"]["p90"][c]))} for i, c in enumerate(order)]
    return {"run": snapshot["run"], "n": res["n"], "seed": res["seed"], "totals": res["totals"], "worst_case_cells": worst,
            "deterministic": deterministic, "assumptions": res["assumptions"], "scenario": snapshot["scenario"]}
