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

def test_collapse_that_reaches_the_street_blocks_it_but_a_distant_one_does_not():
    net = street(width=6.0)
    near = bld(40, 5)           # 5 m from the centreline -> setback 2 m; 8-storey (24 m) reach = 12 m  >= 2 + 0.6*6
    far = bld(40, 60)           # 60 m away: outside the 30 m search
    p = R.blockage(net, [near, far], [24.0, 24.0], [1.0, 1.0], [0.0, 0.0])
    assert p[0] == pytest.approx(1.0, abs=1e-9) and p[1] == p[0]
    p = R.blockage(net, [far], [24.0], [1.0], [0.0])
    assert np.all(p == 0)

def test_low_building_cannot_reach_far_street():
    net = street(width=8.0)
    b = bld(40, 22)             # 22 m north of the centreline; 1 storey 3 m -> reach 1.5 m
    assert R.blockage(net, [b], [3.0], [1.0], [0.0])[0] == 0.0
    tall = R.blockage(net, [b], [60.0], [1.0], [0.0])[0]          # 20 storeys: reach 30 m >= 22-4 + 4.8
    assert tall == pytest.approx(1.0)

def test_probabilities_combine_as_independent_events():
    net = street(width=6.0)
    bs = [bld(10, 5), bld(40, 5), bld(70, 5)]
    pc = [0.2, 0.3, 0.5]
    p = R.blockage(net, bs, [24.0] * 3, pc, [0.0] * 3)[0]
    assert p == pytest.approx(1 - (0.8 * 0.7 * 0.5))

def test_partial_debris_from_extensive_damage_needs_half_the_reach():
    net = street(width=6.0)
    b = bld(40, 5)                       # needs 2 + 3.6 = 5.6 m of reach; extensive uses D/2
    # height 12 m -> D = 6 m: collapse reaches (6 >= 5.6) but extensive (3 >= 5.6) does not
    assert R.blockage(net, [b], [12.0], [0.0], [1.0])[0] == 0.0
    # height 24 m -> D/2 = 6 >= 5.6: partial debris counts, times G
    assert R.blockage(net, [b], [24.0], [0.0], [1.0])[0] == pytest.approx(R.G_PARTIAL)

def test_wider_streets_are_harder_to_block_and_monotone_in_collapse_chance():
    b = [bld(40, 5)]
    narrow = R.blockage(street(width=4.0), b, [12.0], [0.5], [0.0])[0]
    wide = R.blockage(street(width=14.0), b, [12.0], [0.5], [0.0])[0]
    assert narrow == pytest.approx(0.5) and wide == 0.0
    ps = [R.blockage(street(), b, [24.0], [c], [0.0])[0] for c in (0.0, 0.2, 0.6, 1.0)]
    assert ps == sorted(ps)

def test_widths_prefer_osm_tag_then_lanes_then_class_default():
    w = R.widths_for(["primary", "residential", "residential", ["service", "x"]], osm_width=["12 m", None, None, None], lanes=[None, 2, None, None])
    assert list(w) == [12.0, 7.0, 5.5, 4.0]
