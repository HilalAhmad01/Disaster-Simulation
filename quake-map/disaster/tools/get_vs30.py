"""Place the USGS Global Vs30 Mosaic where the app looks for it: data/rasters/vs30_mosaic_median_30c.tif

ScienceBase (where USGS hosts the file) answers scripts with a bot challenge, so this cannot be fully automated:
  1. Open https://doi.org/10.5066/P1NV6UNM in a browser (USGS Global Vs30 Mosaic, CC0).
  2. Download the 'median' Cloud-Optimized GeoTIFF (vs30_mosaic_median_30c.tif, ~900 m cells, m/s).
  3. Run:  python tools/get_vs30.py /path/to/vs30_mosaic_median_30c.tif
Without the file the model uses a flat 250 m/s everywhere and says so in the summary.
"""
import pathlib, shutil, sys
dest = pathlib.Path(__file__).resolve().parent.parent / "data" / "rasters" / "vs30_mosaic_median_30c.tif"
if len(sys.argv) != 2:
    sys.exit(__doc__)
src = pathlib.Path(sys.argv[1])
if not src.exists():
    sys.exit(f"{src} not found.\n{__doc__}")
import rasterio
with rasterio.open(src) as ds:                       # sanity check before installing
    assert ds.count >= 1 and ds.crs and ds.crs.to_epsg() == 4326, "expected a EPSG:4326 GeoTIFF"
dest.parent.mkdir(parents=True, exist_ok=True)
shutil.copy(src, dest)
print("installed", dest)
