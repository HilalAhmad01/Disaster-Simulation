"""Regression baseline. Runs three fixed scenarios on a fixed area and saves/compares summary numbers.

  python tools/baseline.py --update    # (re)write tests/baselines/<scenario>.json
  python tools/baseline.py --check     # run again and print every difference vs the saved files (exit 1 if any)
  python tools/baseline.py             # just print the numbers

Area: the 6 x 5 km box 77.30,28.55,77.36,28.60 (Noida), downloaded once into data/areas/baseline/.
Overture changes between releases, so the release is stored with the numbers; a different release is reported, not failed.
Every changed number must be explained in CHANGELOG.md.
"""
import argparse, json, pathlib, sys
import numpy as np
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
BOX = (77.30, 28.55, 77.36, 28.60)
SCENARIOS = {
    "a_m65_near":   dict(mag=6.5, depth_km=15, dlat=0.0, dlon=0.0),
    "b_m75":        dict(mag=7.5, depth_km=10, dlat=0.0, dlon=0.0),
    "c_m75_far50":  dict(mag=7.5, depth_km=10, dlat=0.45, dlon=0.0),     # ~50 km north of the centre
}
OUT = HERE / "tests" / "baselines"
PCT = [1, 10, 50, 90, 99]

def summarise(model, sc):
    """Run one scenario on `model`; return a JSON-friendly dict of numbers."""
    w, s, e, n = model.bounds
    lat, lon = (s + n) / 2 + sc["dlat"], (w + e) / 2 + sc["dlon"]
    res = model.run(lat, lon, sc["depth_km"], sc["mag"])
    last = model.last
    out = {"scenario": sc, "summary": res["summary"],
           "pga_pct": dict(zip(map(str, PCT), np.percentile(last["pga"], PCT).round(5).tolist()))}
    for k in ("p_slight", "p_moderate", "p_extensive", "p_collapse"):
        out[k + "_mean"] = round(float(last[k].mean()), 5)
        out[k + "_pct"] = dict(zip(map(str, PCT), np.percentile(last[k], PCT).round(5).tolist()))
    return out

def baseline_model():
    import areas, geopandas as gpd
    from simulate import Model
    from overture import fetch_buildings, latest_release
    reg = areas.Registry(HERE / "data")
    m = reg.meta("baseline")
    if m is None:
        print("Downloading the baseline area (once) ...", flush=True)
        release = latest_release()
        reg.create(BOX, fetch_buildings(BOX, release=release), release, aid="baseline", name="baseline")
    return Model(reg.load_exposure("baseline"), data_dir=HERE / "data"), reg.meta("baseline")

def compare(a, b, path="", tol=1e-6, diffs=None):
    diffs = [] if diffs is None else diffs
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                diffs.append(f"{path}/{k}: only in {'saved' if k in a else 'new'}")
            else:
                compare(a[k], b[k], f"{path}/{k}", tol, diffs)
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if abs(a - b) > tol * max(1.0, abs(a)):
            diffs.append(f"{path}: saved {a} -> new {b}")
    elif a != b:
        diffs.append(f"{path}: saved {a!r} -> new {b!r}")
    return diffs

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    model, meta = baseline_model()
    OUT.mkdir(parents=True, exist_ok=True)
    bad = 0
    for name, sc in SCENARIOS.items():
        new = summarise(model, sc)
        new["area"] = {"buildings": meta["buildings"], "release": meta["release"], "exposure_version": meta["exposure_version"]}
        f = OUT / f"{name}.json"
        if a.update:
            f.write_text(json.dumps(new, indent=1, sort_keys=True))
            print("saved", f.name)
        elif a.check:
            if not f.exists():
                print(f"{name}: no saved baseline (run --update)"); bad += 1; continue
            old = json.loads(f.read_text())
            if old["area"].get("release") != new["area"]["release"]:
                print(f"{name}: NOTE Overture release differs (saved {old['area'].get('release')}, now {new['area']['release']}); "
                      "building counts may differ for that reason alone")
            d = compare(old, new)
            print(f"{name}: {'identical' if not d else str(len(d)) + ' differences'}")
            for line in d:
                print("   ", line)
            bad += bool(d)
        else:
            print(name, json.dumps(new["summary"]))
    sys.exit(1 if bad else 0)
