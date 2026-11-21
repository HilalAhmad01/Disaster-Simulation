"""Build the two inputs of tools/calibrate_fragility.py from the openly mirrored Nepal 2015 building table and the HRRP ward
polygons, both obtained by tools/get_nepal_calibration.py:

  python tools/prepare_nepal_survey.py

Reads   data/external/nepal_raw/extracted/csv_building_structure.csv   (derived from the CBS/NPC Household Registration for
                                                                        Housing Reconstruction survey; 762,106 buildings in the
                                                                        11 complete-census districts)
        data/external/nepal_wards/ward/Ward_Boundary_31_Districts.shp  (HRRP / MoFALD federal-structure wards, CC0 on HDX)
Writes  data/calibration/nepal/survey.csv   slim table with numeric damage grade, one row per building
        data/calibration/nepal/wards.gpkg   one polygon per ward, EPSG:4326, column ward_id (same coding as the survey: DD GN WW)

The survey's ward_id and the polygons' DDGNWW use one coding, so the join is exact. Wards without a polygon are reported and
dropped later by the calibration (their buildings are ~5%). Memory: reads only the needed columns, ~300 MB.
"""
import pathlib, sys
import numpy as np, pandas as pd
HERE = pathlib.Path(__file__).resolve().parent.parent
RAW = HERE / "data" / "external" / "nepal_raw" / "extracted" / "csv_building_structure.csv"
WARDS = HERE / "data" / "external" / "nepal_wards" / "ward" / "Ward_Boundary_31_Districts.shp"
OUT = HERE / "data" / "calibration" / "nepal"
COLS = ["building_id", "district_id", "vdcmun_id", "ward_id", "count_floors_pre_eq", "age_building", "foundation_type", "roof_type",
        "ground_floor_type", "other_floor_type", "has_superstructure_adobe_mud", "has_superstructure_mud_mortar_stone",
        "has_superstructure_stone_flag", "has_superstructure_cement_mortar_stone", "has_superstructure_mud_mortar_brick",
        "has_superstructure_cement_mortar_brick", "has_superstructure_timber", "has_superstructure_bamboo",
        "has_superstructure_rc_non_engineered", "has_superstructure_rc_engineered", "has_superstructure_other",
        "condition_post_eq", "damage_grade"]

def main():
    import geopandas as gpd
    for p in (RAW, WARDS):
        if not p.exists():
            sys.exit(f"missing {p}; run tools/get_nepal_calibration.py first")
    OUT.mkdir(parents=True, exist_ok=True)
    sv = pd.read_csv(RAW, usecols=COLS, low_memory=False)
    n0 = len(sv)
    sv["damage_grade_num"] = pd.to_numeric(sv.damage_grade.astype(str).str.extract(r"(\d)")[0], errors="coerce")
    sv = sv.dropna(subset=["damage_grade_num"])
    sv["damage_grade_num"] = sv.damage_grade_num.astype(int)
    sv.to_csv(OUT / "survey.csv", index=False)
    print(f"survey.csv: {len(sv)} of {n0} rows (rows without a grade dropped)")
    w = gpd.read_file(WARDS)
    w["ward_id"] = w.DDGNWW.astype("Int64")
    w = w.dropna(subset=["ward_id"])
    w = w[w.ward_id.isin(sv.ward_id.unique())]
    w = w[["ward_id", "DISTRICT", "GaPa_NaPa", "geometry"]].dissolve(by="ward_id", aggfunc="first").reset_index()   # 21 ids occur twice
    w = w.to_crs(4326)
    w["ward_id"] = w.ward_id.astype(int)
    w.to_file(OUT / "wards.gpkg", driver="GPKG")
    have = set(w.ward_id)
    m = sv.ward_id.isin(have)
    print(f"wards.gpkg: {len(w)} polygons for {sv.ward_id.nunique()} survey wards; buildings with a polygon: {m.mean():.1%}")

if __name__ == "__main__":
    main()
