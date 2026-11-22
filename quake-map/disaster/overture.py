"""Download building footprints for a bounding box from Overture Maps (needs internet and `pip install overturemaps`).

Only the columns in KEEP are read (about 28% fewer bytes than all of them). The `bbox` struct is kept (flattened to
xmin/xmax/ymin/ymax) so a cached area can later be clipped to a smaller box with exactly Overture's own rule.
"""
import os
from concurrent.futures import ThreadPoolExecutor
import geopandas as gpd
import numpy as np
import shapely

DL_THREADS = int(os.environ.get("OVERTURE_THREADS", "8"))   # Parquet row groups fetched at once (each is a separate request to Overture's storage)

KEEP = ("geometry", "bbox", "class", "subtype", "height", "num_floors")
BBOX_COLS = ("xmin", "xmax", "ymin", "ymax")

def clip_to_bbox(raw, bbox):
    """Select footprints the way Overture does: the footprint's *bbox* must intersect the query box.
    Same comparisons as overturemaps.core._prepare_query: xmin < e, xmax > w, ymin < n, ymax > s. bbox = (w, s, e, n)."""
    w, s, e, n = bbox
    keep = (raw["xmin"].to_numpy() < e) & (raw["xmax"].to_numpy() > w) & (raw["ymin"].to_numpy() < n) & (raw["ymax"].to_numpy() > s)
    return raw[keep].reset_index(drop=True)

def latest_release():
    """Newest Overture release id (one small request), or None if it cannot be found."""
    try:
        from overturemaps import core
        return core.get_latest_release()
    except Exception:
        return None

def _read_columns(core, bbox, release=None, progress=None):
    """Read only the KEEP columns. Uses a private helper of the overturemaps package; returns False if this version
    does not have it, None if nothing intersects the box. progress(done, total) is called per Parquet row group."""
    import pyarrow as pa
    prepare = getattr(core, "_prepare_query", None)
    if prepare is None:
        return False
    try:
        prepared = prepare("building", bbox, release) if release else prepare("building", bbox)
    except TypeError:                          # signature changed in another version
        return False
    if prepared is None:
        return None
    dataset, flt = prepared
    cols = [c for c in KEEP if c in dataset.schema.names]
    if not hasattr(dataset, "get_fragments"):
        return dataset.to_table(columns=cols, filter=flt)
    parts = []
    for frag in dataset.get_fragments(filter=flt):
        try:
            parts.extend(frag.split_by_row_group(flt))     # row groups whose statistics cannot match are dropped here
        except Exception:
            parts.append(frag)
    if not parts:
        return None
    # The row groups are independent requests, mostly waiting on the network: fetching 8 at once took 6.5 s instead of 11.3 s for the same
    # 6 parts. Results are collected in order, so the table is the same as before.
    tables = []
    with ThreadPoolExecutor(max_workers=max(1, min(DL_THREADS, len(parts)))) as pool:
        futs = [pool.submit(part.to_table, columns=cols, filter=flt) for part in parts]
        try:
            for i, f in enumerate(futs):
                tables.append(f.result())
                if progress:
                    progress(i + 1, len(parts))
        except BaseException:
            for f in futs:
                f.cancel()
            raise
    return pa.concat_tables(tables)

def _flatten_bbox(df):
    """Turn the bbox struct column (dicts after to_pandas) into four float columns."""
    if "bbox" not in df:
        return df
    b = df.pop("bbox")
    for k in BBOX_COLS:
        df[k] = np.array([r[k] if r is not None else np.nan for r in b], dtype="float64")
    return df

def fetch_buildings(bbox, release=None, progress=None):
    """bbox = (west, south, east, north) in degrees. Returns a GeoDataFrame in EPSG:4326 with the KEEP columns
    (bbox flattened to xmin/xmax/ymin/ymax). `release` pins an Overture release (default: latest)."""
    from overturemaps import core
    table = _read_columns(core, bbox, release, progress)
    if table is False:                         # fallback: public reader, downloads every column
        reader = core.record_batch_reader("building", bbox, release) if release else core.record_batch_reader("building", bbox)
        table = reader.read_all() if reader is not None else None
    if table is None or table.num_rows == 0:
        return gpd.GeoDataFrame({c: [] for c in BBOX_COLS}, geometry=[], crs=4326)
    df = table.select([c for c in KEEP if c in table.schema.names]).to_pandas()
    geom = shapely.from_wkb(df.pop("geometry"))
    df = _flatten_bbox(df)
    if "xmin" not in df:                       # very old schema without bbox: derive it from the footprints
        b = shapely.bounds(geom)
        df["xmin"], df["ymin"], df["xmax"], df["ymax"] = b[:, 0], b[:, 1], b[:, 2], b[:, 3]
    return gpd.GeoDataFrame(df, geometry=geom, crs=4326)
