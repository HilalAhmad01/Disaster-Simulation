"""Deterministic regression: a fixed synthetic city must give the saved numbers (no download needed).
Regenerate with:  python tests/test_regression.py --update   (then explain every change in CHANGELOG.md)"""
import json, pathlib, sys
sys.path[:0] = [str(pathlib.Path(__file__).resolve().parent.parent), str(pathlib.Path(__file__).resolve().parent), str(pathlib.Path(__file__).resolve().parent.parent / "tools")]
import numpy as np, pytest
pytest.importorskip("geopandas")
from synthetic import synthetic_exposure

SAVED = pathlib.Path(__file__).resolve().parent / "baselines" / "synthetic.json"
SCEN = [dict(lat=28.575, lon=77.33, depth_km=15, mag=6.5), dict(lat=28.575, lon=77.33, depth_km=10, mag=7.5),
        dict(lat=29.02, lon=77.33, depth_km=10, mag=7.5)]

def compute():
    import os; os.environ["VS30_TIF"] = "/nonexistent.tif"
    from simulate import Model
    m = Model(synthetic_exposure())
    out = []
    for s in SCEN:
        r = m.run(s["lat"], s["lon"], s["depth_km"], s["mag"])
        L = m.last
        out.append({"summary": {k: v for k, v in r["summary"].items() if k != "soil"},
                    "pga_pct": np.percentile(L["pga"], [1, 50, 99]).round(5).tolist(),
                    "p_ext_mean": round(float(L["p_extensive"].mean()), 5),
                    "p_col_mean": round(float(L["p_collapse"].mean()), 5)})
    return out

def test_synthetic_baseline():
    if not SAVED.exists():
        pytest.skip("no saved synthetic baseline; run tests/test_regression.py --update")
    now, old = compute(), json.loads(SAVED.read_text())
    from baseline import compare
    d = compare(old, now, tol=1e-6)
    assert not d, "\n".join(d)

def test_synthetic_is_deterministic():
    a, b = synthetic_exposure(), synthetic_exposure()
    assert (a.levels == b.levels).all() and a.geometry.equals(b.geometry) and len(a) == 1000

if __name__ == "__main__":
    if "--update" in sys.argv:
        SAVED.write_text(json.dumps(compute(), indent=1, sort_keys=True)); print("saved", SAVED)
