"""Debris-blockage maths on hand-made streets."""
import numpy as np, pytest
import shapely
from shapely.geometry import LineString, box
import roads as R

def street(width=6.0, length=100.0, y=0.0):
    """One street along x at height y, two directed edges."""
    g = LineString([(0, y), (length, y)])
    return R.RoadNet([[0, y], [length, y]], [0, 1], [1, 0], [10, 10], [length, length], [g, g], [width, width])

def bld(x, y, size=10.0):
    return box(x, y, x + size, y + size)

def test_both_directions_share_one_road_id():
    assert street().n_roads == 1

def test_debris_width_matches_anelli_equations():
    """Masonry: a(eps-1), eps = 1.228 + 0.07869 a/b + 0.05626 h/a (a = b = 10 m, h = 9 m -> 10 * (0.228 + 0.07869 + 0.050634) = 3.5732); RC: one storey."""
    w = R.debris_width(100.0, 9.0, True)
    assert w == pytest.approx(10 * (0.228 + 0.07869 + 0.05626 * 0.9), rel=1e-9) and w == pytest.approx(3.5732, abs=1e-3)
    assert R.debris_width(100.0, 9.0, False) == R.STOREY_M
    assert R.debris_width(100.0, 18.0, True) > w                       # taller -> farther

def test_collapse_that_reaches_the_street_blocks_it_but_a_distant_one_does_not():
    net = street(width=6.0)                  # needs >= 6 - 3.5 = 2.5 m of encroachment
    near = bld(40, 4)                        # footprint 4 m from the centreline -> setback 1 m; 3 storeys (9 m): w = 3.57 -> encroaches 2.57 m
    far = bld(40, 60)                        # outside the 30 m search
    p = R.blockage(net, [near, far], [9.0, 9.0], [1.0, 1.0], [0.0, 0.0])
    assert p[0] == pytest.approx(R.DIR_W, abs=1e-9) and p[1] == p[0]   # only the 'debris toward the street' direction (w) is enough; w/2 is not
    assert np.all(R.blockage(net, [far], [9.0], [1.0], [0.0]) == 0)

def test_tall_masonry_collapse_blocks_in_both_reaching_directions():
    net = street(width=6.0)
    p = R.blockage(net, [bld(40, 4)], [30.0], [1.0], [0.0])[0]        # 10 storeys: w = 3.07 + 1.69 = 4.75; w/2 - 1 = 1.4 < 2.5 still no
    assert p == pytest.approx(R.DIR_W)
    p = R.blockage(street(width=4.0), [bld(40, 3)], [30.0], [1.0], [0.0])[0]   # 4 m street: thr 0.5, setback 1: w - 1 = 3.75 and w/2 - 1 = 1.38 both >= 0.5
    assert p == pytest.approx(2 * R.DIR_W)

def test_low_building_cannot_reach_far_street():
    net = street(width=8.0)
    b = bld(40, 22)                          # 22 m north of the centreline: setback 18 m; masonry w = 3.2 m
    assert R.blockage(net, [b], [3.0], [1.0], [0.0])[0] == 0.0

def test_rc_uses_extensive_damage_and_one_storey_of_debris():
    net = street(width=4.0)                  # thr 0.5 m
    b = bld(40, 2)                           # setback 0 (footprint 2 m from the centreline = the kerb)
    # RC (p_masonry 0): P(extensive or worse) = 0.6, collapse chance is irrelevant; w = 3 m, w/2 = 1.5 m, both >= 0.5
    p = R.blockage(net, [b], [12.0], [0.0], [0.6], p_masonry=[0.0])[0]
    assert p == pytest.approx(0.6 * 2 * R.DIR_W)
    # the same building as masonry with no collapse chance cannot block (extensive damage does not spread masonry debris)
    assert R.blockage(net, [b], [12.0], [0.0], [0.6], p_masonry=[1.0])[0] == 0.0
    mixed = R.blockage(net, [b], [12.0], [0.0], [0.6], p_masonry=[0.25])[0]
    assert mixed == pytest.approx(0.75 * 0.6 * 2 * R.DIR_W)

def test_probabilities_combine_as_independent_events():
    net = street(width=6.0)
    bs = [bld(10, 4), bld(40, 4), bld(70, 4)]
    pc = [0.2, 0.3, 0.5]
    p = R.blockage(net, bs, [9.0] * 3, pc, [0.0] * 3)[0]
    assert p == pytest.approx(1 - np.prod([1 - c * R.DIR_W for c in pc]))

def test_wider_streets_are_harder_to_block_and_monotone_in_collapse_chance():
    b = [bld(40, 4)]
    narrow = R.blockage(street(width=4.0), b, [9.0], [0.5], [0.0])[0]
    wide = R.blockage(street(width=14.0), b, [9.0], [0.5], [0.0])[0]
    assert narrow > 0 and wide == 0.0
    ps = [R.blockage(street(), b, [24.0], [c], [0.0])[0] for c in (0.0, 0.2, 0.6, 1.0)]
    assert ps == sorted(ps)

def test_street_narrower_than_the_free_width_is_blocked_by_any_encroaching_debris():
    p = R.blockage(street(width=3.0), [bld(40, 3)], [9.0], [1.0], [0.0])[0]       # thr = 0: w = 3.57 - setback 1.5 > 0 in both directions
    assert p == pytest.approx(2 * R.DIR_W)

def test_widths_prefer_osm_tag_then_lanes_then_class_default():
    w = R.widths_for(["primary", "residential", "residential", ["service", "x"]], osm_width=["12 m", None, None, None], lanes=[None, 2, None, None])
    assert list(w) == [12.0, 7.0, 5.5, 4.0]
