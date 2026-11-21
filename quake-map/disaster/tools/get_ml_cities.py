"""Download training data for the building-attribute model (MASTER_PLAN 1.5): Overture footprints with their (sparse) labels and
Google 2.5D heights for several Indian cities, so the model is validated on whole cities it has not seen.

  python tools/get_ml_cities.py [--cities delhi mumbai ...] [--years 2021 2022 2023]

Writes data/ml/<city>/raw.parquet and h<year>_<epsg>.tif (the height rasters use the fast 'overview' read; see docs/validation.md
for how close that is to the full-resolution read). Skips what already exists, so it can be re-run after an interruption.
"""
import argparse, pathlib, sys, time
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE / "tools"))

# (west, south, east, north): ~20 km boxes over each city's dense core
CITIES = {"delhi": (77.10, 28.52, 77.30, 28.72), "mumbai": (72.80, 18.95, 72.98, 19.15), "bengaluru": (77.50, 12.90, 77.70, 13.10),
          "ahmedabad": (72.50, 22.95, 72.70, 23.15), "chennai": (80.15, 12.95, 80.35, 13.15), "hyderabad": (78.35, 17.35, 78.55, 17.55),
          "kolkata": (88.25, 22.45, 88.45, 22.65), "pune": (73.75, 18.45, 73.95, 18.65), "noida": (77.30, 28.42, 77.55, 28.65)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cities", nargs="+", default=list(CITIES)); ap.add_argument("--years", nargs="+", type=int, default=[2021, 2022, 2023])
    a = ap.parse_args()
    import rasterio
    from overture import fetch_buildings, latest_release
    import get_google_heights as G
    rel = latest_release()
    cache = HERE / "data" / "rasters" / "google_heights" / "_manifests"
    for c in a.cities:
        d = HERE / "data" / "ml" / c
        d.mkdir(parents=True, exist_ok=True)
        bbox = CITIES[c]
        if not (d / "raw.parquet").exists():
            t = time.time()
            raw = fetch_buildings(bbox, release=rel)
            raw.to_parquet(d / "raw.parquet")
            (d / "release.txt").write_text(str(rel))
            print(f"{c}: {len(raw):,} buildings ({time.time() - t:.0f}s)", flush=True)
        for epsg in G.utm_epsgs(bbox):
            for y in a.years:
                p = d / f"h{y}_{epsg}.tif"
                if p.exists():
                    continue
                arr, tf = G.read_mosaic(bbox, epsg, y, 2.0, "overview", cache, log=lambda *x: None)
                if not (arr != G.NODATA).any():
                    continue
                with rasterio.open(p, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1, dtype="float32", crs=f"EPSG:{epsg}",
                                   transform=tf, nodata=G.NODATA, compress="deflate", predictor=3, tiled=True, blockxsize=512, blockysize=512) as ds:
                    ds.write(arr, 1)
                print(f"{c}: height {y} EPSG:{epsg} done", flush=True)

if __name__ == "__main__":
    main()
