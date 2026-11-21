"""People per building, by time of day, matched to GHS-POP.

capacity   people a building holds at typical occupancy (floor area * 0.8 / m2 per person by use; exposure.DENS)
night      residential: capacity scaled so the 1 km cell total equals GHS-POP; others: small night share of capacity
day/commute  shares of the night (home) population or of capacity (priors/occupancy.csv)
Without a population raster, night = capacity and the summary says so.
"""
import pathlib
import numpy as np, pandas as pd

CELL = 1000.0                 # metres: GHS-POP is summed to cells this size before scaling (softens footprint misalignment)
FACTOR_RANGE = (0.05, 3.0)    # allowed scaling of capacity to match GHS-POP; cells outside use the neighbourhood median

def raster_path(folder):
    """First pop*.tif in a folder, or None."""
    if folder and pathlib.Path(folder).exists():
        hits = sorted(pathlib.Path(folder).glob("pop*.tif"))
        return hits[0] if hits else None
    return None

def read_pop_points(path, bbox, pad=0.0):
    """Pixel centres inside bbox (w, s, e, n) with their population: arrays lon, lat, pop (people per pixel)."""
    import rasterio
    from rasterio.windows import from_bounds
    w, s, e, n = bbox
    with rasterio.open(path) as ds:
        win = from_bounds(w - pad, s - pad, e + pad, n + pad, ds.transform).round_offsets().round_lengths()
        a = ds.read(1, window=win, boundless=True, masked=True)
        t = ds.window_transform(win)
    a = np.ma.filled(a.astype("float64"), 0.0)
    a[~np.isfinite(a) | (a < 0)] = 0.0
    rows, cols = np.mgrid[0:a.shape[0], 0:a.shape[1]]
    lon, lat = t.c + (cols + 0.5) * t.a, t.f + (rows + 0.5) * t.e
    return lon.ravel(), lat.ravel(), a.ravel()

def _neighbour_median(fac, ok, keyx, keyy):
    """Median of in-range factors in the 3x3 neighbourhood of each cell (NaN if none)."""
    lut = {(x, y): f for x, y, f, o in zip(keyx, keyy, fac, ok) if o}
    out = np.full(len(fac), np.nan)
    for i, (x, y) in enumerate(zip(keyx, keyy)):
        vals = [lut[(x + dx, y + dy)] for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (x + dx, y + dy) in lut]
        if vals:
            out[i] = np.median(vals)
    return out

def assign(g, x, y, pop=None, bbox=None, to_lonlat=None, data_dir=None):
    """Compute people per building at night/day/commute.
    g: exposure frame with columns use, capacity. x, y: building centroids in metres (UTM of the area).
    pop: (lon, lat, people) pixel arrays from read_pop_points, or None. bbox + to_lonlat (x,y -> lon,lat) are used
    to find 1 km cells that stick out of the area (their building set is incomplete; they take the median factor).
    Returns (DataFrame with columns night, day, commute, factor, stats dict)."""
    from priors import occupancy
    occ = occupancy(data_dir)
    use = g["use"].astype(object).fillna("large").to_numpy()
    cap = g["capacity"].to_numpy(float)
    res = use == "residential"
    share = {k: np.array([occ[k].get(u, occ[k]["large"]) for u in use]) for k in ("night", "day", "commute")}
    stats = {"capacity_total": float(cap.sum())}
    factor = np.ones(len(g))
    if pop is None:
        stats.update(pop_src="capacity (no population raster found; night = typical occupancy)", ghs_total=None)
        night = np.where(res, cap, cap * share["night"])
    else:
        lon, lat, p = pop
        plon, plat = lon, lat
        # put pixels on the same 1 km grid as the buildings; both go through the same projection
        px, py = to_lonlat[1](plon, plat)         # lon/lat -> metres (inverse supplied by caller)
        key = lambda xx, yy: (np.floor(xx / CELL).astype(np.int64), np.floor(yy / CELL).astype(np.int64))
        bx, by = key(x, y)
        pxk, pyk = key(px, py)
        in_area = (plon >= bbox[0]) & (plon <= bbox[2]) & (plat >= bbox[1]) & (plat <= bbox[3])
        ghs = pd.DataFrame({"kx": pxk[in_area], "ky": pyk[in_area], "pop": p[in_area]}).groupby(["kx", "ky"])["pop"].sum()
        capres = pd.DataFrame({"kx": bx[res], "ky": by[res], "cap": cap[res]}).groupby(["kx", "ky"])["cap"].sum()
        cells = pd.concat([ghs.rename("pop"), capres.rename("cap")], axis=1).fillna(0.0)
        cells["raw"] = np.where(cells["cap"] > 0, cells["pop"] / cells["cap"].where(cells["cap"] > 0, 1), np.nan)
        kx, ky = cells.index.get_level_values(0).to_numpy(), cells.index.get_level_values(1).to_numpy()
        # cells that stick out of the requested box hold only some of their buildings: do not trust their factor
        cx, cy = (kx + 0.5) * CELL, (ky + 0.5) * CELL
        ok_edge = np.ones(len(cells), bool)
        for ddx, ddy in ((-.5, -.5), (-.5, .5), (.5, -.5), (.5, .5)):
            lo, la = to_lonlat[0](cx + ddx * CELL, cy + ddy * CELL)
            ok_edge &= (lo >= bbox[0]) & (lo <= bbox[2]) & (la >= bbox[1]) & (la <= bbox[3])
        fac = cells["raw"].to_numpy()
        in_range = np.isfinite(fac) & (fac >= FACTOR_RANGE[0]) & (fac <= FACTOR_RANGE[1]) & ok_edge
        gmed = float(np.median(fac[in_range])) if in_range.any() else 1.0
        nb = _neighbour_median(fac, in_range, kx, ky)
        final = np.where(in_range, fac, np.where(np.isfinite(nb), nb, gmed))
        cells["factor"] = final
        cells["flag"] = ~in_range & (cells["cap"] > 0)
        fmap = cells["factor"]
        f_b = fmap.reindex(pd.MultiIndex.from_arrays([bx, by])).to_numpy()
        f_b = np.where(np.isfinite(f_b), f_b, gmed)
        factor = f_b
        night = np.where(res, cap * f_b, cap * share["night"])
        stats.update(pop_src="GHS-POP", ghs_total=float(p[in_area].sum()),
                     cells=int(len(cells)), cells_flagged=int(cells["flag"].sum()), factor_median=gmed,
                     pop_unallocated=float(cells.loc[cells["cap"] <= 0, "pop"].sum()))
    day = np.where(res, night * share["day"], cap * share["day"])
    commute = np.where(res, night * share["commute"], cap * share["commute"])
    stats.update(night_total=float(night.sum()), day_total=float(day.sum()), commute_total=float(commute.sum()))
    return pd.DataFrame({"night": night, "day": day, "commute": commute, "factor": factor}), stats
