"""Building heights from Google Open Buildings 2.5D Temporal rasters -> storeys.

Raster files come from tools/get_google_heights.py: data/rasters/google_heights/<area_id>/h<year>_<epsg>.tif
(float32 metres above ground, projected UTM CRS, ~2 m pixels). Method (MASTER_PLAN 1.1):
  1. per building and year: median height of the raster pixels whose centre falls inside the footprint
     (footprints with fewer than MIN_PIX pixels use bilinear sampling at the centroid instead);
  2. combine the years with the median; h_spread = max - min across years (the product is not stable year to year);
  3. storeys = clip(round(h / STOREY_M), 1, 40).
"""
import pathlib, re, warnings
import numpy as np, pandas as pd

STOREY_M = 2.8          # metres per storey: the value in 2.8-3.5 m with the smallest bias vs labelled Noida buildings (docs/validation.md#exposure)
MIN_PIX = 2             # footprints covering fewer pixels than this are sampled at the centroid
MAX_H = 100.0           # the product is defined on [0, 100] m
BAND_PIX = 4_000_000    # raster pixels held in memory per band of buildings

def bilinear(arr, transform, x, y, nodata=-99.0):
    """Bilinear sample of a north-up raster at points (same CRS as the transform), between pixel centres.
    Neighbours equal to `nodata` or NaN are left out and the weights renormalised; NaN if all four are missing."""
    cx = np.clip((x - transform.c) / transform.a - 0.5, 0, arr.shape[1] - 1)
    cy = np.clip((y - transform.f) / transform.e - 0.5, 0, arr.shape[0] - 1)
    i0, j0 = np.floor(cx).astype(int), np.floor(cy).astype(int)
    i1, j1 = np.minimum(i0 + 1, arr.shape[1] - 1), np.minimum(j0 + 1, arr.shape[0] - 1)
    fx, fy = cx - i0, cy - j0
    num, den = 0.0, 0.0
    for jj, ii, w in ((j0, i0, (1 - fx) * (1 - fy)), (j0, i1, fx * (1 - fy)), (j1, i0, (1 - fx) * fy), (j1, i1, fx * fy)):
        v = arr[jj, ii].astype(float)
        ok = np.isfinite(v) & (v != nodata)
        num = num + np.where(ok, v, 0.0) * w
        den = den + np.where(ok, w, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 1e-9, num / den, np.nan)

def zonal_median(polys, arr, transform, nodata=-99.0, min_pix=MIN_PIX, band_pix=BAND_PIX):
    """Median of `arr` pixels inside each polygon (all_touched=False: pixel centre inside the footprint).
    polys: array of shapely polygons in the raster's CRS (north-up raster). Returns (median per polygon, valid pixel
    count per polygon). Polygons with fewer than min_pix valid pixels get the bilinear value at their centroid.
    Buildings are processed in bands (sorted by y) so memory stays bounded for big areas; the result is exact."""
    import shapely
    from rasterio.features import rasterize
    from rasterio.windows import Window, transform as wtransform
    polys = np.asarray(polys, dtype=object)
    n = len(polys)
    med, cnt = np.full(n, np.nan), np.zeros(n, int)
    if n == 0:
        return med, cnt
    b = shapely.bounds(polys)
    px = abs(transform.a)
    order = np.argsort(-(b[:, 1] + b[:, 3]))
    H, W = arr.shape
    start = 0
    while start < n:
        end = min(start + 4096, n)
        while end - start > 1:                      # shrink the band until its raster window fits in memory
            sel = order[start:end]
            if (b[sel, 3].max() - b[sel, 1].min()) / px * W <= band_pix:
                break
            end = start + max(1, (end - start) // 2)
        sel = order[start:end]
        r0 = int(np.clip(np.floor((transform.f - b[sel, 3].max()) / px) - 1, 0, H - 1))
        r1 = int(np.clip(np.ceil((transform.f - b[sel, 1].min()) / px) + 2, r0 + 1, H))
        wt = wtransform(Window(0, r0, W, r1 - r0), transform)
        sub = arr[r0:r1]
        shapes = [(polys[i], k + 1) for k, i in enumerate(sel) if polys[i] is not None and not polys[i].is_empty]
        if shapes:
            lab = rasterize(shapes, out_shape=sub.shape, transform=wt, fill=0, dtype="int32", all_touched=False)
            m = (lab > 0) & (sub != nodata) & np.isfinite(sub)
            if m.any():
                g = pd.DataFrame({"k": lab[m], "v": sub[m]}).groupby("k")["v"].agg(["median", "size"])
                ids = sel[g.index.to_numpy() - 1]
                med[ids], cnt[ids] = g["median"].to_numpy(), g["size"].to_numpy()
        start = end
    few = cnt < min_pix                              # tiny footprints: sample at the centroid
    if few.any():
        c = shapely.centroid(polys[few])
        med[few] = bilinear(arr, transform, shapely.get_x(c), shapely.get_y(c), nodata)
    return med, cnt

def _year_files(folder):
    """{(year, epsg): path} for the h<year>_<epsg>.tif rasters in a folder."""
    out = {}
    for p in pathlib.Path(folder).glob("h*_*.tif"):
        m = re.fullmatch(r"h(\d{4})_(\d+)\.tif", p.name)
        if m:
            out[(int(m.group(1)), int(m.group(2)))] = p
    return out

def building_heights(geoms, folder, years=None):
    """Per-building Google height. geoms: footprints (EPSG:4326). Returns a DataFrame with columns
    h_google (median across years, m), h_spread (max-min across years, m), h_years (number of years used),
    or None when the folder has no rasters. A footprint is looked up in the raster that contains its centroid."""
    import rasterio, shapely
    from pyproj import Transformer
    files = _year_files(folder) if folder and pathlib.Path(folder).exists() else {}
    if years:
        files = {k: v for k, v in files.items() if k[0] in years}
    if not files:
        return None
    geoms = np.asarray(geoms, dtype=object)
    n = len(geoms)
    per_year = {}                                    # year -> per-building medians
    cen = shapely.centroid(geoms)
    lon, lat = shapely.get_x(cen), shapely.get_y(cen)
    for (year, epsg), path in sorted(files.items()):
        tr = Transformer.from_crs(4326, epsg, always_xy=True)
        with rasterio.open(path) as ds:
            cx, cy = tr.transform(lon, lat)
            inside = (cx >= ds.bounds.left) & (cx <= ds.bounds.right) & (cy >= ds.bounds.bottom) & (cy <= ds.bounds.top)
            if not inside.any():
                continue
            idx = np.where(inside)[0]
            utm = shapely.transform(geoms[idx], lambda c: np.column_stack(tr.transform(c[:, 0], c[:, 1])))
            med, _ = zonal_median(utm, ds.read(1), ds.transform, nodata=ds.nodata if ds.nodata is not None else -99.0)
        col = per_year.setdefault(year, np.full(n, np.nan))
        col[idx] = med
    if not per_year:
        return None
    M = np.column_stack([per_year[y] for y in sorted(per_year)])
    M = np.where((M < 0) | (M > MAX_H), np.nan, M)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        h, lo, hi = np.nanmedian(M, 1), np.nanmin(M, 1), np.nanmax(M, 1)
    return pd.DataFrame({"h_google": h, "h_spread": hi - lo, "h_years": np.isfinite(M).sum(1)})

def storeys_from_height(h, storey_m=None):
    """Whole storeys from a height in metres."""
    return np.clip(np.round(np.asarray(h, float) / (storey_m or STOREY_M)), 1, 40)
