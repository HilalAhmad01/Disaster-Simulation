"""Soil stiffness (Vs30, m/s) at each building from the USGS Global Vs30 Mosaic (median GeoTIFF, ~900 m cells).
Put vs30_mosaic_median_30c.tif in the data/ folder, or point the VS30_TIF environment variable at it.
Without the file, a flat 250 m/s (soft alluvium) is used.
"""
import os, pathlib
import numpy as np

FLAT = 250.0
FLAGS = (600, 601, 602, 603)    # ocean, ice, glacier, lake markers in the mosaic file
_here = pathlib.Path(__file__).resolve().parent
_root = _here.parent if _here.name == "backend" else _here
DEFAULT = _root / "data" / "vs30_mosaic_median_30c.tif"

def bilinear(arr, x0, dx, y0, dy, lon, lat):
    """Sample a north-up grid (x0,y0 = outer corner of the first cell, dy negative) at points, interpolating between cell centres."""
    cx = np.clip((lon - x0) / dx - 0.5, 0, arr.shape[1] - 1)
    cy = np.clip((lat - y0) / dy - 0.5, 0, arr.shape[0] - 1)
    i0, j0 = np.floor(cx).astype(int), np.floor(cy).astype(int)
    i1, j1 = np.minimum(i0 + 1, arr.shape[1] - 1), np.minimum(j0 + 1, arr.shape[0] - 1)
    fx, fy = cx - i0, cy - j0
    return (arr[j0, i0] * (1 - fx) * (1 - fy) + arr[j0, i1] * fx * (1 - fy)
            + arr[j1, i0] * (1 - fx) * fy + arr[j1, i1] * fx * fy)

def vs30_at(lat, lon, tif=None):
    """Return (vs30 array, source text) for points given as numpy arrays of lat and lon."""
    path = pathlib.Path(tif or os.environ.get("VS30_TIF") or DEFAULT)
    if not path.exists():
        return np.full(len(lat), FLAT), f"flat {FLAT:.0f} m/s (no soil map found at {path})"
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
