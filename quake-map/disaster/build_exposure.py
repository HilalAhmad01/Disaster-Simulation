"""Command-line way to build data/buildings.geojson from a footprint file (the UI can also load areas for you).
Usage:
  python build_exposure.py data/noida.parquet               # any file geopandas can read
  python build_exposure.py "Noida, Uttar Pradesh, India"    # Overpass fallback (often busy)
"""
import sys, time, pathlib
import numpy as np, pandas as pd, geopandas as gpd
from exposure import make_exposure

arg = sys.argv[1] if len(sys.argv) > 1 else "Noida, Uttar Pradesh, India"
here = pathlib.Path(__file__).resolve().parent
out = (here.parent if here.name == "backend" else here) / "data"
out.mkdir(exist_ok=True)

def load_overpass(place):
    import osmnx as ox
    from osmnx._errors import InsufficientResponseError
    from shapely.geometry import box
    mirrors = ["https://overpass-api.de/api", "https://overpass.private.coffee/api", "https://overpass.kumi.systems/api"]
    ox.settings.requests_timeout = 180
    def fetch(poly):
        wait = 5
        for i in range(8):
            ox.settings.overpass_url = mirrors[i % 3]
            try:
                return ox.features_from_polygon(poly, tags={"building": True})
            except InsufficientResponseError:
                return None
            except Exception as e:
                print(f"    try {i+1} failed: {str(e).splitlines()[0][:70]}")
                time.sleep(wait); wait = min(wait * 2, 60)
        sys.exit("Overpass is busy. Use a downloaded file instead (see usage).")
    city = ox.geocode_to_gdf(place).geometry.iloc[0]
    w, s, e, n = city.bounds
    parts = []
    for x in np.arange(w, e, 0.04):
        for y in np.arange(s, n, 0.04):
            t = box(x, y, x + 0.04, y + 0.04).intersection(city)
            if t.geom_type in ("Polygon", "MultiPolygon") and t.area > 0:
                r = fetch(t)
                if r is not None:
                    parts.append(r)
                time.sleep(2)
    b = gpd.GeoDataFrame(pd.concat(parts), crs=4326)
    return b[~b.index.duplicated()]

p = pathlib.Path(arg)
if p.exists():
    b = gpd.read_parquet(p) if p.suffix == ".parquet" else gpd.read_file(p)
else:
    b = load_overpass(arg)

g = make_exposure(b)
g.to_parquet(out / "buildings.parquet")
print(f"{len(g)} buildings saved to {out / 'buildings.parquet'}")
print(f"Estimated people in buildings (all): {int(g['occupants'].sum()):,}")
print(g.groupby("use")["occupants"].agg(["count", "sum"]).sort_values("sum", ascending=False).to_string())
