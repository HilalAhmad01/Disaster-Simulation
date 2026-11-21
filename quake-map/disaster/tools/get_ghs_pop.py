"""Cache GHS-POP (GHSL R2023A, 3 arc-second ~100 m, epoch 2025, EPSG:4326) for an area.

  python tools/get_ghs_pop.py --area baseline [--epoch 2025]

Reads only the area's window from the public JRC AWS bucket (s3://jrc-ghsl/ghs-pop/r2023a/4326/3asec/<epoch>/) and writes
data/rasters/ghs_pop/<area_id>/pop<epoch>.tif (float32 people per pixel). Licence: CC BY 4.0 (JRC GHSL).
Note: the 'E2025' layer is a modelled projection/estimate, not a census count; see docs/data_sources.md.
"""
import argparse, pathlib, sys
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", required=True)
    ap.add_argument("--epoch", type=int, default=2025)
    ap.add_argument("--pad", type=float, default=0.02)
    a = ap.parse_args()
    import areas, rasterio
    from rasterio.windows import from_bounds
    meta = areas.Registry(HERE / "data").meta(a.area)
    if meta is None:
        sys.exit(f"Unknown area {a.area}.")
    w, s, e, n = meta["bbox"]
    url = f"/vsicurl/https://jrc-ghsl.s3.amazonaws.com/ghs-pop/r2023a/4326/3asec/{a.epoch}/GHS_POP_E{a.epoch}_GLOBE_R2023A_4326_3ss_V1_0.tif"
    out = HERE / "data" / "rasters" / "ghs_pop" / a.area
    out.mkdir(parents=True, exist_ok=True)
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", GDAL_HTTP_MAX_RETRY="4"):
        with rasterio.open(url) as ds:
            win = from_bounds(w - a.pad, s - a.pad, e + a.pad, n + a.pad, ds.transform).round_offsets().round_lengths()
            arr = ds.read(1, window=win).astype("float32")
            tf = ds.window_transform(win)
    arr[~(arr >= 0)] = 0
    p = out / f"pop{a.epoch}.tif"
    with rasterio.open(p, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1, dtype="float32",
                       crs="EPSG:4326", transform=tf, compress="deflate") as dst:
        dst.write(arr, 1)
    print("wrote", p, f"people in window: {arr.sum():,.0f}")

if __name__ == "__main__":
    main()
