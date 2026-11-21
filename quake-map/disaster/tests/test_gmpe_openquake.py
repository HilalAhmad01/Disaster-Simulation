"""Our ground-motion equations must match OpenQuake to within 1%. Skipped when openquake is not installed
(use the separate environment described in tools/requirements-verify.txt)."""
import numpy as np, pytest
pytest.importorskip("openquake.hazardlib")
import gmpe
from verify_gmpe import oq_pga, grid
from openquake.hazardlib.gsim.boore_atkinson_2008 import BooreAtkinson2008
from openquake.hazardlib.imt import PGA

M, R, V = (np.array(x) for x in zip(*grid()))

@pytest.mark.parametrize("mech,rake", [("ss", 0.0), ("rs", 90.0), ("ns", -90.0)])
def test_ba08_pga_matches_openquake(mech, rake):
    oq = oq_pga(BooreAtkinson2008, M, R, V, rake)[0]
    rel = np.abs(gmpe.pga_g(M, R, V, mech) / oq - 1)
    assert rel.max() < 0.01, f"{mech}: worst relative difference {rel.max():.3%}"

def test_unspecified_mechanism_offset_matches_openquake_coefficients():
    """OpenQuake has no 'unspecified' case; on rock the offset to strike-slip must be exp(e1 - e2) from its own table."""
    c = BooreAtkinson2008.COEFFS[PGA()]
    e1, e2 = float(c['e1']), float(c['e2'])
    m, r = np.array([5.0, 6.5, 8.0]), np.array([5.0, 20.0, 80.0])
    oq_ss = oq_pga(BooreAtkinson2008, m, r, 760.0, 0.0)[0]
    np.testing.assert_allclose(gmpe.pga_g(m, r, 760.0, "unspec"), oq_ss * np.exp(e1 - e2), rtol=1e-3)

def test_standard_deviations_match_openquake():
    _, total, inter, intra = oq_pga(BooreAtkinson2008, np.array([6.5]), np.array([10.0]), 400.0, 0.0)
    assert gmpe.TAU == pytest.approx(inter[0]) and gmpe.PHI == pytest.approx(intra[0]) and gmpe.SIGMA == pytest.approx(total[0])
    assert gmpe.SIGMA == pytest.approx(np.hypot(gmpe.TAU, gmpe.PHI), abs=2e-3)

# ---------------- ported regional models (gmm.py) ----------------
import gmm
from openquake.hazardlib.gsim.boore_2014 import BooreEtAl2014
from openquake.hazardlib.gsim.sharma_2009 import SharmaEtAl2009

def _oq_all(cls, m, r, v, rake, **kw):
    from openquake.hazardlib.contexts import simple_cmaker
    cm = simple_cmaker([cls(**kw)], ["PGA"])
    ctx = cm.new_ctx(len(r)); ctx.mag, ctx.rake, ctx.rjb, ctx.vs30 = m, rake, r, v
    out = cm.get_mean_stds([ctx]); return out[0, 0, 0], out[1, 0, 0], out[2, 0, 0], out[3, 0, 0]

BM, BR, BV = (np.array(x) for x in zip(*[(m, r, v) for m, r, v in grid() if v != 225.0]))

@pytest.mark.parametrize("mech,rake", [("ss", 0.0), ("rs", 90.0), ("ns", -90.0)])
def test_bssa14_matches_openquake(mech, rake):
    mean, sig, tau, phi = _oq_all(BooreEtAl2014, BM, BR, BV, rake)
    ours = gmm.bssa14(BM, BR, BV, mech)
    assert np.abs(np.exp(ours[0] - mean) - 1).max() < 0.01
    for a, b in zip(ours[1:], (sig, tau, phi)):
        np.testing.assert_allclose(a, b, rtol=1e-6)

def test_bssa14_unspecified_matches_openquake_sof_false():
    mean, *_ = _oq_all(BooreEtAl2014, BM, BR, BV, 0.0, sof=False)
    assert np.abs(np.exp(gmm.bssa14(BM, BR, BV, "unspec")[0] - mean) - 1).max() < 0.01

@pytest.mark.parametrize("mech,rake", [("ss", 0.0), ("rs", 90.0), ("ns", -90.0)])
def test_sharma09_matches_openquake(mech, rake):
    mean, sig, *_ = _oq_all(SharmaEtAl2009, BM, BR, BV, rake)
    ours = gmm.sharma09(BM, BR, BV, mech)
    assert np.abs(np.exp(ours[0] - mean) - 1).max() < 0.01
    np.testing.assert_allclose(ours[1], sig, rtol=1e-6)

def test_mix_is_consistent_with_its_parts():
    d = np.array([5.0, 50.0, 150.0, 300.0])
    mu, sig, tau, phi, info = gmm.pga(7.0, d, 400.0, "ss")
    w = gmm.weights(d)
    parts = {m: gmm.MODELS[m](7.0, d, 400.0, "ss") for m in gmm.MODELS}
    np.testing.assert_allclose(mu, sum(w[m] * parts[m][0] for m in w), rtol=1e-12)
