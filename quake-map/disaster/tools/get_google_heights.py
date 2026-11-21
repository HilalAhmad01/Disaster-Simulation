"""Download Google Open Buildings 2.5D Temporal building heights for an area (public GCS bucket, no account needed).

  python tools/get_google_heights.py --area baseline [--years 2021 2022 2023] [--res 2] [--method nearest]

Writes data/rasters/google_heights/<area_id>/h<year>_<epsg>.tif (float32 metres, UTM, nodata -99).
Source: gs://open-buildings-temporal-data/v1 (manifests/*.json list the 12.5 km tiles, 0.5 m pixels, 3 bands:
building_fractional_count, building_height, building_presence). Licence CC BY 4.0 / ODbL; see docs/data_sources.md.
--res    output pixel size in metres (default 2: the product's effective resolution is ~4 m, see docs/validation.md)
--method nearest | average | overview | native. nearest/average read the 0.5 m pixels (slow, ~100 s per year per 30 km2); overview reads the
         file's own 2 m pyramid level (~16x faster, see docs/validation.md for the measured difference); native keeps 0.5 m (very heavy)
"""
import argparse, json, math, pathlib, re, sys, urllib.request
import numpy as np
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
BUCKET = "open-buildings-temporal-data"
API = f"https://storage.googleapis.com/storage/v1/b/{BUCKET}/o"
NODATA = -99.0

def utm_epsgs(bbox):
    """EPSG codes of the UTM zones a lon/lat box touches."""
    w, s, e, n = bbox
    zones = range(int((w + 180) // 6) + 1, int((min(e, 179.999999) + 180) // 6) + 2)
    base = 32600 if (s + n) / 2 >= 0 else 32700
    return [base + z for z in zones]

def _json(url):
    with urllib.request.urlopen(url, timeout=120) as r:
        return json.load(r)

def list_manifests(epsg, year, cache):
    """Names of manifest files for a UTM zone and year (several s2 cells can exist per zone)."""
    f = cache / "manifest_index.json"
    idx = json.loads(f.read_text()) if f.exists() else None
    if idx is None:
        names, tok = [], None
        while True:
            d = _json(f"{API}?prefix=v1/manifests/&maxResults=1000" + (f"&pageToken={tok}" if tok else ""))
            names += [i["name"] for i in d.get("items", [])]
            tok = d.get("nextPageToken")
            if not tok:
                break
        idx = names
        cache.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(idx))
    pat = re.compile(rf"v1/manifests/\w+_EPSG_{epsg}_{year}_\d\d_\d\d\.json$")
    return [n for n in idx if pat.match(n)]

def tiles_for(utm_bounds, epsg, year, cache):
    """[(url, (x0, y0, x1, y1))] tiles of the year that intersect a UTM box."""
    hits = []
    for name in list_manifests(epsg, year, cache):
        local = cache / pathlib.Path(name).name
        if not local.exists():
            local.write_bytes(urllib.request.urlopen(f"https://storage.googleapis.com/{BUCKET}/{name}", timeout=120).read())
        m = json.loads(local.read_text())
        prefix = m["uriPrefix"].replace("gs://", "https://storage.googleapis.com/")
        for ts in m["tilesets"]:
            for s in ts["sources"]:
                a, d = s["affineTransform"], s["dimensions"]
                x0, y1 = a["translateX"], a["translateY"]
                x1, y0 = x0 + d["width"] * a["scaleX"], y1 + d["height"] * a["scaleY"]
                if x0 < utm_bounds[2] and x1 > utm_bounds[0] and y0 < utm_bounds[3] and y1 > utm_bounds[1]:
                    hits.append((prefix + s["uris"][0], (x0, y0, x1, y1)))
    return hits

def read_mosaic(bbox, epsg, year, res, method, cache, log=print):
    """Height band of all tiles over a lon/lat box as one array on a res-metre grid in the UTM zone. Returns (array, transform)."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    from rasterio.windows import from_bounds
    from pyproj import Transformer
    tr = Transformer.from_crs(4326, epsg, always_xy=True)
    w, s, e, n = bbox
    xs, ys = zip(*[tr.transform(x, y) for x in np.linspace(w, e, 5) for y in np.linspace(s, n, 5)])
    ub = (min(xs), min(ys), max(xs), max(ys))
    step = 0.5 if method == "native" else float(res)
    x0, y1 = math.floor(ub[0] / step) * step, math.ceil(ub[3] / step) * step
    W, H = int(math.ceil((ub[2] - x0) / step)), int(math.ceil((y1 - ub[1]) / step))
    out = np.full((H, W), NODATA, np.float32)
    tf = from_origin(x0, y1, step, step)
    rs = {"nearest": Resampling.nearest, "average": Resampling.average, "overview": Resampling.nearest, "native": Resampling.nearest}[method]
    tiles = tiles_for(ub, epsg, year, cache)
    log(f"  {year}: {len(tiles)} tile(s), output {W}x{H} px at {step} m")
    env = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif", GDAL_HTTP_MAX_RETRY="4", GDAL_HTTP_RETRY_DELAY="2")
    with rasterio.Env(**env):
        for url, (tx0, ty0, tx1, ty1) in tiles:
            gx0, gy0, gx1, gy1 = max(x0, tx0, ub[0]), max(ub[1], ty0), min(x0 + W * step, tx1, ub[2]), min(y1, ty1, ub[3])
            if gx1 <= gx0 or gy1 <= gy0:
                continue
            # snap to the output grid
            gx0, gx1 = x0 + math.floor((gx0 - x0) / step) * step, x0 + math.ceil((gx1 - x0) / step) * step
            gy1, gy0 = y1 - math.floor((y1 - gy1) / step) * step, y1 - math.ceil((y1 - gy0) / step) * step
            c0, r0 = int(round((gx0 - x0) / step)), int(round((y1 - gy1) / step))
            wpx, hpx = int(round((gx1 - gx0) / step)), int(round((gy1 - gy0) / step))
            with rasterio.open("/vsicurl/" + url, **({} if method == "overview" else {"OVERVIEW_LEVEL": "NONE"})) as ds:
                win = from_bounds(gx0, gy0, gx1, gy1, ds.transform)
                a = ds.read(2, window=win, out_shape=(hpx, wpx), resampling=rs, boundless=True, fill_value=NODATA)
            r1, c1 = min(r0 + hpx, H), min(c0 + wpx, W)
            blk = a[: r1 - r0, : c1 - c0]
            dst = out[r0:r1, c0:c1]
            m = (dst == NODATA) | (blk != NODATA)       # never overwrite real data with nodata
            dst[m] = blk[m]
    return out, tf

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", required=True)
    ap.add_argument("--years", type=int, nargs="+", default=[2021, 2022, 2023])
    ap.add_argument("--res", type=float, default=2.0)
    ap.add_argument("--method", choices=["nearest", "average", "overview", "native"], default="nearest")
    ap.add_argument("--pad", type=float, default=0.002, help="degrees of margin around the area")
    a = ap.parse_args()
    import areas, rasterio
    meta = areas.Registry(HERE / "data").meta(a.area)
    if meta is None:
        sys.exit(f"Unknown area {a.area}. Load it in the app first.")
    w, s, e, n = meta["bbox"]
    bbox = (w - a.pad, s - a.pad, e + a.pad, n + a.pad)
    out_dir = HERE / "data" / "rasters" / "google_heights" / a.area
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = HERE / "data" / "rasters" / "google_heights" / "_manifests"
    for epsg in utm_epsgs(bbox):
        for year in a.years:
            arr, tf = read_mosaic(bbox, epsg, year, a.res, a.method, cache)
            if not (arr != NODATA).any():
                print(f"  {year} EPSG:{epsg}: no data in this zone, skipped"); continue
            p = out_dir / f"h{year}_{epsg}.tif"
            with rasterio.open(p, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1, dtype="float32",
                               crs=f"EPSG:{epsg}", transform=tf, nodata=NODATA, compress="deflate", predictor=3,
                               tiled=True, blockxsize=512, blockysize=512) as ds:
                ds.write(arr, 1)
            print("wrote", p, f"({p.stat().st_size/1e6:.1f} MB)")

if __name__ == "__main__":
    main()
