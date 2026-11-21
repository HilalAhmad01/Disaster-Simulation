"""Download a large region once (e.g. overnight) and store it as a registry area; later boxes inside it are cut from
the saved raw footprints with no network. Same release rule as the app: a newer Overture release is reported, never
fetched silently unless you pass --refresh.

  python tools/prefetch_region.py --bbox 76.8,28.2,77.8,29.0 --name delhi-ncr [--no-rasters]

After the footprints it also downloads the Google building heights (tools/get_google_heights.py, --heights-method overview by default) and GHS-POP population
(tools/get_ghs_pop.py) for the area and rebuilds the exposure from them, as the app does for a normal area. Without those rasters
the area has no measured storeys and its people come from typical occupancy (far too many), so skipping them (--no-rasters) is
only sensible for a quick test. Memory: a 35 x 33 km box (1.2 million buildings, central Delhi) peaks at about 2.5 GB; a box
four times larger needs a machine with correspondingly more. Run it under `ulimit -v` if the machine is shared.
"""
import argparse, pathlib, subprocess, sys
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import areas
from overture import fetch_buildings, latest_release

ap = argparse.ArgumentParser()
ap.add_argument("--bbox", required=True, help="west,south,east,north")
ap.add_argument("--name", required=True)
ap.add_argument("--refresh", action="store_true")
ap.add_argument("--heights-method", default="overview", choices=["overview", "nearest", "average", "native"],
                help="overview (default, ~16x faster, see docs/validation.md for the measured difference) or nearest (full resolution; hours for a big region)")
ap.add_argument("--no-rasters", action="store_true", help="skip the height and population downloads (area stays low quality)")
a = ap.parse_args()
bbox = tuple(float(x) for x in a.bbox.split(","))
assert len(bbox) == 4 and bbox[0] < bbox[2] and bbox[1] < bbox[3], "bbox must be west,south,east,north"
reg = areas.Registry(HERE / "data")
hit = None if a.refresh else reg.find_covering(bbox)
newest = latest_release()

def with_rasters(aid):
    """Download heights and population for the area and rebuild its exposure (the registry sees the new rasters as a changed input)."""
    if a.no_rasters:
        return
    for tool, extra in (("get_google_heights.py", ["--method", a.heights_method]), ("get_ghs_pop.py", [])):
        print(f"\n== {tool} --area {aid} {' '.join(extra)}", flush=True)
        subprocess.run([sys.executable, "-I", str(HERE / "tools" / tool), "--area", aid, *extra], check=True)
    print("rebuilding the exposure with the rasters ...")
    g = reg.load_exposure(aid)
    m = reg.meta(aid)
    print(f"area {aid}: {len(g):,} buildings, {m['people']:,} people ({m['stats'].get('pop_src')}); storeys: {m['stats'].get('height_source')}")

if hit:
    print(f"Already saved as '{hit['id']}' (release {hit['release']}).")
    if newest and newest > hit["release"]:
        print(f"A newer release exists ({newest}). Re-run with --refresh to download it.")
    with_rasters(hit["id"])
    sys.exit(0)
def prog(d, t):
    print(f"\rdownloading {d}/{t} parts", end="", flush=True)
raw = fetch_buildings(bbox, release=newest, progress=prog)
print(f"\n{len(raw):,} buildings; building the exposure table (this can take a while) ...")
m = reg.create(bbox, raw, newest, aid=a.name, name=a.name)
print("saved area", m["id"], m["buildings"], "buildings")
with_rasters(m["id"])
