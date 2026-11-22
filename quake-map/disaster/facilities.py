"""Hospitals, fire stations, police and shelters for an area (MASTER_PLAN 5.1).

Sources: OpenStreetMap via the Overpass API (small area queries) and Overture 'place' points. Duplicates (same kind within
DEDUPE_M metres with similar or missing names) are merged. Each facility is linked to the building footprint that contains it
(or the nearest within LINK_M); its functionality is 1 - P(extensive damage or worse) of that building, and below 0.5 it is
flagged 'may not be working'. Open spaces (parks, grounds) are not buildings and keep functionality 1; Overpass gives only their centre, so their size is unknown (a limitation).
"""
import difflib, json, pathlib, time
from concurrent.futures import ThreadPoolExecutor
import numpy as np, pandas as pd, geopandas as gpd, shapely

KINDS = ("hospital", "clinic", "ambulance", "fire_station", "police", "shelter", "open_space", "school")
WORKING_KINDS = ("hospital", "fire_station", "police", "ambulance")      # kinds a response can start from
DEDUPE_M, LINK_M, NAME_SIM = 50.0, 30.0, 0.6
OVERPASS = ("https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter", "https://overpass.kumi.systems/api/interpreter")   # public mirrors, tried in turn
UA = "quake-damage-map/1.0 (local research project)"

def _kind(tags):
    a, e, l, lu = (tags.get(k) for k in ("amenity", "emergency", "leisure", "landuse"))
    if a == "hospital": return "hospital"
    if a in ("clinic", "doctors"): return "clinic"
    if e == "ambulance_station": return "ambulance"
    if a == "fire_station": return "fire_station"
    if a == "police": return "police"
    if e == "assembly_point" or a == "shelter": return "shelter"
    if l in ("park", "pitch", "stadium") or lu in ("recreation_ground", "grass") and tags.get("name"): return "open_space"
    if a == "school": return "school"
    return None

def overpass_query(bbox):
    w, s, e, n = bbox
    b = f"({s},{w},{n},{e})"
    return ('[out:json][timeout:120];('
            f'nwr["amenity"~"^(hospital|clinic|doctors|fire_station|police|shelter|school)$"]{b};'
            f'nwr["emergency"~"^(ambulance_station|assembly_point)$"]{b};'
            f'nwr["leisure"~"^(park|pitch|stadium)$"]{b};nwr["landuse"="recreation_ground"]{b};);out center tags;')

def parse_overpass(elements):
    rows = []
    for el in elements:
        tags = el.get("tags", {})
        k = _kind(tags)
        c = el if el["type"] == "node" else el.get("center")
        if k is None or not c:
            continue
        beds = pd.to_numeric(tags.get("beds"), errors="coerce")
        rows.append({"kind": k, "name": tags.get("name") or tags.get("name:en") or "", "lon": c["lon"], "lat": c["lat"], "source": "osm",
                     "beds": float(beds) if pd.notna(beds) else np.nan, "src_id": f"{el['type']}/{el['id']}"})
    return pd.DataFrame(rows, columns=["kind", "name", "lon", "lat", "source", "beds", "src_id"])

def fetch_osm(bbox, session=None, retries=2):
    import requests
    s = session or requests
    last = None
    for url in OVERPASS:
        for _ in range(retries):
            try:
                r = s.post(url, data={"data": overpass_query(bbox)}, timeout=150, headers={"User-Agent": UA})
                if r.ok:
                    return parse_overpass(r.json()["elements"])
                last = f"{url}: HTTP {r.status_code}"
            except Exception as ex:
                last = f"{url}: {ex}"
            time.sleep(3)
    raise RuntimeError(f"Overpass is busy or unreachable ({last})")

OVT = {"hospital": "hospital", "fire_station": "fire_station", "police_station": "police", "public_health_clinic": "clinic"}

def fetch_overture(bbox, release=None, min_confidence=0.5):
    from overturemaps import core
    prep = core._prepare_query("place", bbox, release) if release else core._prepare_query("place", bbox)
    if prep is None:
        return pd.DataFrame(columns=["kind", "name", "lon", "lat", "source", "beds", "src_id"])
    ds, flt = prep
    cols = ["id", "geometry", "names", "taxonomy", "confidence"]
    parts = []
    if hasattr(ds, "get_fragments"):                         # split into row groups and read them at once, like the building download
        for frag in ds.get_fragments(filter=flt):
            try:
                parts.extend(frag.split_by_row_group(flt))
            except Exception:
                parts.append(frag)
    if len(parts) > 1:
        import pyarrow as pa
        with ThreadPoolExecutor(max_workers=min(8, len(parts))) as pool:
            df = pa.concat_tables(list(pool.map(lambda q: q.to_table(columns=cols, filter=flt), parts))).to_pandas()
    else:
        df = ds.to_table(columns=cols, filter=flt).to_pandas()
    kind = df.taxonomy.apply(lambda d: OVT.get(d.get("primary")) if isinstance(d, dict) else None)
    df = df[kind.notna() & (df.confidence.fillna(0) >= min_confidence)].copy()
    kind = kind[df.index]
    pts = shapely.from_wkb(df.geometry)
    return pd.DataFrame({"kind": kind.to_numpy(), "name": [(n or {}).get("primary") or "" if isinstance(n, dict) else "" for n in df.names],
                         "lon": shapely.get_x(pts), "lat": shapely.get_y(pts), "source": "overture", "beds": np.nan, "src_id": df.id.to_numpy()})

def _similar(a, b):
    if not a or not b:
        return True                                           # a missing name does not rule out a match
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() >= NAME_SIM

def merge(osm, ovt):
    """OSM first (it carries beds), Overture rows are added unless an OSM row of the same kind lies within DEDUPE_M with a
    similar/missing name. Overture duplicates among themselves are merged the same way."""
    from scipy.spatial import cKDTree
    both = pd.concat([osm, ovt], ignore_index=True)
    if both.empty:
        return both
    lat0 = both.lat.mean()
    xy = np.column_stack([both.lon * 111320 * np.cos(np.radians(lat0)), both.lat * 110540])
    tree, keep = cKDTree(xy), np.ones(len(both), bool)
    for i in range(len(both)):                                # earlier rows (OSM first) win
        if not keep[i]:
            continue
        for j in tree.query_ball_point(xy[i], DEDUPE_M):
            if j > i and keep[j] and both.kind[j] == both.kind[i] and _similar(both.name[i], both.name[j]):
                keep[j] = False
                if np.isnan(both.beds[i]) and not np.isnan(both.beds[j]):
                    both.loc[i, "beds"] = both.beds[j]
    return both[keep].reset_index(drop=True)

def link(fac, buildings):
    """Add building_id (index into the exposure table; -1 if none) by containment, else nearest footprint within LINK_M metres.
    Open spaces are never linked."""
    g = gpd.GeoDataFrame(fac.copy(), geometry=gpd.points_from_xy(fac.lon, fac.lat), crs=4326)
    u = buildings.estimate_utm_crs()
    P, B = g.to_crs(u), gpd.GeoDataFrame({"bid": np.arange(len(buildings))}, geometry=buildings.geometry.to_crs(u).values, crs=u)
    j = gpd.sjoin(P[["geometry"]], B, how="left", predicate="within")
    j = j[~j.index.duplicated(keep="first")]
    bid = j["bid"].reindex(P.index)
    miss = bid.isna() & ~g.kind.isin(["open_space"])
    if miss.any():
        nn = gpd.sjoin_nearest(P.loc[miss, ["geometry"]], B, how="left", max_distance=LINK_M)
        nn = nn[~nn.index.duplicated(keep="first")]
        bid.loc[nn.index] = nn["bid"]
    out = fac.copy()
    out["building_id"] = bid.fillna(-1).astype(int).to_numpy()
    out.loc[out.kind == "open_space", "building_id"] = -1
    return out

def build(bbox, buildings, osm_fn=fetch_osm, ovt_fn=fetch_overture, release=None):
    """Facility table for a box: columns id, kind, name, lon, lat, source, beds, building_id. Network failures in one source
    do not discard the other: the 'notes' attribute records what was missing."""
    notes, osm, ovt = [], None, None
    # the two sources are independent requests to different servers (about 30 s each on a big city): ask both at once
    with ThreadPoolExecutor(max_workers=2) as pool:
        fo = pool.submit(osm_fn, bbox)
        fv = pool.submit(ovt_fn, bbox, release) if release else pool.submit(ovt_fn, bbox)
        try:
            osm = fo.result()
        except Exception as ex:
            notes.append(f"OpenStreetMap unavailable: {str(ex)[:100]}")
        try:
            ovt = fv.result()
        except Exception as ex:
            notes.append(f"Overture places unavailable: {str(ex)[:100]}")
    empty = pd.DataFrame(columns=["kind", "name", "lon", "lat", "source", "beds", "src_id"])
    f = merge(osm if osm is not None else empty, ovt if ovt is not None else empty)
    f = f[f.lon.between(bbox[0], bbox[2]) & f.lat.between(bbox[1], bbox[3])].reset_index(drop=True)
    f = link(f, buildings) if len(f) else f.assign(building_id=pd.Series(dtype=int))
    f.insert(0, "id", np.arange(len(f)))
    f.attrs["notes"] = notes
    return f

def functionality(fac, p_extensive):
    """Per facility: functionality = 1 - P(extensive or worse) of its building (1 for open spaces / unlinked), and the flag."""
    bid = fac.building_id.to_numpy()
    pe = np.where(bid >= 0, np.asarray(p_extensive)[np.clip(bid, 0, len(p_extensive) - 1)], 0.0)
    fn = 1.0 - pe
    return pd.DataFrame({"p_damaged": pe, "functionality": fn, "may_not_work": fn < 0.5})

def path_for(data_dir, area_id):
    return pathlib.Path(data_dir) / "facilities" / f"{area_id}.parquet"

def load_or_build(data_dir, area_id, bbox, buildings, **kw):
    p = path_for(data_dir, area_id)
    if p.exists():
        return pd.read_parquet(p)
    f = build(bbox, buildings, **kw)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not f.attrs.get("notes"):                              # only cache complete results
        f.to_parquet(p)
    return f
