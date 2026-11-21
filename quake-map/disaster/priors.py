"""Small look-up tables shipped with the code (priors/) that users can override in data/priors/.

construction_mix.csv : region_id, urban, storeys_bin, p_URM, p_RC_LOW, p_RC_MID, source   (tools/build_construction_priors.py)
occupancy.csv        : use, night, day, commute, source                                   (time-of-day shares, see population.py)
"""
import pathlib
import numpy as np, pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
TYPES = ("URM", "RC_LOW", "RC_MID")
BINS = ("1", "2", "3-4", "5-7", "8+")

def path(name, data_dir=None):
    """data/priors/<name> wins over the shipped priors/<name>."""
    if data_dir:
        p = pathlib.Path(data_dir) / "priors" / name
        if p.exists():
            return p
    return HERE / "priors" / name

def storeys_bin(levels):
    """Index 0..4 of the storey bin (1, 2, 3-4, 5-7, 8+) for an array of storeys."""
    return np.digitize(np.asarray(levels, float), [1.5, 2.5, 4.5, 7.5])

def construction_mix(region="IN-urban", data_dir=None):
    """(5, 3) array of type probabilities (columns URM, RC_LOW, RC_MID) per storey bin for a region."""
    df = pd.read_csv(path("construction_mix.csv", data_dir))
    sub = df[df.region_id == region]
    if sub.empty:
        sub = df[df.region_id == df.region_id.iloc[0]]
    sub = sub.set_index("storeys_bin").loc[list(BINS)]
    return sub[["p_" + t for t in TYPES]].to_numpy(float)

def type_probs(levels, engineered, mix=None, engineered_floor=0.8):
    """Probability of each construction type per building: the storey-bin prior, then the engineered rule
    (industrial/office/retail/hotel/education/medical uses or footprints >= 1500 m2 with up to 4 storeys are RC_LOW
    with probability at least `engineered_floor`; the rest is shared in proportion to the prior)."""
    mix = construction_mix() if mix is None else mix
    p = mix[storeys_bin(levels)].copy()
    eng = np.asarray(engineered, bool) & (np.asarray(levels, float) <= 4)
    if eng.any():
        lo = p[eng, 1]
        new = np.maximum(lo, engineered_floor)
        rest = 1 - lo
        scale = np.where(rest > 1e-12, (1 - new) / np.where(rest > 1e-12, rest, 1), 0.0)
        p[eng, 0] *= scale
        p[eng, 2] *= scale
        p[eng, 1] = new
    return p

def occupancy(data_dir=None):
    """DataFrame indexed by use with columns night, day, commute (shares of capacity; residential: of the home population)."""
    return pd.read_csv(path("occupancy.csv", data_dir)).set_index("use")
