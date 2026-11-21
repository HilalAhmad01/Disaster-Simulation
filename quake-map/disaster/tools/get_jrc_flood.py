"""Fetch a return-period river-flood depth map for a bounding box from the JRC / Copernicus EMS global flood hazard maps
(GloFAS v2.x, 3 arc-second, tiles of 10 x 10 degrees) and install it as a flood scenario the UI can pick.

  python tools/get_jrc_flood.py --bbox 76.8,28.3,77.6,28.95 --rp 100 --name delhi
      -> data/hazards/flood/jrc_rp100_delhi.tif   (water depth in metres, cropped, EPSG:4326)

Return periods available: 10, 20, 50, 75, 100, 200, 500 years. Tiles (~45 MB each) are cached in data/external/jrc_flood/.
Source: https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/CEMS-GLOFAS/flood_hazard/ (open, CC BY 4.0 as stated in the README there).
Caveats from the dataset's own README: the maps are NOT official hazard maps, cover river inundation only (no pluvial or coastal
flooding), and very large depths in small channels or at tile edges can be artefacts. A building in the file's flooded cells gets
the full water depth of that cell; no defences, drainage or flood-protection levels are modelled.
"""
import argparse, json, pathlib, subprocess, sys, urllib.request
HERE = pathlib.Path(__file__).resolve().parent.parent
BASE = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/CEMS-GLOFAS/flood_hazard"
RPS = (10, 20, 50, 75, 100, 200, 500)

def tiles_for(bbox):
    w, s, e, n = bbox
    ext = json.load(urllib.request.urlopen(f"{BASE}/tile_extents.geojson", timeout=60))
    out = []
    for f in ext["features"]:
        xs = [p[0] for p in f["geometry"]["coordinates"][0]]; ys = [p[1] for p in f["geometry"]["coordinates"][0]]
        if min(xs) < e and max(xs) > w and min(ys) < n and max(ys) > s:
            out.append((f["properties"]["id"], f["properties"]["name"]))
    return out

def fetch(tile, rp, cache):
    i, name = tile
    fn = f"ID{i}_{name}_RP{rp}_depth.tif"
    dest = cache / fn
    cache.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        print("downloading", fn)
        subprocess.run(["curl", "-fSL", "-C", "-", "-o", str(dest), f"{BASE}/RP{rp}/{fn}"], check=True)    # plain curl; resumable
    return dest

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--bbox", required=True, help="west,south,east,north in degrees")
    ap.add_argument("--rp", type=int, default=100, choices=RPS)
    ap.add_argument("--name", required=True)
    a = ap.parse_args(argv)
    bbox = [float(v) for v in a.bbox.split(",")]
    import rasterio
    from rasterio.merge import merge
    paths = [fetch(t, a.rp, HERE / "data" / "external" / "jrc_flood") for t in tiles_for(bbox)]
    if not paths:
        sys.exit("no JRC tile covers that box (the maps cover land between about 60 S and 80 N)")
    srcs = [rasterio.open(p) for p in paths]
    arr, tf = merge(srcs, bounds=tuple(bbox), nodata=srcs[0].nodata)
    prof = srcs[0].profile.copy()
    prof.update(driver="GTiff", height=arr.shape[1], width=arr.shape[2], transform=tf, compress="deflate", tiled=False, count=1)
    for k in ("blockxsize", "blockysize"):
        prof.pop(k, None)
    out = HERE / "data" / "hazards" / "flood" / f"jrc_rp{a.rp}_{a.name}.tif"
    out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(out, "w", **prof) as d:
        d.write(arr[:1])
    wet = arr[0][(arr[0] > 0) & (arr[0] < 100)]
    print(f"wrote {out}  {arr.shape[2]}x{arr.shape[1]} px, flooded pixels {wet.size} ({wet.size / arr[0].size:.1%}), max depth {wet.max() if wet.size else 0:.1f} m")

if __name__ == "__main__":
    main()
