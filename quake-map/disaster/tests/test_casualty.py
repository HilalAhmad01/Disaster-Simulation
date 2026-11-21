import numpy as np, pytest
import casualty as K
from simulate import FRAG, BETA, TYPES

rates, params = K.load()

def one(pga, occ=100.0, t="URM"):
    W = np.zeros((1, 3)); W[0, TYPES.index(t)] = 1.0
    return K.casualties(np.array([pga]), W, np.array([occ]), FRAG, BETA, TYPES, rates, params)

def test_no_shaking_no_casualties_and_scales_with_people():
    tot, d = one(0.001)
    assert tot.sum() < 0.01 and d.sum() < 1e-6
    a, b = one(0.6, 100.0)[0], one(0.6, 300.0)[0]
    np.testing.assert_allclose(b, 3 * a)

def test_casualties_rise_with_shaking_and_deaths_follow_collapse():
    prev = -1
    for g in (0.1, 0.3, 0.6, 1.2):
        d = one(g)[0][3]
        assert d >= prev; prev = d
    tot = one(2.0)[0]                                                   # certain complete damage
    f = params["collapse_fraction_given_complete"]["URM"]
    expect = (1 - f) * rates[3] + f * rates[4]
    np.testing.assert_allclose(tot / 100.0, expect, rtol=0.02)           # ~all buildings complete, per person
    assert tot[0] > tot[1] > tot[2] > 0 and tot[3] > tot[2]             # severity pattern of the (placeholder) table

def test_mix_equals_weighted_average_of_pure_types():
    pga, occ = np.array([0.4]), np.array([50.0])
    mix = np.array([[0.3, 0.5, 0.2]])
    m = K.casualties(pga, mix, occ, FRAG, BETA, TYPES, rates, params)[0]
    parts = sum(w * K.casualties(pga, np.eye(3)[j][None, :], occ, FRAG, BETA, TYPES, rates, params)[0] for j, w in enumerate(mix[0]))
    np.testing.assert_allclose(m, parts, rtol=1e-12)

def test_pager_values_and_flag():
    th, be = params["pager_india"]["theta"], params["pager_india"]["beta"]
    assert K.pager_deaths(np.array([14.05]), np.array([1000.0]), params) == pytest.approx(500.0, rel=1e-6)     # median of the lognormal CDF
    assert K.pager_deaths(np.array([6.0, 8.0, 9.0]), np.ones(3), params) < 0.02
    s_ok = K.summary(np.array([0, 0, 0, 100.0]), 80.0, params); s_bad = K.summary(np.array([0, 0, 0, 1000.0]), 10.0, params)
    assert not s_ok["pager_disagreement"] and s_bad["pager_disagreement"] and not s_ok["verified"]

def test_model_reports_casualties_with_caveat(tmp_path, monkeypatch):
    monkeypatch.setenv("VS30_TIF", str(tmp_path / "none.tif"))
    from synthetic import synthetic_exposure
    from simulate import Model
    m = Model(synthetic_exposure(300))
    c = m.run(28.575, 77.33, 10, 7.2)["summary"]["casualties"]
    assert c["deaths"] > 0 and c["injury_1"] >= c["injury_2"] and c["verified"] is False and "UNVERIFIED" in c["caveat"]
