"""Download a large region once (e.g. overnight) and store it as a registry area; later boxes inside it are cut from
the saved raw footprints with no network. Same release rule as the app: a newer Overture release is reported, never
fetched silently unless you pass --refresh.

  python tools/prefetch_region.py --bbox 76.8,28.2,77.8,29.0 --name delhi-ncr
"""
import argparse, pathlib, sys
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import areas
from overture import fetch_buildings, latest_release

ap = argparse.ArgumentParser()
ap.add_argument("--bbox", required=True, help="west,south,east,north")
ap.add_argument("--name", required=True)
ap.add_argument("--refresh", action="store_true")
a = ap.parse_args()
bbox = tuple(float(x) for x in a.bbox.split(","))
assert len(bbox) == 4 and bbox[0] < bbox[2] and bbox[1] < bbox[3], "bbox must be west,south,east,north"
reg = areas.Registry(HERE / "data")
hit = None if a.refresh else reg.find_covering(bbox)
newest = latest_release()
if hit:
    print(f"Already saved as '{hit['id']}' (release {hit['release']}).")
    if newest and newest > hit["release"]:
        print(f"A newer release exists ({newest}). Re-run with --refresh to download it.")
    sys.exit(0)
def prog(d, t):
    print(f"\rdownloading {d}/{t} parts", end="", flush=True)
raw = fetch_buildings(bbox, release=newest, progress=prog)
print(f"\n{len(raw):,} buildings; building the exposure table (this can take a while) ...")
m = reg.create(bbox, raw, newest, aid=a.name, name=a.name)
print("saved area", m["id"], m["buildings"], "buildings")
