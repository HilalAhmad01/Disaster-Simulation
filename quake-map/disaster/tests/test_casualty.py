import numpy as np, pytest
import casualty as K
from simulate import FRAG, BETA, TYPES
import simulate as S

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
    expect = (1 - f) * rates['URM'][3] + f * rates['URM'][4]
    np.testing.assert_allclose(tot / 100.0, expect, rtol=0.02)           # ~all buildings complete, per person
    assert tot[0] > tot[1] > tot[2] > 0 and tot[3] > tot[2]             # severity pattern of the Hazus table

def test_mix_equals_weighted_average_of_pure_types():
    pga, occ = np.array([0.4]), np.array([50.0])
    mix = np.array([[0.3, 0.5, 0.2]])
    m = K.casualties(pga, mix, occ, FRAG, BETA, TYPES, rates, params)[0]
    parts = sum(w * K.casualties(pga, np.eye(3)[j][None, :], occ, FRAG, BETA, TYPES, rates, params)[0] for j, w in enumerate(mix[0]))
    np.testing.assert_allclose(m, parts, rtol=1e-12)

def test_pager_values_and_flag():
    th, be = params["pager_india"]["theta"], params["pager_india"]["beta"]
    assert (th, be) == (11.53, 0.14)                                                                           # OF 2009-1136 country table
    assert K.pager_deaths(np.array([th]), np.array([1000.0]), params) == pytest.approx(500.0, rel=1e-6)     # median of the lognormal CDF
    assert K.pager_deaths(np.array([9.0]), np.array([25.0]), params) == pytest.approx(1.0, rel=0.1)         # report text: 1 death per ~25 people at IX
    assert K.pager_deaths(np.array([5.0, 6.0, 7.0]), np.ones(3), params) < 0.001                          # near zero below MMI VII
    s_ok = K.summary(np.array([0, 0, 0, 100.0]), 80.0, params); s_bad = K.summary(np.array([0, 0, 0, 1000.0]), 10.0, params)
    assert not s_ok["pager_disagreement"] and s_bad["pager_disagreement"] and s_ok["verified"]

def test_model_reports_casualties_with_caveat(tmp_path, monkeypatch):
    monkeypatch.setenv("VS30_TIF", str(tmp_path / "none.tif"))
    from synthetic import synthetic_exposure
    from simulate import Model
    m = Model(synthetic_exposure(300))
    c = m.run(28.575, 77.33, 10, 7.2)["summary"]["casualties"]
    assert c["deaths"] > 0 and c["injury_1"] >= c["injury_2"] and c["verified"] is True and "Hazus 6.1" in c["caveat"]


def test_rates_are_the_hazus_values_per_type():
    """Spot values copied from the Hazus 6.1 manual (percent there, fraction here): URML differs from C3L at moderate damage."""
    assert set(rates) == {"URM", "RC_LOW", "RC_MID"} and all(r.shape == (5, 4) for r in rates.values())
    np.testing.assert_allclose(rates["URM"][1], [0.0035, 0.004, 0.00001, 0.00001])        # Table 12-4 URML
    np.testing.assert_allclose(rates["RC_LOW"][1], [0.002, 0.00025, 0, 0])                # Table 12-4 C3L
    np.testing.assert_allclose(rates["URM"][4], [0.40, 0.20, 0.05, 0.10])                 # Table 12-7 collapse
    assert params["collapse_fraction_given_complete"]["RC_MID"] == 0.13                   # Table 12-8 C3M
    assert rates["URM"][1][1] > 10 * rates["RC_LOW"][1][1]                                # URML moderate-damage hospitalisations >> C3L

def test_one_type_fast_path_matches_the_general_one():
    """The Monte Carlo's one-type-per-building shortcut gives the same casualties as the general mixed-type code."""
    rates, params = K.load()
    rng = np.random.default_rng(3)
    n = 500
    pga, occ, typ = rng.lognormal(-1.2, 0.8, n), rng.uniform(0, 40, n), rng.integers(0, 3, n)
    W = np.eye(3)[typ]
    E = S.exceedance(pga, W)
    general = K.casualties_from(E, W, occ, TYPES, rates, params)[0]
    p = S.mix_exceedance(E, W, n)
    assert np.allclose(p, S.damage_probs(pga, W))
    assert np.allclose(K.totals_one_type(p, typ, occ, TYPES, rates, params), general, rtol=1e-12)
