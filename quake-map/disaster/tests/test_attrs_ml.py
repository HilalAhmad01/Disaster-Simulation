"""Building-attribute model: features and how it plugs into the exposure."""
import numpy as np, pandas as pd, pytest
gpd = pytest.importorskip("geopandas"); lgb = pytest.importorskip("lightgbm")
import shapely
from shapely.geometry import box
import attrs_ml as A, exposure

def city(n=600, seed=0):
    rng = np.random.default_rng(seed)
    x, y = rng.uniform(0, 0.02, n), rng.uniform(0, 0.02, n)
    w = rng.uniform(5e-5, 3e-4, n); h = rng.uniform(5e-5, 3e-4, n)
    return [box(77.3 + a, 28.5 + b, 77.3 + a + c, 28.5 + b + d) for a, b, c, d in zip(x, y, w, h)]

def test_neighbourhood_features_match_brute_force():
    geoms = np.array(city(), dtype=object)
    F = A.features(geoms)
    u = gpd.GeoSeries(geoms, crs=4326); u = np.asarray(u.to_crs(u.estimate_utm_crs()).values, dtype=object)
    c = shapely.centroid(u); xy = np.column_stack([shapely.get_x(c), shapely.get_y(c)]); la = np.log(np.maximum(shapely.area(u), 1.0))
    for k in (0, 17, 300, 599):
        d = np.hypot(*(xy - xy[k]).T)
        for r in (50, 100, 250):
            nb = np.flatnonzero((d <= r) & (np.arange(len(xy)) != k))
            assert F[f"n_{r}"][k] == len(nb)
            if len(nb):
                assert F[f"mean_la_{r}"][k] == pytest.approx(la[nb].mean())
                if r != 250:
                    assert F[f"rank_la_{r}"][k] == pytest.approx((la[nb] < la[k]).mean())
                    assert F[f"share_big_{r}"][k] == pytest.approx((la[nb] > np.log(200)).mean())
            else:
                assert np.isnan(F[f"mean_la_{r}"][k])
        assert F.dist_nn[k] == pytest.approx(np.sort(d)[1])

def test_row_subset_gives_the_same_rows_and_shape_features_are_sane():
    geoms = np.array(city(), dtype=object)
    full, rows = A.features(geoms), np.array([3, 40, 41, 500])
    sub = A.features(geoms, rows=rows)
    np.testing.assert_allclose(sub.fillna(-9).to_numpy(), full.iloc[rows].fillna(-9).to_numpy())
    assert (full.compactness.between(0.3, 0.7855)).all()          # rectangles: pi/4 for a square, lower when elongated
    assert full.compactness.max() == pytest.approx(np.pi / 4, abs=0.02)
    assert (full.elongation >= 1 - 1e-9).all()
    assert full.n_vertices.eq(5).all() and full.orient_sin2.abs().le(1).all()

def test_storey_bins_and_use_groups():
    assert list(A.storey_bin([1, 2, 3, 4, 5, 7, 8, 30])) == [0, 1, 2, 2, 3, 3, 4, 4]
    assert list(A.use_group(["residential", "office", "hotel", "religious", "outbuilding", "weird", None])) == ["residential", "commercial", "commercial", "civic", "other", "other", "other"]

@pytest.fixture()
def tiny_model(tmp_path):
    """A small real LightGBM model on synthetic footprints (bigger footprints -> more storeys), saved where load_model() looks."""
    import json
    geoms = np.array(city(1500, 1), dtype=object)
    F = A.features(geoms)
    y = np.clip((F.log_area - 4.0).to_numpy() * 2 + np.random.default_rng(2).normal(0, .5, len(F)), 0, 4).round().astype(int)
    feats = list(F.columns)
    bst = lgb.train(dict(objective="multiclass", num_class=5, num_leaves=7, learning_rate=0.1, verbose=-1, min_data_in_leaf=10), lgb.Dataset(F, y), 30)
    d = tmp_path / "models"; d.mkdir()
    bst.save_model(str(d / "storeys_satellite.txt"))
    (d / "storeys_satellite.json").write_text(json.dumps({"features": feats, "rep_storeys": [1, 2, 3.4, 5.8, 10]}))
    A._MODEL.clear()
    return tmp_path

def _inputs(n=400):
    geoms = np.array(city(n, 5), dtype=object)
    b = pd.DataFrame({"num_floors": np.nan, "height": np.nan}, index=range(n))
    b.loc[:49, "num_floors"] = 12                                              # labelled
    ht = pd.DataFrame({"h_google": np.full(n, 9.0), "h_spread": np.zeros(n)})
    ht.loc[50:149, "h_google"] = 20.0                                          # satellite-measured (usable)
    ht.loc[150:249, "h_google"] = 0.5                                          # unusable -> guess_unstable
    ht.loc[250:299, "h_spread"] = 12.0                                         # unstable -> guess_unstable
    u = gpd.GeoSeries(geoms, crs=4326); utm = u.estimate_utm_crs()
    area = np.asarray(u.to_crs(utm).area)
    return b, area, ht, geoms, utm

def test_model_replaces_only_guessed_storeys(tiny_model):
    b, area, ht, geoms, utm = _inputs()
    base = exposure.attributes_full(b, area, ht)
    d = exposure.attributes_full(b, area, ht, geoms=geoms, utm=utm, data_dir=tiny_model)
    src = base["levels_src"].to_numpy(); new = d["levels_src"].to_numpy()
    guessed = np.isin(src, ["guess", "guess_unstable"])
    assert guessed.sum() > 100 and not guessed[:150].any()      # the first 150 rows are labelled or satellite-measured
    assert (new[guessed] == "satellite_model").all() and (new[~guessed] == src[~guessed]).all()
    np.testing.assert_array_equal(d["levels"].to_numpy()[~guessed], base["levels"].to_numpy()[~guessed])     # labelled + measured untouched
    assert set(d["levels"].to_numpy()[guessed]) <= {1, 2, 3, 6, 10}
    np.testing.assert_allclose(d["p"].sum(1), 1.0, atol=1e-9)
    assert (d["p"][~guessed] == base["p"][~guessed]).all()                     # hard bins -> identical type probabilities outside the model's rows
    assert not np.allclose(d["p"][guessed], base["p"][guessed])               # soft mixture changed them

def test_without_model_or_heights_nothing_changes(tmp_path, tiny_model):
    b, area, ht, geoms, utm = _inputs()
    base = exposure.attributes_full(b, area, ht)
    no_model = exposure.attributes_full(b, area, ht, geoms=geoms, utm=utm, data_dir=tmp_path / "empty")      # falls back to priors/models (shipped)
    assert set(no_model["levels_src"]) <= {"label", "google", "google_over_label", "guess", "guess_unstable", "satellite_model"}
    A._MODEL.clear()
    no_heights = exposure.attributes_full(b, area, None, geoms=geoms, utm=utm, data_dir=tiny_model)
    ref = exposure.attributes_full(b, area, None)
    assert (no_heights["levels_src"] == ref["levels_src"]).all() and (no_heights["levels"] == ref["levels"]).all()    # heights absent: model not used

def test_single_storey_uses_stay_capped(tiny_model):
    b, area, ht, geoms, utm = _inputs()
    b["class"] = "warehouse"
    d = exposure.attributes_full(b, area, ht, geoms=geoms, utm=utm, data_dir=tiny_model)
    guessed = d["levels_src"].to_numpy() == "satellite_model"
    assert guessed.any() and (d["levels"].to_numpy()[guessed] <= 2).all()

def test_shipped_model_loads_and_predicts_probabilities():
    A._MODEL.clear()
    m = A.load_model(None)
    assert m is not None and len(m[1]["features"]) > 15 and "h_google" not in m[1]["features"]       # no satellite-height features
    P = A.predict_storeys(np.array(city(200, 3), dtype=object), np.arange(100))
    assert P.shape == (100, 5) and np.allclose(P.sum(1), 1.0, atol=1e-6)
