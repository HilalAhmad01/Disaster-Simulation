"""Drone-updated damage picture: baseline untouched, identical with no observations, strong local and weak neighbour updates."""
import hashlib
import numpy as np, pytest
from drone.config import Config
from drone import feedback as F
import dronefix


@pytest.fixture(scope="module")
def city(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    c = dronefix.make_city(mp, tmp_path_factory.mktemp("fb"))
    yield c
    mp.undo()


def digest(m):
    L = m.last
    return hashlib.sha256(b"".join(L[c].to_numpy(float).tobytes() for c in ("p_slight", "p_moderate", "p_extensive", "p_collapse", "risk"))).hexdigest()


def test_confusion_matrix_columns_sum_to_one():
    C = F.confusion(0.9)
    assert np.allclose(C.sum(0), 1.0)
    assert C[0, 2] == 0 and C[2, 0] == 0                       # intact is never mistaken for collapsed directly

def test_posterior_moves_towards_the_observation():
    prior = np.array([0.5, 0.3, 0.2])
    for obs in (0, 1, 2):
        post = F.posterior(prior, obs, 0.9)
        assert post.sum() == pytest.approx(1.0) and post[obs] > prior[obs]
    assert F.posterior(np.array([0.0, 0.0, 1.0]), 0, 0.9)[2] == pytest.approx(1.0)   # an impossible observation changes nothing

def test_no_observations_means_identical_aggregates(city):
    m = city["model"]
    d = F.DroneUpdated(m, Config())
    a = d.aggregates()
    assert a["modelled"] == pytest.approx(a["drone_updated"]) and a["changed"] == pytest.approx(0.0, abs=1e-9)
    base_cells = {(round(f["geometry"]["coordinates"][0], 6), round(f["geometry"]["coordinates"][1], 6)): f["properties"] for f in m.cells}
    for f in a["cells"]:
        k = (round(f["geometry"]["coordinates"][0], 6), round(f["geometry"]["coordinates"][1], 6))
        for key in ("risk", "collapse", "rank", "heavy", "n"):
            assert f["properties"][key] == base_cells[k][key]

def test_observing_a_collapsed_building_raises_it_and_its_neighbours_slightly(city):
    m = city["model"]
    cfg = Config(feedback_min_scanned=3)
    d = F.DroneUpdated(m, cfg)
    pc0 = m.last["p_collapse"].to_numpy(float)
    before = digest(m)
    # scan many buildings of one cell, all collapsed
    cell = np.bincount(d.cell).argmax()
    ids = np.flatnonzero(d.cell == cell)
    for b in ids[:12]:
        info = d.observe(int(b), 2)
        assert info["after"] > info["before"]
    p = d.probabilities()
    assert p[ids[0], 3] > 0.5 and p[ids[0], 3] > pc0[ids[0]]
    unscanned = ids[12:]
    assert len(unscanned) > 0 and np.all(p[unscanned, 3] >= pc0[unscanned] - 1e-12) and p[unscanned, 3].mean() > pc0[unscanned].mean()
    assert np.all(p[unscanned, 3] <= 1 + 1e-12)
    assert digest(m) == before                                                   # the baseline never changes

def test_observing_intact_lowers_neighbours(city):
    m = city["model"]
    d = F.DroneUpdated(m, Config(feedback_min_scanned=3))
    cell = np.bincount(d.cell).argmax()
    ids = np.flatnonzero(d.cell == cell)
    pc0 = m.last["p_collapse"].to_numpy(float)
    if pc0[ids].mean() < 0.02:
        pytest.skip("collapse probability too small in this fixture for a visible drop")
    for b in ids[:12]:
        d.observe(int(b), 0)
    p = d.probabilities()
    assert p[ids[12:], 3].mean() < pc0[ids[12:]].mean()

def test_factor_is_clipped_and_needs_enough_scanned_buildings(city):
    m = city["model"]
    cfg = Config(feedback_min_scanned=8)
    d = F.DroneUpdated(m, cfg)
    cell = np.bincount(d.cell).argmax()
    ids = np.flatnonzero(d.cell == cell)
    for b in ids[:5]:                                                           # fewer than min_scanned: no neighbour effect
        d.observe(int(b), 2)
    d.probabilities()
    assert np.allclose(d.factor, 1.0)
    for b in ids[5:20]:
        d.observe(int(b), 2)
    d.probabilities()
    assert d.factor.max() <= cfg.feedback_clip_hi + 1e-9 and d.factor.min() >= cfg.feedback_clip_lo - 1e-9 and d.factor.max() > 1.0

def test_disabling_feedback_leaves_the_picture_unchanged(city):
    m = city["model"]
    d = F.DroneUpdated(m, Config(feedback_enabled=False))
    for b in range(10):
        d.observe(b, 2)
    assert np.allclose(d.probabilities(), d.base)

def test_updated_totals_move_with_observations(city):
    m = city["model"]
    d = F.DroneUpdated(m, Config(feedback_min_scanned=3))
    cell = np.bincount(d.cell).argmax()
    for b in np.flatnonzero(d.cell == cell)[:15]:
        d.observe(int(b), 2)
    a = d.aggregates()
    assert a["drone_updated"]["expected_collapses"] > a["modelled"]["expected_collapses"] and a["observed_buildings"] == 15
    assert a["deaths"] is None or a["deaths"]["ratio"] >= 1.0


def test_modelled_view_equals_the_baseline_even_after_observations(city):
    m = city["model"]
    d = F.DroneUpdated(m, Config(feedback_min_scanned=3))
    cell = np.bincount(d.cell).argmax()
    for b in np.flatnonzero(d.cell == cell)[:15]:
        d.observe(int(b), 2)
    base = d.aggregates(updated=False)
    upd = d.aggregates(updated=True)
    ref = {(round(f["geometry"]["coordinates"][0], 6)): f["properties"]["risk"] for f in m.cells}
    assert all(ref[round(f["geometry"]["coordinates"][0], 6)] == f["properties"]["risk"] for f in base["cells"])
    assert sum(f["properties"]["collapse"] for f in upd["cells"]) > sum(f["properties"]["collapse"] for f in base["cells"])
