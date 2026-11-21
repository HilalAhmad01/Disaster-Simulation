"""Turn raw building footprints into an exposure table: use, storeys, construction type, people."""
import numpy as np, pandas as pd

METHOD_VERSION = "1"    # bump whenever the exposure method changes; cached areas are then rebuilt from raw footprints

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

def attributes(b, area):
    """b: DataFrame with default index (optional columns class/building/subtype/height/num_floors); area: footprint m2."""
    def num(*names):
        for n in names:
            if n in b:
                return pd.to_numeric(b[n], errors="coerce")
        return pd.Series(np.nan, index=b.index)

    use = pd.Series(np.nan, index=b.index, dtype=object)
    for c in [c for c in ("class", "building", "subtype") if c in b]:
        use = use.fillna(b[c].astype(str).str.lower().map(LABEL))
    apt = np.zeros(len(b), bool)
    for c in [c for c in ("class", "building") if c in b]:
        apt |= (b[c].astype(str).str.lower() == "apartments").to_numpy()
    guess = np.where(area < 20, "outbuilding", np.where(area >= 1500, "large", "residential"))
    use = use.fillna(pd.Series(guess, index=b.index))

    default = np.where(apt, 6, np.where(use.isin(["office", "retail", "hotel", "education", "medical"]), 4,
              np.where(use.isin(SINGLE_STOREY), 1, np.where(area < 150, 2, 3))))
    levels = num("building:levels", "num_floors", "floors").fillna(num("height") / 3).fillna(pd.Series(default, index=b.index))
    levels = levels.clip(1, 40).round()
    levels = levels.where(~use.isin(SINGLE_STOREY), levels.clip(upper=2))

    # heuristic construction type (placeholder; v2 replaces this with an ML model)
    btype = np.where(levels <= 2, "URM", np.where(levels <= 4, "RC_LOW", "RC_MID"))
    engineered = use.isin(ENGINEERED).to_numpy() | (area >= 1500)
    btype = np.where(engineered & (levels.to_numpy() <= 4), "RC_LOW", btype)

    occupants = (area * levels * 0.8 / use.map(DENS)).round().clip(0, 4000)
    return use, levels, btype, occupants

def make_exposure(b):
    """b: GeoDataFrame of building footprints -> GeoDataFrame(geometry, btype, levels, occupants, use)."""
    b = b.to_crs(4326) if b.crs else b.set_crs(4326)
    b = b[b.geometry.type.isin(["Polygon", "MultiPolygon"])].reset_index(drop=True)
    area = b.to_crs(b.estimate_utm_crs()).area.to_numpy()
    use, levels, btype, occ = attributes(b, area)
    g = b[["geometry"]].copy()
    g["btype"], g["levels"], g["occupants"], g["use"] = btype, levels.astype(int), occ.astype(int), use.to_numpy()
    return g
