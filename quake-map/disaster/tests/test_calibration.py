"""Phase 3: the fitting code must recover known curves from simulated survey counts."""
import numpy as np, pandas as pd, pytest
import calibration as C

TRUE_THETA, TRUE_BETA = np.array([0.12, 0.25, 0.45, 0.80]), 0.55

def simulate(n_wards=160, per_ward=400, seed=1, truncated=True, spread=0.25):
    rng = np.random.default_rng(seed)
    x = rng.uniform(np.log(0.05), np.log(1.0), n_wards)
    s = np.full(n_wards, spread)
    P = C.state_probs(x, np.log(TRUE_THETA), TRUE_BETA, s)
    if truncated:
        P = P.copy(); P[:, 0] = 0; P /= P.sum(1, keepdims=True)
    counts = np.array([rng.multinomial(per_ward, p) for p in P])
    return x, s, counts

def test_probabilities_are_proper():
    x = np.log([0.02, 0.2, 1.0])
    P = C.state_probs(x, np.log(TRUE_THETA), TRUE_BETA, 0.3)
    np.testing.assert_allclose(P.sum(1), 1.0, atol=1e-9) ; assert (P >= 0).all()
    E = C.exceedance(x, np.log(TRUE_THETA), TRUE_BETA, 0.3)
    assert (np.diff(E, axis=1) <= 1e-12).all() and (np.diff(E, axis=0) >= 0).all()

def test_closed_form_shaking_integral_equals_gauss_hermite():
    from numpy.polynomial.hermite_e import hermegauss
    m, s, mu, beta = np.log(0.3), 0.4, np.log(0.45), 0.55
    z, w = hermegauss(40)
    gh = (w * C.ndtr((m + s * z - mu) / beta)).sum() / w.sum()
    assert C.exceedance(np.array([m]), [mu], beta, np.array([s]))[0, 0] == pytest.approx(gh, abs=1e-9)

@pytest.mark.parametrize("truncated", [True, False])
def test_fit_recovers_true_parameters(truncated):
    x, s, n = simulate(truncated=truncated)
    r = C.fit(x, s, n, truncated)
    assert r["success"]
    if truncated:       # conditional on damage the onset (theta_1) is weakly identified; the upper medians and beta are
        np.testing.assert_allclose(r["theta"][1:], TRUE_THETA[1:], rtol=0.08)
    else:
        np.testing.assert_allclose(r["theta"], TRUE_THETA, rtol=0.08)
    assert r["beta"] == pytest.approx(TRUE_BETA, rel=0.10)
    assert list(r["theta"]) == sorted(r["theta"])

def test_ignoring_shaking_spread_inflates_beta():
    x, s, n = simulate(spread=0.4, truncated=False)
    honest = C.fit(x, s, n, False)["beta"]
    naive = C.fit(x, np.zeros_like(s), n, False)["beta"]
    assert naive > honest and honest == pytest.approx(TRUE_BETA, rel=0.10)

def test_bootstrap_interval_covers_truth_and_is_ward_based():
    x, s, n = simulate(truncated=False)
    mle = C.fit(x, s, n, False)
    ward = np.arange(len(x))
    boot = C.bootstrap(ward, x, s, n, mle, n=60, seed=3, truncated=False)
    res = C.summarize(mle, boot)
    for name, truth in zip(["theta_slight", "theta_moderate", "theta_extensive", "theta_complete", "beta"], list(TRUE_THETA) + [TRUE_BETA]):
        lo, hi = res[name]["ci90"]; assert lo <= truth * 1.06 and hi >= truth * 0.94, name
    assert res["ci_width_over_theta_extensive"] < 0.5 and res["narrow_ci"]
    few = C.bootstrap(ward[:12], x[:12], s[:12], n[:12], C.fit(x[:12], s[:12], n[:12], False), n=20, seed=3, truncated=False)
    assert (np.percentile(few[:, 2], 95) - np.percentile(few[:, 2], 5)) > (res["theta_extensive"]["ci90"][1] - res["theta_extensive"]["ci90"][0])

def test_classification_and_grouping():
    df = pd.DataFrame({"has_superstructure_cement_mortar_brick": [1, 1, 0, 0, 0, 1], "has_superstructure_rc_non_engineered": [0, 0, 1, 1, 0, 0],
                       "has_superstructure_rc_engineered": [0, 0, 0, 0, 1, 0], "count_floors_pre_eq": [2, 3, 3, 6, 5, 1],
                       "ward": ["a", "a", "a", "b", "b", "b"], "damage_grade": [1, 3, 5, 4, 2, 3]})
    df["cls"] = C.classify_nepal(df)
    assert [None if pd.isna(v) else v for v in df.cls] == ["URM", None, "RC_LOW", "RC_MID", "RC_MID", "URM"]
    wp = pd.DataFrame({"mean_lnpga": [np.log(.2), np.log(.3)], "sd_lnpga": [.1, .1], "sigma_shakemap": [.3, .3]}, index=pd.Index(["a", "b"], name="ward"))
    g = C.to_groups(df, "ward", "damage_grade", wp)
    row = g[(g.cls == "RC_MID") & (g.ward == "b")].iloc[0]
    assert (row.n4, row.n2) == (1, 1) and row.s == pytest.approx(np.hypot(.1, .3))     # grade 4 -> complete, grade 2 -> moderate
    alt = C.to_groups(df, "ward", "damage_grade", wp, grade_map=C.GRADE_TO_STATE_ALT)
    assert alt[(alt.cls == "RC_MID") & (alt.ward == "b")].iloc[0].n3 == 1

def test_ward_pga_population_weighting():
    gpd = pytest.importorskip("geopandas")
    from shapely.geometry import box
    grid = pd.DataFrame({"lon": [0.25, 0.75, 1.5], "lat": [0.5, 0.5, 0.5], "pga_g": [0.1, 0.4, 0.2], "sigma_lnpga": [0.3, 0.3, 0.5]})
    wards = gpd.GeoDataFrame({"w": ["x", "y"]}, geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)], crs=4326)
    plain = C.ward_pga(grid, wards, "w")
    assert plain.loc["x", "mean_lnpga"] == pytest.approx(np.mean(np.log([0.1, 0.4])))
    pop = (np.array([0.25, 0.75, 1.5]), np.array([0.5, 0.5, 0.5]), np.array([900.0, 100.0, 5.0]))
    w = C.ward_pga(grid, wards, "w", pop)
    assert w.loc["x", "mean_lnpga"] == pytest.approx(0.9 * np.log(0.1) + 0.1 * np.log(0.4), rel=1e-4)
    assert w.loc["y", "sigma_shakemap"] == pytest.approx(0.5)

def test_fit_classes_writes_json_and_plot(tmp_path):
    x, s, n = simulate(n_wards=80, per_ward=300, truncated=True)
    g = pd.DataFrame({"cls": "URM", "ward": np.arange(80), "x": x, "s": s, **{f"n{k}": n[:, k] for k in range(5)}})
    res = C.fit_classes(g, tmp_path, classes=("URM", "RC_LOW"), n_boot=8, plots=tmp_path / "plots")
    assert (tmp_path / "fit_URM.json").exists() and (tmp_path / "plots" / "URM.png").exists()
    assert "skipped" in res["RC_LOW"] and res["URM"]["theta_extensive"]["mle"] == pytest.approx(TRUE_THETA[2], rel=0.15)

def test_choose_final_rule_and_provenance():
    import json, pathlib
    cur = json.loads((pathlib.Path(__file__).resolve().parent.parent / "priors" / "fragility.json").read_text())
    x, s, n = simulate(truncated=False)
    mle = C.fit(x, s, n, False); good = C.summarize(mle, C.bootstrap(np.arange(len(x)), x, s, n, mle, n=20, seed=1, truncated=False))
    good.update(n_buildings=int(n.sum()), truncated=False)
    wide = dict(good, narrow_ci=False, ci_width_over_theta_extensive=0.9)
    out = C.choose_final({"URM": good, "RC_LOW": wide, "RC_MID": {"skipped": "too little data (3 groups)"}}, cur)
    assert out["pga_g_medians_slight_moderate_extensive_complete"]["URM"][2] == pytest.approx(TRUE_THETA[2], rel=0.1)
    assert out["pga_g_medians_slight_moderate_extensive_complete"]["RC_LOW"] == cur["pga_g_medians_slight_moderate_extensive_complete"]["RC_LOW"]
    assert "fitted" in out["class_status"]["URM"] and "too uncertain" in out["class_status"]["RC_LOW"] and "too little" in out["class_status"]["RC_MID"]
    assert out["samples"]["URM"] and out["status"].startswith("PARTLY")
    assert cur["status"].startswith("UNCALIBRATED")                       # input not mutated

def test_holdout_score_beats_null_on_data_from_the_same_curves():
    xa, sa, na = simulate(n_wards=200, seed=5, truncated=True)
    tr = (np.arange(200) < 150)
    mk = lambda m: pd.DataFrame({"x": xa[m], "s": sa[m], **{f"n{k}": na[m][:, k] for k in range(5)}})
    train, test = mk(tr), mk(~tr)
    mle = C.fit(train.x.to_numpy(), train.s.to_numpy(), train[[f"n{k}" for k in range(5)]].to_numpy(), True)
    sc = C.holdout_score(mle, test, train)
    assert sc["skill"] > 0.05 and sc["log_loss"] < sc["log_loss_null"] and sc["brier"] < sc["brier_null"]

def _write_shakemap(path, lons, lats, pga_pct, sig):
    rows = "\n".join(f"{a:.5f} {b:.5f} {p:.4f} {q:.3f}" for a, b, p, q in zip(lons, lats, pga_pct, sig))
    path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<shakemap_grid xmlns="http://earthquake.usgs.gov/eqcenter/shakemap" event_id="t">'
                    '<grid_field index="1" name="LON" units="dd"/><grid_field index="2" name="LAT" units="dd"/>'
                    '<grid_field index="3" name="PGA" units="pctg"/><grid_field index="4" name="STDPGA" units="ln(pctg)"/>'
                    f"<grid_data>\n{rows}\n</grid_data></shakemap_grid>")

def test_driver_end_to_end_recovers_curves_on_synthetic_survey(tmp_path):
    gpd = pytest.importorskip("geopandas")
    import importlib.util, pathlib
    from shapely.geometry import box
    spec = importlib.util.spec_from_file_location("cal_tool", pathlib.Path(__file__).resolve().parent.parent / "tools" / "calibrate_fragility.py")
    tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)
    rng = np.random.default_rng(4)
    nx, ny = 16, 12
    wards = gpd.GeoDataFrame({"ward_id": np.arange(nx * ny)}, geometry=[box(i * .1, j * .1, i * .1 + .1, j * .1 + .1) for i in range(nx) for j in range(ny)], crs=4326)
    wards.to_file(tmp_path / "w.gpkg", driver="GPKG")
    # PGA field rises west to east: 0.05 g .. 0.9 g; ShakeMap points every 0.02 degrees
    gx, gy = np.meshgrid(np.arange(0.01, nx * .1, 0.02), np.arange(0.01, ny * .1, 0.02))
    pga = 0.05 * (0.9 / 0.05) ** (gx / (nx * .1))
    _write_shakemap(tmp_path / "grid.xml", gx.ravel(), gy.ravel(), (pga * 100).ravel(), np.full(gx.size, .3))
    from shapely.geometry import Point
    # ward mean ln PGA straight from the same machinery, then draw buildings from the true curves (grade = state)
    wp = C.ward_pga(C.read_shakemap_grid(tmp_path / "grid.xml"), wards.assign(ward_id=wards.ward_id.astype(str)), "ward_id")
    rows = []
    for w, r in wp.iterrows():
        P = C.state_probs(np.array([r.mean_lnpga]), np.log(TRUE_THETA), TRUE_BETA, np.array([np.hypot(r.sd_lnpga, 0.3)]))[0]
        P[0] = 0; P /= P.sum()
        for st, k in zip(range(1, 5), rng.multinomial(500, P[1:])):
            rows += [(w, st if st < 4 else int(rng.choice([4, 5])))] * int(k)
    sv = pd.DataFrame(rows, columns=["ward_id", "damage_grade"])
    sv["has_superstructure_cement_mortar_brick"], sv["has_superstructure_rc_non_engineered"] = 1, 0
    sv["has_superstructure_rc_engineered"], sv["count_floors_pre_eq"] = 0, 2
    sv.to_csv(tmp_path / "s.csv", index=False)
    tool.main(["--survey", str(tmp_path / "s.csv"), "--wards", str(tmp_path / "w.gpkg"), "--survey-ward-col", "ward_id", "--ward-col", "ward_id",
               "--grade-col", "damage_grade", "--shakemap", str(tmp_path / "grid.xml"), "--boot", "10", "--apply"], out=tmp_path / "out")
    fit = __import__("json").loads((tmp_path / "out" / "fit_URM.json").read_text())
    assert fit["theta_extensive"]["mle"] == pytest.approx(TRUE_THETA[2], rel=0.12) and fit["theta_complete"]["mle"] == pytest.approx(TRUE_THETA[3], rel=0.12)
    assert fit["beta"]["mle"] == pytest.approx(TRUE_BETA, rel=0.15) and fit["truncated"]
    new = __import__("json").loads((tmp_path / "out" / "fragility.json").read_text())
    assert "fitted" in new["class_status"]["URM"] and "kept previous" in new["class_status"]["RC_LOW"]
