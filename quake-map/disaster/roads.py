"""Road network and the chance debris blocks each road (MASTER_PLAN 5.2-5.3).

RoadNet holds plain arrays (so it can be built from OSMnx or by hand in tests):
  node_xy (n, 2) metres in the area's UTM CRS, edges as directed arrays edge_u/edge_v, edge_t (free-flow seconds),
  edge_len (m), edge_geom (shapely lines, UTM), edge_width (m), road_id (both directions of one street share an id).

Blockage model (MASTER_PLAN 5.3), calibrated to Anelli, Mori & Vona (2020), Applied Sciences 10(4):1289, equations 2-11:
  a street is unusable for emergency vehicles when less than FREE_MIN_M = 3.5 m of pavement stays free (their eq. 11), so debris that
  encroaches by at least  thr = max(W - 3.5, 0)  metres blocks a street of width W (any encroachment blocks one narrower than 3.5 m).
  debris spread beyond the facade (their eqs. 3-4, 7), a = footprint side, h = building height:
      masonry   w = a * (eps - 1),  eps = 1.228 + 0.07869 a/b + 0.05626 h/a   -> with a = b = sqrt(footprint area): 0.3067 a + 0.05626 h
                (the debris area is largest at the collapse limit state: use P(complete damage))
      RC frame  w = storey height (infill panels fall; reached at the life-safety state: use P(extensive or worse))
  the paper builds three debris graphs per building (eqs. 4-6, 7-9): debris all toward the street (w), toward both sides (w/2), or
  away (0). It gives no weights; this module uses 1/3 each (an assumption).
  setback s_i = max(distance(footprint, street centreline) - W/2, 0);  encroachment = w - s_i
  P(i blocks e)  = type-mixed  P(state) * (1/3 [w - s >= thr] + 1/3 [w/2 - s >= thr])     (and encroachment > 0)
  P(e blocked)   = 1 - prod_i (1 - P(i blocks e))        (log space, vectorised over street-building pairs)
Simplifications (documented in docs/roads.md): debris from the two sides of a street is not added up (the paper does add it, so this
under-states blockage on narrow streets with collapses on both sides); footprint depth towards the street is taken as sqrt(area).
"""
import pathlib
import numpy as np, pandas as pd

FREE_MIN_M = 3.5                               # pavement width that must stay free for emergency vehicles (Anelli et al. 2020, eq. 11)
EPS0, EPS_AB, EPS_HA = 1.228, 0.07869, 0.05626  # masonry debris amplification, Anelli et al. 2020 eq. 3 (a/b and h/a coefficients)
DIR_W = 1.0 / 3.0                              # weight of each of the two debris directions that reach the street (assumption; 'away' adds 0)
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

def debris_width(area_m2, height_m, masonry):
    """Debris spread beyond the facade (m) of a collapsed building: masonry from Anelli et al. (2020) eqs. 2-4 with a = b = sqrt(area),
    RC from eq. 7 (one storey height of infill panels)."""
    a = np.sqrt(np.maximum(np.asarray(area_m2, float), 1.0))
    h = np.asarray(height_m, float)
    eps = EPS0 + EPS_AB + EPS_HA * h / a
    return np.where(masonry, a * (eps - 1.0), STOREY_M)

def blockage(net, b_geom, b_height_m, p_collapse, p_extensive, p_masonry=None, search=SEARCH_M, free_min=FREE_MIN_M):
    """P(each directed edge is blocked by debris). b_geom: building footprints in the net's UTM CRS (shapely, array);
    b_height_m, p_collapse (complete damage), p_extensive (extensive damage or worse), p_masonry (share of load-bearing masonry,
    default 1): per building. Streets with no building within `search` metres get 0."""
    import shapely
    from shapely import STRtree
    b_geom = np.asarray(b_geom, dtype=object)
    n = len(b_geom)
    pm = np.ones(n) if p_masonry is None else np.clip(np.asarray(p_masonry, float), 0.0, 1.0)
    tree = STRtree(b_geom)
    eb, bi = tree.query(shapely.buffer(net.geom, search), predicate="intersects")        # pairs: edge index, building index
    if len(eb) == 0:
        return np.zeros(net.n_edges)
    dist = shapely.distance(net.geom[eb], b_geom[bi])
    W = net.width[eb]
    setback = np.maximum(dist - W / 2.0, 0.0)
    thr = np.maximum(W - free_min, 0.0)
    area, h = shapely.area(b_geom)[bi], np.asarray(b_height_m, float)[bi]
    def share(w):                                                                       # chance the debris direction reaches far enough
        def blocks(x):
            enc = x - setback
            return (enc > 0) & (enc >= thr)
        return DIR_W * blocks(w) + DIR_W * blocks(w / 2.0)
    p_mas = np.asarray(p_collapse, float)[bi] * share(debris_width(area, h, True))
    p_rc = np.asarray(p_extensive, float)[bi] * share(debris_width(area, h, False))
    p_i = np.clip(pm[bi] * p_mas + (1.0 - pm[bi]) * p_rc, 0.0, 1.0 - 1e-12)
    logq = np.zeros(net.n_edges)
    np.add.at(logq, eb, np.log1p(-p_i))
    p_edge = 1.0 - np.exp(logq)
    # both directions of a street are one street: take the larger value (they only differ by search ties)
    rid = net.road_id
    mx = pd.Series(p_edge).groupby(rid).transform("max").to_numpy()
    return mx
