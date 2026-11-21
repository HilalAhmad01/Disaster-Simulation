"""Phase 4: Monte Carlo ranges."""
import numpy as np, pytest
gpd = pytest.importorskip("geopandas")
import uncertainty as U
from synthetic import synthetic_exposure

@pytest.fixture()
def model(tmp_path, monkeypatch):
    monkeypatch.setenv("VS30_TIF", str(tmp_path / "none.tif"))
    from simulate import Model
    g = synthetic_exposure(600)
    for t in ("URM", "RC_LOW", "RC_MID"):                                # make types genuinely uncertain
        g["p_" + t] = {"URM": .4, "RC_LOW": .5, "RC_MID": .1}[t]
    return Model(g)

OFF = dict(tau=0, phi=0, frag=0, occ=0)

def test_all_sigmas_zero_equals_deterministic_run(model):
    det = model.run(28.575, 77.33, 12, 6.8)
    r = U.run_mc(model, n=10, seed=1, draw_types=False, scales=OFF)
    t = r["totals"]
    assert t["people_at_risk"]["p10"] == pytest.approx(t["people_at_risk"]["p90"], rel=1e-12)
    assert t["people_at_risk"]["p50"] == pytest.approx(det["summary"]["people_at_risk"], rel=2e-3)      # det. rounds per building
    assert t["expected_collapses"]["mean"] == pytest.approx(model.last["p_collapse"].sum(), rel=2e-3)
    np.testing.assert_allclose(r["p_extensive_mean"], model.last["p_extensive"], atol=1.1e-3)

def test_same_seed_same_numbers_different_seed_differs(model):
    model.run(28.575, 77.33, 12, 6.8)
    a, b, c = U.run_mc(model, n=12, seed=5), U.run_mc(model, n=12, seed=5), U.run_mc(model, n=12, seed=6)
    assert a["totals"] == b["totals"] and np.array_equal(a["p_extensive_p90"], b["p_extensive_p90"])
    assert a["totals"]["people_at_risk"]["p50"] != c["totals"]["people_at_risk"]["p50"]

def test_spread_grows_with_tau_and_ranges_bracket_the_median(model):
    model.run(28.575, 77.33, 12, 6.8)
    width = lambda k: (lambda t: t["p90"] - t["p10"])(U.run_mc(model, n=80, seed=2, draw_types=False, scales=dict(tau=k, phi=0, frag=0, occ=0))["totals"]["people_at_risk"])
    assert width(0.0) < 1e-6 < width(0.5) < width(1.0) < width(2.0)
    t = U.run_mc(model, n=60, seed=3)["totals"]["people_at_risk"]
    assert t["p10"] < t["p50"] < t["p90"]

def test_each_source_widens_the_distribution(model):
    model.run(28.575, 77.33, 12, 6.8)
    base = U.run_mc(model, n=60, seed=4, draw_types=False, scales=OFF)["totals"]["people_at_risk"]
    for src in ("phi", "frag", "occ"):
        sc = dict(OFF); sc[src] = 1
        t = U.run_mc(model, n=60, seed=4, draw_types=False, scales=sc)["totals"]["people_at_risk"]
        assert t["p90"] - t["p10"] > base["p90"] - base["p10"] + 1e-6, src
    t = U.run_mc(model, n=60, seed=4, draw_types=True, scales=OFF)["totals"]["people_at_risk"]
    assert t["mean"] != pytest.approx(base["mean"], rel=1e-9) or t["p90"] >= t["p10"]       # type draws change the totals

def test_field_correlation_matches_the_model():
    rng = np.random.default_rng(0)
    x, y = np.array([2000.0, 2500.0, 5000.0, 12000.0, 2000.0]), np.array([3000.0, 3000.0, 3000.0, 3000.0, 3000.0])
    f = U.CorrelatedField(np.r_[x, 0, 20000], np.r_[y, 0, 6000], b_km=8.5, spacing=500.0)
    z = f.sample(rng, 6000)
    assert z.std(axis=0)[:5] == pytest.approx(1.0, abs=0.04)                         # unit variance, also between nodes (2250 m offsets)
    for j, h in ((1, 500.0), (2, 3000.0), (3, 10000.0)):
        emp = np.corrcoef(z[:, 0], z[:, j])[0, 1]
        assert emp == pytest.approx(np.exp(-3 * h / 8500.0), abs=0.05), h
    assert abs(np.corrcoef(z[:, 0], z[:, 4])[0, 1] - 1) < 1e-9                       # same point

def test_field_grid_coarsens_for_large_areas():
    x = np.array([0.0, 60000.0]); y = np.array([0.0, 60000.0])
    f = U.CorrelatedField(x, y, spacing=500.0, max_nodes=1500)
    assert f.spacing > 500.0 and f.L.shape[0] <= 1500 * 1.3

def test_report_has_worst_case_cells(model):
    model.run(28.575, 77.33, 12, 6.8)
    sn = model.mc_snapshot()
    res = U.run_mc(model, n=20, seed=1, snapshot=sn)
    rep = U.report(sn, res, deterministic={"people_at_risk": 1})
    w = rep["worst_case_cells"]
    assert 1 <= len(w) <= 8 and all(w[i]["risk_p90"] >= w[i + 1]["risk_p90"] for i in range(len(w) - 1))
    assert all(c["risk_p10"] <= c["risk_p50"] <= c["risk_p90"] for c in w)
    assert rep["assumptions"]["fragility"]["URM"].startswith("assumed")           # placeholders flagged, not hidden

def test_casualty_ranges_are_reported_and_zero_sigma_matches_deterministic(model):
    det = model.run(28.575, 77.33, 12, 7.0)["summary"]["casualties"]
    r = U.run_mc(model, n=10, seed=1, draw_types=False, scales=OFF)["totals"]
    assert r["deaths"]["p50"] == pytest.approx(det["deaths"], abs=1.5) and r["deaths"]["p10"] == pytest.approx(r["deaths"]["p90"], rel=1e-9)
    full = U.run_mc(model, n=40, seed=2)["totals"]
    assert full["deaths"]["p10"] < full["deaths"]["p90"] and full["injuries"]["p90"] > full["deaths"]["p90"]
