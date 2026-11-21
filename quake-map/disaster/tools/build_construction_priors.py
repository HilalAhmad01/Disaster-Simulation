"""Build priors/construction_mix.csv (probability of each construction type by storey bin) from census material shares.

  python tools/build_construction_priors.py                       # uses priors/census_materials.csv
  python tools/build_construction_priors.py --census my_town.csv  # columns: region_id,urban,wall_burnt_brick_or_stone,roof_concrete,source

Input shares are fractions of households (Census of India 2011, Table HL-03 'predominant material of roof and wall').
Caveat: 'burnt brick wall' does not separate load-bearing masonry from RC frame with brick infill, and a concrete roof is
common to both, so the census gives a PRIOR, not an answer; the storey count is the stronger signal.

Rules (MASTER_PLAN 1.2), b = share of burnt-brick/stone walls, c = share of concrete roofs. Walls and roofs are assumed
independent unless the census gives the joint table:
  storeys 1-2 : URM = b(1-c) + 0.5*b*c   (brick/stone walls with non-concrete roof, plus half of those with concrete roof)
                RC_LOW = 1 - URM
  storeys 3-4 : RC_LOW = clip(c, 0.6, 0.95);  URM = 1 - RC_LOW
  storeys 5-7 and 8+ : RC_MID 0.95, RC_LOW 0.05
Engineered uses (industrial, warehouse, office, retail, hotel, education, medical) or footprints >= 1500 m2 are
adjusted at run time in exposure.py (RC_LOW at least 0.8 for up to 4 storeys); that rule is not stored here.
"""
import argparse, pathlib
import pandas as pd
HERE = pathlib.Path(__file__).resolve().parent.parent
BINS = ["1", "2", "3-4", "5-7", "8+"]

def mix_for(b, c):
    urm12 = b * (1 - c) + 0.5 * b * c
    rc34 = min(max(c, 0.6), 0.95)
    rows = {"1": (urm12, 1 - urm12, 0.0), "2": (urm12, 1 - urm12, 0.0), "3-4": (1 - rc34, rc34, 0.0),
            "5-7": (0.0, 0.05, 0.95), "8+": (0.0, 0.05, 0.95)}
    return rows

def build(census):
    out = []
    for r in census.itertuples():
        for bin_, (u, l, m) in mix_for(float(r.wall_burnt_brick_or_stone), float(r.roof_concrete)).items():
            out.append(dict(region_id=r.region_id, urban=int(r.urban), storeys_bin=bin_, p_URM=round(u, 4), p_RC_LOW=round(l, 4),
                            p_RC_MID=round(m, 4), source=r.source))
    return pd.DataFrame(out)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--census", default=str(HERE / "priors" / "census_materials.csv"))
    ap.add_argument("--out", default=str(HERE / "priors" / "construction_mix.csv"))
    a = ap.parse_args()
    df = build(pd.read_csv(a.census))
    assert (abs(df[["p_URM", "p_RC_LOW", "p_RC_MID"]].sum(axis=1) - 1) < 1e-3).all()
    df.to_csv(a.out, index=False)
    print(df.drop(columns="source").to_string(index=False))
    print("wrote", a.out)
