"""Static vector tiles of an area's buildings (MASTER_PLAN 6.1).

Built once per area with tippecanoe into a PMTiles file (data/tiles/<area>.pmtiles): geometry, feature id (= row in the exposure
table) and storeys only. Nothing run-specific is in the tiles, so they never need rebuilding for a new simulation and the browser
caches them. The per-run damage class and collapse chance travel separately as two digit strings indexed by feature id
(`run_attrs`), which the page puts into the layers' style expressions, so a new run changes one paint property rather than
setting 483k feature states.

Settings keep the picture identical to the GeoJSON path: extent 8192 (MapLibre's GeoJSON extent), no line simplification, no
tiny-polygon reduction, no feature or size limits (nothing is dropped), zooms 13-16 (the page over-zooms beyond 16).
"""
import gzip, json, pathlib, subprocess, shutil
import numpy as np

MINZ, MAXZ = 13, 16
LAYER = "bld"

def tippecanoe_path():
    here = pathlib.Path(__file__).resolve().parent.parent.parent / ".tools" / "tippecanoe" / "tippecanoe"
    for p in (shutil.which("tippecanoe"), str(here)):
        if p and pathlib.Path(p).exists():
            return p
    return None

def tile_path(data_dir, area_id):
    return pathlib.Path(data_dir) / "tiles" / f"{area_id}.pmtiles"

def version_of(g):
    """Deterministic stamp that changes when the footprints or storeys change (what the tiles contain)."""
    import shapely
    c = shapely.get_coordinates(np.asarray(g.geometry.values[:: max(1, len(g) // 500)], dtype=object))
    return f"{len(g)}-{int(g['levels'].sum())}-{round(float(c.sum()), 5)}"

def build(g, out, log=print):
    """Write tiles for exposure frame g (EPSG:4326, columns geometry, levels). Returns the output path."""
    import shapely
    exe = tippecanoe_path()
    if exe is None:
        raise RuntimeError("tippecanoe is not installed (see tools/install_tippecanoe.sh)")
    out = pathlib.Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    src = out.with_suffix(".geojsonl")
    geoms = shapely.to_geojson(np.asarray(g.geometry.values, dtype=object))
    lv = g["levels"].to_numpy(int)
    with open(src, "w") as f:
        for i, (gj, l) in enumerate(zip(geoms, lv)):
            f.write('{"type":"Feature","id":%d,"properties":{"levels":%d},"geometry":%s}\n' % (i, l, gj))
    log("Cutting vector tiles…")
    tmp = out.with_suffix(".tmp.pmtiles")
    cmd = [exe, "-o", str(tmp), "-l", LAYER, f"-Z{MINZ}", f"-z{MAXZ}", "--full-detail=13", "--no-line-simplification",
           "--no-tiny-polygon-reduction", "--no-feature-limit", "--no-tile-size-limit", "--no-tile-stats", "--read-parallel", "--force", str(src)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    src.unlink(missing_ok=True)
    if r.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("tippecanoe failed: " + r.stderr[-300:])
    tmp.replace(out)
    out.with_suffix(".json").write_text(json.dumps({"version": version_of(g), "buildings": len(g), "minzoom": MINZ, "maxzoom": MAXZ}))
    return out

def meta(path):
    f = pathlib.Path(path).with_suffix(".json")
    return json.loads(f.read_text()) if f.exists() and pathlib.Path(path).exists() else None

_readers = {}
def read_tile(path, z, x, y):
    """Raw (gzip-compressed) tile bytes, or None. Readers are cached per file."""
    from pmtiles.reader import Reader, MmapSource
    key = (str(path), pathlib.Path(path).stat().st_mtime_ns)
    if key not in _readers:
        for k in [k for k in _readers if k[0] == str(path)]:
            _readers.pop(k)[1].close()
        fh = open(path, "rb")
        _readers[key] = (Reader(MmapSource(fh)), fh)
    return _readers[key][0].get(z, x, y)

def run_attrs(last):
    """Per-run attributes for all buildings as two strings indexed by feature id: cat (one digit 0-3, the colour class) and pc
    (three digits 000-100, collapse chance in percent, the same two decimals the GeoJSON path sends)."""
    cat = "".join(map(str, np.asarray(last["cat"]).astype(int).tolist()))
    pc = np.clip(np.rint(np.asarray(last["p_collapse"], float) * 100), 0, 100).astype(int)
    return {"n": int(len(pc)), "cat": cat, "pc": "".join("%03d" % v for v in pc.tolist())}
