"""Road network and the chance debris blocks each road (MASTER_PLAN 5.2-5.3).

RoadNet holds plain arrays (so it can be built from OSMnx or by hand in tests):
  node_xy (n, 2) metres in the area's UTM CRS, edges as directed arrays edge_u/edge_v, edge_t (free-flow seconds),
  edge_len (m), edge_geom (shapely lines, UTM), edge_width (m), road_id (both directions of one street share an id).

Blockage model (form from the plan; constants are PLACEHOLDERS to be calibrated against Yu & Gardoni 2022, Applied Sciences 2020
and ISPRS 2018, see docs/roads.md). For street e of width W and building i with height H_i = levels x storey height:
  debris reach       D_i = REACH * H_i
  setback            s_i = max(distance(footprint, street centreline) - W/2, 0)
  P(i blocks e)      = P_collapse_i * [D_i >= s_i + F*W]  +  G * P_extensive_i * [D_i/2 >= s_i + F*W]
  P(e blocked)       = 1 - prod_i (1 - P(i blocks e))        (in log space, vectorised over street-building pairs)
"""
import pathlib
import numpy as np, pandas as pd

REACH, F_BLOCK, G_PARTIAL = 0.5, 0.6, 0.1      # debris reach / building height, share of width that must be covered, partial-debris factor
SEARCH_M = 30.0                                # buildings farther than this from a street cannot block it
STOREY_M = 3.0                                 # height per storey for debris (not the satellite-height calibration)
LAMBDA = 20.0                                  # expected-cost penalty: t * (1 + LAMBDA * P(blocked))

class RoadNet:
    def __init__(self, node_xy, edge_u, edge_v, edge_t, edge_len, edge_geom, edge_width, road_id=None, node_ll=None, utm=None):
        self.node_xy = np.asarray(node_xy, float)
        self.u, self.v = np.asarray(edge_u, int), np.asarray(edge_v, int)
        self.t, self.len = np.maximum(np.asarray(edge_t, float), 0.1), np.asarray(edge_len, float)
        self.geom, self.width = np.asarray(edge_geom, dtype=object), np.asarray(edge_width, float)
        if road_id is None:
            lo, hi = np.minimum(self.u, self.v), np.maximum(self.u, self.v)
            road_id = pd.factorize(pd.Series(list(zip(lo, hi, np.round(self.len, 0)))))[0]
        self.road_id = np.asarray(road_id, int)
        self.node_ll, self.utm = node_ll, utm

    @property
    def n_nodes(self): return len(self.node_xy)
    @property
    def n_edges(self): return len(self.u)
    @property
    def n_roads(self): return int(self.road_id.max()) + 1 if len(self.road_id) else 0

def _table(name, key="highway", val=None, data_dir=None):
    import priors
    df = pd.read_csv(priors.path(name, data_dir)).set_index(key)
    return df.iloc[:, 0].to_dict()

def road_class(h):
    """OSM 'highway' value (may be a list) -> base class without '_link'."""
    if isinstance(h, (list, tuple)):
        h = h[0]
    return str(h).replace("_link", "")

def widths_for(highway, osm_width=None, lanes=None, data_dir=None):
    """Street width in metres: OSM width tag > lanes x 3.5 m > default per road class (priors/street_width.csv)."""
    tab = _table("street_width.csv", data_dir=data_dir)
    out = np.array([tab.get(road_class(h), tab["default"]) for h in highway], float)
    ln = pd.to_numeric(pd.Series(lanes if lanes is not None else [np.nan] * len(out)).astype(object).apply(lambda x: x[0] if isinstance(x, list) else x), errors="coerce").to_numpy()
    out = np.where(np.isfinite(ln) & (ln > 0), ln * 3.5, out)
    w = pd.to_numeric(pd.Series(osm_width if osm_width is not None else [np.nan] * len(out)).astype(object).apply(lambda x: x[0] if isinstance(x, list) else x).astype(str).str.extract(r"([\d.]+)")[0], errors="coerce").to_numpy()
    return np.where(np.isfinite(w) & (w > 1), w, out)

def from_osmnx(G, data_dir=None):
    """Build a RoadNet from an OSMnx MultiDiGraph (drive network). Speeds default per road class where OSM has no maxspeed."""
    import geopandas as gpd, shapely
    from pyproj import Transformer
    nodes, edges = None, None
    import osmnx as ox
    nodes, edges = ox.graph_to_gdfs(G)
    utm = nodes.estimate_utm_crs()
    tr = Transformer.from_crs(4326, utm, always_xy=True)
    nx_, ny_ = tr.transform(nodes.x.to_numpy(), nodes.y.to_numpy())
    index = {n: i for i, n in enumerate(nodes.index)}
    e = edges.reset_index()
    spd = _table("road_speeds.csv", data_dir=data_dir)
    ms = pd.to_numeric(e.get("maxspeed", pd.Series([np.nan] * len(e))).astype(object).apply(lambda x: x[0] if isinstance(x, list) else x).astype(str).str.extract(r"([\d.]+)")[0], errors="coerce")
    kph = np.where(ms.notna() & (ms > 0), ms, [spd.get(road_class(h), spd["default"]) for h in e["highway"]])
    length = e["length"].to_numpy(float)
    geoms = gpd.GeoSeries(e.geometry.values, crs=4326).to_crs(utm).values
    w = widths_for(e["highway"], e["width"] if "width" in e else None, e["lanes"] if "lanes" in e else None, data_dir)
    return RoadNet(np.column_stack([nx_, ny_]), [index[a] for a in e["u"]], [index[b] for b in e["v"]], length / (kph / 3.6), length, geoms, w,
                   node_ll=np.column_stack([nodes.x.to_numpy(), nodes.y.to_numpy()]), utm=utm)

def graph_path(data_dir, area_id):
    return pathlib.Path(data_dir) / "roads" / f"{area_id}.graphml"

def load_or_download(data_dir, area_id, bbox, pad_km=2.0, log=print):
    """Drive network for an area bbox padded by pad_km, cached as data/roads/<area>.graphml. Needs network the first time."""
    import osmnx as ox
    p = graph_path(data_dir, area_id)
    if p.exists():
        return ox.load_graphml(p)
    w, s, e, n = bbox
    dlat, dlon = pad_km / 111.0, pad_km / (111.0 * np.cos(np.radians((s + n) / 2)))
    ox.settings.requests_timeout = 240
    log("Downloading the road network from OpenStreetMap…")
    G = ox.graph_from_bbox(bbox=(w - dlon, s - dlat, e + dlon, n + dlat), network_type="drive", simplify=True)
    p.parent.mkdir(parents=True, exist_ok=True)
    ox.save_graphml(G, p)
    return G

def blockage(net, b_geom, b_height_m, p_collapse, p_extensive, reach=REACH, f=F_BLOCK, g=G_PARTIAL, search=SEARCH_M):
    """P(each directed edge is blocked by debris). b_geom: building footprints in the net's UTM CRS (shapely, array);
    b_height_m, p_collapse, p_extensive: per building. Streets with no building within `search` metres get 0."""
    import shapely
    from shapely import STRtree
    b_geom = np.asarray(b_geom, dtype=object)
    tree = STRtree(b_geom)
    eb, bi = tree.query(shapely.buffer(net.geom, search), predicate="intersects")        # pairs: edge index, building index
    if len(eb) == 0:
        return np.zeros(net.n_edges)
    dist = shapely.distance(net.geom[eb], b_geom[bi])
    W = net.width[eb]
    setback = np.maximum(dist - W / 2.0, 0.0)
    D = reach * np.asarray(b_height_m)[bi]
    need = setback + f * W
    p_i = np.asarray(p_collapse)[bi] * (D >= need) + g * np.asarray(p_extensive)[bi] * (D / 2.0 >= need)
    p_i = np.clip(p_i, 0.0, 1.0 - 1e-12)
    logq = np.zeros(net.n_edges)
    np.add.at(logq, eb, np.log1p(-p_i))
    p_edge = 1.0 - np.exp(logq)
    # both directions of a street are one street: take the larger value (they only differ by search ties)
    rid = net.road_id
    mx = pd.Series(p_edge).groupby(rid).transform("max").to_numpy()
    return mx
