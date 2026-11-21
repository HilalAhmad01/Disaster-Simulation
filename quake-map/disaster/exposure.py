"""Turn raw building footprints into an exposure table: use, storeys, construction type, people."""
import numpy as np, pandas as pd

METHOD_VERSION = "4"    # bump whenever the exposure method changes; cached areas are then rebuilt from raw footprints

LABEL = {}
def _add(cat, *names):
    for n in names:
        LABEL[n] = cat
_add("residential", "residential", "house", "detached", "semidetached_house", "terrace", "apartments", "dormitory",
     "bungalow", "duplex", "hut", "cabin", "static_caravan")
_add("office", "office", "commercial", "civic", "public", "government", "bank", "fire_station", "police", "transportation")
_add("retail", "retail", "supermarket", "mall", "shop", "kiosk", "market")
_add("hotel", "hotel", "hostel", "guest_house")
_add("education", "education", "school", "kindergarten", "college", "university")
_add("medical", "medical", "hospital", "clinic")
_add("industrial", "industrial", "factory", "manufacture", "construction")
_add("warehouse", "warehouse", "storage", "hangar", "logistics", "depot")
_add("religious", "religious", "church", "mosque", "temple", "chapel", "shrine", "cathedral", "gurdwara")
_add("entertainment", "entertainment", "stadium", "sports_hall", "cinema")
_add("outbuilding", "outbuilding", "shed", "garage", "garages", "carport", "parking", "stable", "barn", "cowshed",
     "greenhouse", "farm", "farm_auxiliary", "agricultural", "roof", "silo", "tank", "toilets", "service")

# gross floor area (m2) per person at typical occupancy; rough planning values, tune per city
DENS = {"residential": 15, "office": 12, "retail": 10, "hotel": 30, "education": 7, "medical": 20,
        "industrial": 40, "warehouse": 150, "religious": 10, "entertainment": 8, "outbuilding": 400, "large": 100}
SINGLE_STOREY = ["industrial", "warehouse", "outbuilding", "large", "religious", "entertainment"]
ENGINEERED = ["industrial", "warehouse", "office", "retail", "hotel", "education", "medical"]

def _levels(b, area, use, apt, h=None, spread=None, storey_m=None):
    """Storeys and where they came from. Priority: label (num_floors / building:levels) > height label / storey_m >
    Google 2.5D height > today's heuristic. Google heights that are unstable (spread > 6 m) or too low (< 2 m) are not
    trusted: those buildings use the heuristic and are marked guess_unstable."""
    from heights import STOREY_M
    sm = storey_m or STOREY_M
    def num(*names):
        for n in names:
            if n in b:
                return pd.to_numeric(b[n], errors="coerce")
        return pd.Series(np.nan, index=b.index)
    default = pd.Series(np.where(apt, 6, np.where(use.isin(["office", "retail", "hotel", "education", "medical"]), 4,
              np.where(use.isin(SINGLE_STOREY), 1, np.where(area < 150, 2, 3)))), index=b.index, dtype=float)
    lab = num("building:levels", "num_floors", "floors")
    lab_h = num("height") / sm
    src = pd.Series("guess", index=b.index, dtype=object)
    levels = default.copy()
    if h is not None:
        h = pd.Series(np.asarray(h, float), index=b.index)
        sp = pd.Series(np.asarray(spread, float) if spread is not None else np.zeros(len(b)), index=b.index).fillna(0)
        has = h.notna()
        good = has & (h >= 2.0) & (sp <= 6.0)
        levels = levels.where(~good, (h / sm))
        src = src.mask(good, "google").mask(has & ~good, "guess_unstable")
    single = use.isin(SINGLE_STOREY)
    levels = levels.where(~(single & src.isin(["google", "guess", "guess_unstable"])), levels.clip(upper=2))
    levels = levels.mask(lab_h.notna(), lab_h)
    src = src.mask(lab_h.notna(), "label_height")
    levels = levels.mask(lab.notna(), lab)
    src = src.mask(lab.notna(), "label")
    return levels.clip(1, 40).round(), src

def A_storey_bin(levels):
    from priors import storeys_bin
    return storeys_bin(levels)

def attributes(b, area, heights=None, storey_m=None, mix=None):
    """b: DataFrame with default index (optional columns class/building/subtype/height/num_floors); area: footprint m2.
    heights: optional DataFrame (h_google, h_spread) aligned with b. Returns (use, levels, btype, occupants) as in v1;
    attributes_full gives the rest."""
    d = attributes_full(b, area, heights, storey_m, mix)
    return d["use"], d["levels"], d["btype"], d["capacity"]

def attributes_full(b, area, heights=None, storey_m=None, mix=None, geoms=None, utm=None, data_dir=None):
    """All per-building attributes as a dict of Series/arrays: use, levels, levels_src, p (n x 3 type probabilities),
    btype (most likely type), capacity (people at typical occupancy).
    geoms (EPSG:4326 footprints) enables the satellite-supervised storey model (attrs_ml.py): only for buildings whose storeys would
    otherwise be guesses, and only where Google heights exist (the product's coverage region). Labelled and satellite-measured storeys are
    never touched."""
    from priors import type_probs, TYPES
    use = pd.Series(np.nan, index=b.index, dtype=object)
    for c in [c for c in ("class", "building", "subtype") if c in b]:
        use = use.fillna(b[c].astype(str).str.lower().map(LABEL))
    apt = np.zeros(len(b), bool)
    for c in [c for c in ("class", "building") if c in b]:
        apt |= (b[c].astype(str).str.lower() == "apartments").to_numpy()
    guess = np.where(area < 20, "outbuilding", np.where(area >= 1500, "large", "residential"))
    use = use.fillna(pd.Series(guess, index=b.index))
    h = heights["h_google"].to_numpy() if heights is not None else None
    sp = heights["h_spread"].to_numpy() if heights is not None else None
    levels, src = _levels(b, area, use, apt, h, sp, storey_m)
    engineered = use.isin(ENGINEERED).to_numpy() | (area >= 1500)
    bin_probs = None
    if geoms is not None and heights is not None:
        import attrs_ml
        sel = np.flatnonzero(src.isin(["guess", "guess_unstable"]).to_numpy())
        P = attrs_ml.predict_storeys(geoms, sel, utm, data_dir) if len(sel) else None
        if P is not None:
            rep = np.array([1, 2, 3, 6, 10])                       # integer storeys for the most likely bin
            lv = levels.to_numpy().copy()
            lv[sel] = np.where(use.isin(SINGLE_STOREY).to_numpy()[sel], np.minimum(rep[P.argmax(1)], 2), rep[P.argmax(1)])
            levels = pd.Series(lv, index=levels.index)
            src = src.copy(); src.iloc[sel] = "satellite_model"
            bin_probs = np.eye(5)[A_storey_bin(lv)]
            bin_probs[sel] = P
    p = type_probs(levels.to_numpy(), engineered, mix, bin_probs=bin_probs)
    btype = np.array(TYPES)[p.argmax(axis=1)]
    capacity = (area * levels * 0.8 / use.map(DENS)).round().clip(0, 4000)
    return {"use": use, "levels": levels, "levels_src": src, "p": p, "btype": btype, "capacity": capacity}

def make_exposure(b, height_dir=None, pop_raster=None, data_dir=None, storey_m=None, region="IN-urban"):
    """b: GeoDataFrame of building footprints -> GeoDataFrame with columns
    use, levels, levels_src, btype (most likely), p_URM, p_RC_LOW, p_RC_MID, capacity, occupants (= night),
    occ_day, occ_commute, pop_factor. g.attrs["stats"] holds totals and data-source notes.
    height_dir: folder with Google heights (heights.py). pop_raster: GHS-POP GeoTIFF (population.py)."""
    import heights as H, population as P, priors
    b = b.to_crs(4326) if b.crs else b.set_crs(4326)
    b = b[b.geometry.type.isin(["Polygon", "MultiPolygon"])].reset_index(drop=True)
    utm = b.estimate_utm_crs()
    u = b.to_crs(utm)
    area = u.area.to_numpy()
    ht = H.building_heights(b.geometry.values, height_dir) if height_dir else None
    d = attributes_full(b, area, ht, storey_m, priors.construction_mix(region, data_dir), geoms=b.geometry.values, utm=utm, data_dir=data_dir)
    c = u.centroid
    x, y = c.x.to_numpy(), c.y.to_numpy()
    g = b[["geometry"]].copy()
    g["use"] = d["use"].to_numpy()
    g["levels"] = d["levels"].astype(int).to_numpy()
    g["levels_src"] = d["levels_src"].to_numpy()
    g["btype"] = d["btype"]
    for i, t in enumerate(priors.TYPES):
        g["p_" + t] = d["p"][:, i].astype("float32")
    g["capacity"] = d["capacity"].astype(int).to_numpy()
    pop = None
    if pop_raster:
        from pyproj import Transformer
        w, s, e, n = b.total_bounds
        pad = 0.02
        lon, lat, v = P.read_pop_points(pop_raster, (w, s, e, n), pad)
        fwd, inv = Transformer.from_crs(4326, utm, always_xy=True), Transformer.from_crs(utm, 4326, always_xy=True)
        pop = (lon, lat, v)
        occ, st = P.assign(g, x, y, pop, (w, s, e, n), (lambda xx, yy: inv.transform(xx, yy), lambda lo, la: fwd.transform(lo, la)), data_dir)
    else:
        occ, st = P.assign(g, x, y, None, data_dir=data_dir)
    g["occupants"] = occ["night"].round().astype(int).to_numpy()
    g["occ_day"] = occ["day"].round().astype(int).to_numpy()
    g["occ_commute"] = occ["commute"].round().astype(int).to_numpy()
    g["pop_factor"] = occ["factor"].astype("float32").to_numpy()
    src = g["levels_src"].value_counts()
    st["levels_src"] = {k: int(v) for k, v in src.items()}
    st["levels_model_share"] = float(src.get("satellite_model", 0) / max(len(g), 1))
    st["levels_measured_share"] = float(src.reindex(["label", "label_height", "google"]).fillna(0).sum() / max(len(g), 1))
    st["height_source"] = "Google Open Buildings 2.5D Temporal" if ht is not None else "none (storeys are guesses except labelled buildings)"
    st["storey_model"] = "satellite-supervised gradient boosting (footprint + neighbourhood) for buildings the height product cannot resolve" if st["levels_model_share"] > 0 else None
    g.attrs["stats"] = st
    return g
