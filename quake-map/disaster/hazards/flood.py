"""Flood hazard (MASTER_PLAN 7.2): water depth at each building from a raster, damage from a depth-damage curve.

Depth source: a GeoTIFF of water depth in metres (a scenario you supply, or a return-period map such as the JRC global river
flood hazard maps; none is bundled). Damage: a depth-damage curve (priors/flood_damage.csv, UNVERIFIED placeholder shape, to be
replaced with the JRC Huizinga et al. 2017 Asia curves) converted to damage states by loss-ratio thresholds; the median depth of
each state is the depth at which the curve reaches the threshold, with a lognormal spread in depth (assumed beta). A building of
several storeys is assumed to lose less per metre of water: effective depth = depth / sqrt(storeys) (assumption).
"""
import numpy as np, pandas as pd
from scipy.special import ndtr
from .base import Hazard

LOSS_THRESHOLDS = (0.10, 0.30, 0.50, 0.80)     # loss ratio at which slight / moderate / extensive / complete damage is reached
BETA = 0.5                                      # ln-sd of the depth that produces each state (assumption)

def depth_damage_curve(data_dir=None):
    import priors
    df = pd.read_csv(priors.path("flood_damage.csv", data_dir))
    return df.depth_m.to_numpy(float), df.loss_ratio.to_numpy(float)

def state_depths(curve=None, thresholds=LOSS_THRESHOLDS):
    """Depth (m) at which the curve reaches each loss threshold (linear interpolation of the monotone curve)."""
    d, l = curve or depth_damage_curve()
    return np.interp(thresholds, l, d)

def sample_depth(raster_path, lon, lat, scale=1.0):
    """Water depth (m) at points from a depth GeoTIFF (bilinear between pixel centres, no-data counted as dry), times scale."""
    import rasterio
    from pyproj import Transformer
    import heights
    with rasterio.open(raster_path) as ds:
        x, y = (lon, lat) if (ds.crs is None or ds.crs.to_epsg() == 4326) else Transformer.from_crs(4326, ds.crs, always_xy=True).transform(lon, lat)
        win = rasterio.windows.from_bounds(x.min() - ds.res[0], y.min() - ds.res[1], x.max() + ds.res[0], y.max() + ds.res[1], ds.transform).round_offsets().round_lengths()
        a = ds.read(1, window=win, boundless=True, masked=True)
        t = ds.window_transform(win)
        nd = ds.nodata
    arr = np.ma.filled(a.astype("float64"), np.nan)
    if nd is not None:
        arr[arr == nd] = np.nan
    arr[arr < -100] = np.nan
    v = heights.bilinear(arr, t, np.asarray(x), np.asarray(y), nodata=-9999.0)
    inside = (x >= t.c) & (x <= t.c + t.a * arr.shape[1]) & (y <= t.f) & (y >= t.f + t.e * arr.shape[0])
    return np.where(inside & np.isfinite(v), np.maximum(v, 0.0), 0.0) * float(scale)

class FloodHazard(Hazard):
    name = "flood"
    cell_key = "depth_m"

    def intensity(self, model, params):
        path = params.get("raster")
        if not path:
            raise ValueError("Choose a flood depth raster (GeoTIFF, metres).")
        depth = sample_depth(path, model.lon, model.lat, params.get("scale", 1.0))
        return {"value": depth}

    def damage(self, model, intensity):
        d = intensity["value"]
        deff = d / np.sqrt(np.maximum(model.g["levels"].to_numpy(float), 1.0))
        th = state_depths()
        with np.errstate(divide="ignore"):
            z = (np.log(np.maximum(deff, 1e-9))[:, None] - np.log(th)[None, :]) / BETA
        return np.where(d[:, None] > 0, ndtr(z), 0.0)

    def table(self, model, intensity):
        return {"intensity": intensity["value"].round(2)}

    def summary(self, model, intensity, p, occ):
        d = intensity["value"]
        return {"peak": [["Deepest water at a building (m)", round(float(d.max()), 2)], ["Buildings with water", int((d > 0).sum())]],
                "max_depth_m": round(float(d.max()), 2), "flooded_buildings": int((d > 0).sum()),
                "hazard_note": "Flood damage curve is an UNVERIFIED placeholder shape; casualties are not modelled for floods."}

    def describe(self):
        return {"name": self.name, "label": "Flood", "intensity": "Water depth", "unit": "m", "inputs": ["raster", "scale", "time_of_day"],
                "note": "Supply a water-depth GeoTIFF for the scenario; the depth-damage curve is a placeholder."}
