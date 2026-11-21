"""Install the USGS global Vs30 map where the app looks for it: data/rasters/vs30_mosaic_median_30c.tif

  python tools/get_vs30.py                     # downloads global_vs30.grd (610 MB) with curl and converts it (~2 min, <1 GB RAM)
  python tools/get_vs30.py /path/to/file       # install a GeoTIFF you already have (e.g. the newer Global Vs30 Mosaic,
                                               #   vs30_mosaic_median_30c.tif from https://doi.org/10.5066/P1NV6UNM) or a .grd

The automatic download is the Heath et al. (2020) hybrid global Vs30 map (topographic-slope default with regional insets, 30
arcsec, -56..84 deg latitude, public domain), https://apps.usgs.gov/shakemap_geodata/vs30/global_vs30.grd. That host is reachable
from scripts (ScienceBase, where the newer mosaic lives, answers scripts with a bot challenge). Outside -56..84 deg latitude
or over water the file has no value and the model falls back to a flat 250 m/s with a note in the run summary.
"""
import pathlib, shutil, sys
HERE = pathlib.Path(__file__).resolve().parent.parent
dest = HERE / "data" / "rasters" / "vs30_mosaic_median_30c.tif"
URL = "https://apps.usgs.gov/shakemap_geodata/vs30/global_vs30.grd"

def download(to):
    """Plain curl, resumable (-C -). Python's urllib is answered with 403 by this host, curl is not."""
    import subprocess
    to.parent.mkdir(parents=True, exist_ok=True)
    print("downloading", URL)
    subprocess.run(["curl", "-fSL", "-C", "-", "-o", str(to), URL], check=True)

def convert(src, out, rows=512):
    """Strip-wise copy to a tiled, compressed float32 GeoTIFF in EPSG:4326 (the GMT grid is 4 GB when expanded)."""
    import rasterio
    from rasterio.windows import Window
    with rasterio.open(src) as s:
        prof = s.profile.copy()
        prof.update(driver="GTiff", crs="EPSG:4326", dtype="float32", count=1, tiled=True, blockxsize=512, blockysize=512,
                    compress="deflate", predictor=3, nodata=float("nan"), BIGTIFF="YES")
        prof.pop("interleave", None)
        out.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(out, "w", **prof) as d:
            for r0 in range(0, s.height, rows):
                w = Window(0, r0, s.width, min(rows, s.height - r0))
                d.write(s.read(1, window=w), 1, window=w)
    print("installed", out, f"({out.stat().st_size / 1e6:.0f} MB)")

def main():
    if len(sys.argv) > 2:
        sys.exit(__doc__)
    if len(sys.argv) == 2:
        src = pathlib.Path(sys.argv[1])
        if not src.exists():
            sys.exit(f"{src} not found.\n{__doc__}")
    else:
        src = HERE / "data" / "external" / "vs30" / "global_vs30.grd"
        download(src)
    import rasterio
    with rasterio.open(src) as ds:                       # sanity check before installing
        assert ds.count >= 1, "no bands"
        if ds.crs is not None:
            assert ds.crs.to_epsg() == 4326, "expected a lon/lat grid (EPSG:4326)"
    if src.suffix.lower() in (".tif", ".tiff"):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dest)
        print("installed", dest)
    else:
        convert(src, dest)

if __name__ == "__main__":
    main()
