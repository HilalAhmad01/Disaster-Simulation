import numpy as np, pytest
import validation as V

def test_auc_known_values():
    assert V.auc([1, 2, 3, 4], [0, 0, 1, 1]) == 1.0
    assert V.auc([4, 3, 2, 1], [0, 0, 1, 1]) == 0.0
    assert V.auc([1, 1, 1, 1], [0, 1, 0, 1]) == 0.5                       # all ties
    assert V.auc([1, 2, 2, 3], [0, 0, 1, 1]) == pytest.approx(0.875)      # ties count half
    assert np.isnan(V.auc([1, 2], [1, 1]))
    rng = np.random.default_rng(0); y = rng.random(4000) < .3
    assert V.auc(rng.random(4000), y) == pytest.approx(0.5, abs=0.03)
    assert V.auc(rng.random(4000) + y * 1.0, y) > 0.7

def test_auc_matches_brute_force():
    rng = np.random.default_rng(2); s = rng.integers(0, 6, 60).astype(float); y = rng.random(60) < .4
    pos, neg = s[y], s[~y]
    brute = np.mean([(a > b) + 0.5 * (a == b) for a in pos for b in neg])
    assert V.auc(s, y) == pytest.approx(brute)

def test_bootstrap_ci_brackets_auc_and_clusters_widen_it():
    rng = np.random.default_rng(1); cell = np.repeat(np.arange(40), 25)
    y = rng.random(1000) < (0.2 + 0.5 * (cell % 2)); s = y * 0.5 + rng.random(1000) + (cell % 2)
    a = V.auc(s, y); lo, hi = V.auc_ci(s, y, n=100); lo2, hi2 = V.auc_ci(s, y, n=100, block=cell)
    assert lo <= a <= hi and lo2 <= a <= hi2 and (hi2 - lo2) >= (hi - lo) * 0.8

def test_probability_scores():
    assert V.brier([1, 0], [1, 0]) == 0 and V.brier([0.5, 0.5], [1, 0]) == 0.25
    assert V.log_loss([0.9, 0.1], [1, 0]) < V.log_loss([0.6, 0.4], [1, 0])
    t = V.calibration_table(np.linspace(0, 1, 400), np.random.default_rng(0).random(400) < np.linspace(0, 1, 400), bins=4)
    assert (abs(t.predicted - t.observed) < 0.12).all()

def test_cell_ranking():
    assert V.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0) and V.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert V.top_cell_hit_rate([5, 4, 3, 2, 1], [5, 4, 1, 2, 3], 2) == 1.0
    assert V.top_cell_hit_rate([5, 4, 3, 2, 1], [1, 2, 3, 4, 5], 2) == 0.0
    t = V.cell_table(["a"] * 12 + ["b"] * 3, np.r_[np.full(12, .3), np.full(3, .9)], np.r_[np.ones(6), np.zeros(6), np.ones(3)], min_n=10)
    assert list(t.key) == ["a"] and t.obs[0] == 0.5
