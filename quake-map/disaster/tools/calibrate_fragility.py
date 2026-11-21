"""Fit fragility curves to the Nepal 2015 survey (MASTER_PLAN 3.2-3.5). Needs data you must obtain yourself, see
tools/get_nepal_calibration.py. Example:

  python tools/calibrate_fragility.py --survey data/calibration/nepal/survey.csv --wards data/calibration/nepal/wards.gpkg \
      --survey-ward-col ward_id --ward-col ward_id --grade-col damage_grade_num \
      --shakemap data/calibration/nepal/grid.xml --uncertainty data/calibration/nepal/uncertainty.xml \
      [--pop data/rasters/ghs_pop/nepal/pop2025.tif] [--district-col district_id --holdout 12,24,31] [--apply]

Steps: ward PGA (population-weighted ShakeMap) -> class + grade mapping -> grouped likelihood with shaking uncertainty ->
ward bootstrap -> plots in docs/calibration/ -> (--apply) write data/priors/fragility.json by the 3.5 rule.
The survey lists DAMAGED buildings only, so the likelihood is conditional on damage (truncated) unless --untruncated.
"""
import argparse, json, pathlib, sys
import numpy as np, pandas as pd
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import calibration as C

def main(argv=None, out=None):
    ap = argparse.ArgumentParser()
    for a, kw in (("--survey", dict(required=True)), ("--wards", dict(required=True)), ("--survey-ward-col", dict(required=True)),
                  ("--ward-col", dict(required=True)), ("--grade-col", dict(required=True)), ("--shakemap", dict(required=True)),
                  ("--uncertainty", {}), ("--pop", {}), ("--district-col", {}), ("--holdout", dict(default="")), ("--boot", dict(type=int, default=200)),
                  ("--alt-grades", dict(action="store_true")), ("--untruncated", dict(action="store_true")), ("--apply", dict(action="store_true"))):
        ap.add_argument(a, **kw)
    a = ap.parse_args(argv)
    import geopandas as gpd
    out = pathlib.Path(out) if out else HERE / "data" / "calibration" / "nepal"
    grid = C.read_shakemap_grid(a.shakemap, a.uncertainty)
    wards = gpd.read_file(a.wards).to_crs(4326)
    wards[a.ward_col] = wards[a.ward_col].astype(str)                   # ids must match as text (int vs str mismatches are silent)
    pop = None
    if a.pop:
        import population
        lon0, lat0, lon1, lat1 = wards.total_bounds
        pop = population.read_pop_points(a.pop, (lon0, lat0, lon1, lat1), 0.01)
    wp = C.ward_pga(grid, wards, a.ward_col, pop)
    out.mkdir(parents=True, exist_ok=True)
    wp.to_parquet(out / "ward_pga.parquet")
    sv = pd.read_csv(a.survey, low_memory=False)
    sv[a.survey_ward_col] = sv[a.survey_ward_col].astype(str)
    sv["cls"] = C.classify_nepal(sv)
    print("class counts:", sv.cls.value_counts(dropna=False).to_dict())
    gm = C.GRADE_TO_STATE_ALT if a.alt_grades else C.GRADE_TO_STATE
    holdout = [h for h in a.holdout.split(",") if h]
    if holdout:
        sv_test = sv[sv[a.district_col].astype(str).isin(holdout)]; sv = sv[~sv[a.district_col].astype(str).isin(holdout)]
    groups = C.to_groups(sv, a.survey_ward_col, a.grade_col, wp.rename_axis(a.survey_ward_col), grade_map=gm)
    groups = groups.rename(columns={a.survey_ward_col: "ward"}) if a.survey_ward_col in groups else groups
    fits = C.fit_classes(groups, out, n_boot=a.boot, truncated=not a.untruncated, plots=out / "plots" if out != HERE / "data" / "calibration" / "nepal" else HERE / "docs" / "calibration")
    for c, r in fits.items():
        print(c, r.get("skipped") or {k: round(r[k]["mle"], 3) for k in ("theta_slight", "theta_moderate", "theta_extensive", "theta_complete", "beta")},
              "narrow CI" if r.get("narrow_ci") else "")
    if holdout:
        tg = C.to_groups(sv_test, a.survey_ward_col, a.grade_col, wp.rename_axis(a.survey_ward_col), grade_map=gm)
        for c, r in fits.items():
            if "skipped" in r or not len(tg[tg.cls == c]):
                continue
            res = {"theta": np.array([r[k]["mle"] for k in ("theta_slight", "theta_moderate", "theta_extensive", "theta_complete")]), "beta": r["beta"]["mle"]}
            print("held-out", c, C.holdout_score(res, tg[tg.cls == c], groups[groups.cls == c]))
    if a.apply:
        import priors
        cur = json.loads(priors.path("fragility.json").read_text())
        new = C.choose_final(fits, cur)
        dest = (out / "fragility.json") if out != HERE / "data" / "calibration" / "nepal" else HERE / "data" / "priors" / "fragility.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(new, indent=1))
        print("wrote", dest)

if __name__ == "__main__":
    main()
