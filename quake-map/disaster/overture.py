"""Download building footprints for a bounding box from Overture Maps (needs internet and `pip install overturemaps`)."""
import geopandas as gpd
import shapely

KEEP = ("geometry", "class", "subtype", "height", "num_floors")

def _read_columns(core, bbox):
    """Read only the KEEP columns (about 28% fewer bytes than all of them). Uses a private helper of the
    overturemaps package; returns False if this version does not have it, None if nothing intersects the box."""
    prepare = getattr(core, "_prepare_query", None)
    if prepare is None:
        return False
    try:
        prepared = prepare("building", bbox)
    except TypeError:                          # signature changed in another version
        return False
    if prepared is None:
        return None
    dataset, flt = prepared
    cols = [c for c in KEEP if c in dataset.schema.names]
    return dataset.to_table(columns=cols, filter=flt)

def fetch_buildings(bbox):
    """bbox = (west, south, east, north) in degrees."""
    from overturemaps import core
    table = _read_columns(core, bbox)
    if table is False:                         # fallback: public reader, downloads every column
        reader = core.record_batch_reader("building", bbox)
        table = reader.read_all() if reader is not None else None
    if table is None or table.num_rows == 0:
        return gpd.GeoDataFrame(geometry=[], crs=4326)
    df = table.select([c for c in KEEP if c in table.schema.names]).to_pandas()
    geom = shapely.from_wkb(df.pop("geometry"))
    return gpd.GeoDataFrame(df, geometry=geom, crs=4326)
