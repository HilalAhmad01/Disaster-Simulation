"""Soil stiffness (Vs30, m/s) at each building from the USGS Global Vs30 Mosaic (median GeoTIFF, ~900 m cells).
Put vs30_mosaic_median_30c.tif in data/rasters/ (data/ also works), or point the VS30_TIF environment variable at it.
Without the file, a flat 250 m/s (soft alluvium) is used.
"""
import os, pathlib
import numpy as np

FLAT = 250.0
FLAGS = (600, 601, 602, 603)    # ocean, ice, glacier, lake markers in the mosaic file
_here = pathlib.Path(__file__).resolve().parent
_root = _here.parent if _here.name == "backend" else _here
def _default():
    for p in (_root / "data" / "rasters" / "vs30_mosaic_median_30c.tif", _root / "data" / "vs30_mosaic_median_30c.tif"):
        if p.exists():
            return p
    return _root / "data" / "rasters" / "vs30_mosaic_median_30c.tif"
DEFAULT = _default()

def bilinear(arr, x0, dx, y0, dy, lon, lat):
    """Sample a north-up grid (x0,y0 = outer corner of the first cell, dy negative) at points, interpolating between cell centres."""
    cx = np.clip((lon - x0) / dx - 0.5, 0, arr.shape[1] - 1)
    cy = np.clip((lat - y0) / dy - 0.5, 0, arr.shape[0] - 1)
    i0, j0 = np.floor(cx).astype(int), np.floor(cy).astype(int)
    i1, j1 = np.minimum(i0 + 1, arr.shape[1] - 1), np.minimum(j0 + 1, arr.shape[0] - 1)
    fx, fy = cx - i0, cy - j0
    return (arr[j0, i0] * (1 - fx) * (1 - fy) + arr[j0, i1] * fx * (1 - fy)
            + arr[j1, i0] * (1 - fx) * fy + arr[j1, i1] * fx * fy)

def override_path():
    """Optional local microzonation raster (Vs30 in m/s, nodata/0 where unknown): data/rasters/vs30_override.tif or
    the VS30_OVERRIDE_TIF environment variable. Where it has a value it wins over the mosaic."""
    p = os.environ.get("VS30_OVERRIDE_TIF")
    p = pathlib.Path(p) if p else _root / "data" / "rasters" / "vs30_override.tif"
    return p if p.exists() else None

def apply_override(v, lat, lon, path):
    """Replace v by the override raster's value (nearest cell) wherever that cell holds a usable Vs30. Returns (v, share replaced)."""
    import rasterio
    from rasterio.windows import from_bounds
    with rasterio.open(path) as ds:
        win = from_bounds(lon.min(), lat.min(), lon.max(), lat.max(), ds.transform).round_offsets().round_lengths()
        arr = ds.read(1, window=win, boundless=True, masked=True)
        t = ds.window_transform(win)
    a = np.ma.filled(arr.astype("float64"), np.nan)
    a[(a < 100) | (a > 3000)] = np.nan
    j = np.clip(np.floor((lat - t.f) / t.e).astype(int), 0, a.shape[0] - 1)
    i = np.clip(np.floor((lon - t.c) / t.a).astype(int), 0, a.shape[1] - 1)
    inside = (lon >= t.c) & (lon <= t.c + t.a * a.shape[1]) & (lat <= t.f) & (lat >= t.f + t.e * a.shape[0])
    o = a[j, i]
    use = inside & np.isfinite(o)
    return np.where(use, np.clip(o, 150, 1300), v), float(use.mean())

def vs30_at(lat, lon, tif=None):
    """Return (vs30 array, source text) for points given as numpy arrays of lat and lon."""
    v, src = _mosaic_vs30(lat, lon, tif)
    ov = override_path()
    if ov is not None:
        v, share = apply_override(v, lat, lon, ov)
        if share > 0:
            src += f" + local microzonation override ({share:.0%} of buildings)"
    return v, src

def _mosaic_vs30(lat, lon, tif=None):
    path = pathlib.Path(tif or os.environ.get("VS30_TIF") or _default())
    if not path.exists():
        return np.full(len(lat), FLAT), f"flat {FLAT:.0f} m/s (no soil map found)"
    import rasterio
    from rasterio.windows import from_bounds
    pad = 0.05
    with rasterio.open(path) as ds:
        win = from_bounds(lon.min() - pad, lat.min() - pad, lon.max() + pad, lat.max() + pad, ds.transform)
        win = win.round_offsets().round_lengths()
        arr = ds.read(1, window=win, boundless=True, masked=True)
        t = ds.window_transform(win)
    if t.b != 0 or t.d != 0:
        raise ValueError("Rotated soil raster is not supported")
    a = np.ma.filled(arr.astype("float64"), np.nan)
    a[np.isin(a, FLAGS)] = np.nan
    ok = np.isfinite(a)
    if not ok.any():
        return np.full(len(lat), FLAT), "flat (soil map has no land values here)"
    a[~ok] = np.nanmedian(a)
    v = bilinear(a, t.c, t.a, t.f, t.e, lon, lat)
    return np.clip(v, 150, 1300), "USGS Global Vs30 Mosaic"
