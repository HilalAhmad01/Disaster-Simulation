"""Skill scores for replaying past earthquakes (MASTER_PLAN 3.6): ranking and calibration measures, no model code."""
import numpy as np, pandas as pd

def auc(score, positive):
    """Area under the ROC curve = P(random damaged building scores higher than a random undamaged one); ties count half.
    Rank-based (Mann-Whitney). NaN if either class is empty."""
    s, y = np.asarray(score, float), np.asarray(positive, bool)
    n1, n0 = int(y.sum()), int((~y).sum())
    if n1 == 0 or n0 == 0:
        return float("nan")
    r = pd.Series(s).rank(method="average").to_numpy()
    return float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))

def auc_ci(score, positive, n=200, seed=0, block=None):
    """Bootstrap 90% interval for the AUC. block: optional cluster id per row (e.g. 500 m cell) so spatially clustered
    buildings are resampled together (narrower intervals would be too optimistic)."""
    rng = np.random.default_rng(seed)
    s, y = np.asarray(score, float), np.asarray(positive, bool)
    if block is None:
        idx = [rng.integers(0, len(s), len(s)) for _ in range(n)]
    else:
        b = pd.factorize(block)[0]
        groups = [np.where(b == k)[0] for k in range(b.max() + 1)]
        idx = [np.concatenate([groups[k] for k in rng.integers(0, len(groups), len(groups))]) for _ in range(n)]
    v = np.array([auc(s[i], y[i]) for i in idx])
    v = v[np.isfinite(v)]
    return float(np.percentile(v, 5)), float(np.percentile(v, 95))

def brier(prob, positive):
    p, y = np.asarray(prob, float), np.asarray(positive, float)
    return float(np.mean((p - y) ** 2))

def log_loss(prob, positive, eps=1e-6):
    p, y = np.clip(np.asarray(prob, float), eps, 1 - eps), np.asarray(positive, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))

def calibration_table(prob, positive, bins=8):
    """Predicted vs observed rate by quantile bin of the prediction."""
    d = pd.DataFrame({"p": np.asarray(prob, float), "y": np.asarray(positive, float)})
    d["bin"] = pd.qcut(d.p, bins, duplicates="drop")
    return d.groupby("bin", observed=True).agg(n=("y", "size"), predicted=("p", "mean"), observed=("y", "mean")).reset_index(drop=True)

def spearman(a, b):
    a, b = pd.Series(a), pd.Series(b)
    return float(a.rank().corr(b.rank())) if len(a) > 2 else float("nan")

def top_cell_hit_rate(pred, obs, k):
    """Share of the k cells with the highest observed damage that are among the k cells with the highest prediction."""
    pred, obs = np.asarray(pred, float), np.asarray(obs, float)
    k = min(k, len(pred))
    return float(len(set(np.argsort(-pred)[:k]) & set(np.argsort(-obs)[:k])) / k) if k else float("nan")

def cell_table(key, pred_heavy, observed_positive, min_n=10):
    """Aggregate graded buildings to cells: mean predicted P(damage) and observed damaged share, cells with >= min_n buildings."""
    d = pd.DataFrame({"key": key, "pred": pred_heavy, "obs": np.asarray(observed_positive, float)})
    t = d.groupby("key").agg(n=("obs", "size"), pred=("pred", "mean"), obs=("obs", "mean"))
    return t[t.n >= min_n].reset_index()
