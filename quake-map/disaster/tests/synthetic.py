"""Deterministic synthetic exposure (no download) used by regression tests and baselines."""
import numpy as np, geopandas as gpd
from shapely.geometry import box

def synthetic_exposure(n=1000, seed=7, centre=(77.33, 28.575), span=(0.06, 0.05)):
    """n square footprints (~15-40 m) scattered over a 6 x 5 km box with fixed-seed attributes. Columns follow the
    exposure table of exposure.make_exposure (kept in step with it by tests/test_exposure.py)."""
    rng = np.random.default_rng(seed)
    lon = centre[0] + (rng.random(n) - .5) * span[0]
    lat = centre[1] + (rng.random(n) - .5) * span[1]
    half = rng.uniform(7e-5, 1.8e-4, n)
    geoms = [box(x - h, y - h, x + h, y + h) for x, y, h in zip(lon, lat, half)]
    levels = rng.choice([1, 2, 3, 4, 6, 9], n, p=[.15, .3, .25, .15, .1, .05])
    btype = np.where(levels <= 2, "URM", np.where(levels <= 4, "RC_LOW", "RC_MID"))
    use = rng.choice(["residential", "office", "retail", "industrial"], n, p=[.8, .08, .07, .05])
    cap = np.round(((2 * half * 111e3) ** 2) * levels * 0.8 / 15).astype(int).clip(1, 4000)
    g = gpd.GeoDataFrame({"btype": btype, "levels": levels, "occupants": cap, "use": use}, geometry=geoms, crs=4326)
    return g
