"""Pick a showcase seed for the drone demo: the hidden outcome is drawn from the model's own probabilities with a seed, and some seeds put
nobody where the first sortie flies. This scores seeds by how many findable survivor sites lie in the top blocks of the initial queue.
The chosen seed is a SHOWCASE seed and is disclosed as such (docs/integration/SIMPLIFICATIONS.md).

    python tools/pick_demo_seed.py [--area baseline] [--mag 7.2] [--seeds 30] [--top 8]
"""
import argparse, pathlib, sys
import numpy as np
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ap = argparse.ArgumentParser(); ap.add_argument("--area", default="baseline"); ap.add_argument("--mag", type=float, default=7.2); ap.add_argument("--seeds", type=int, default=30); ap.add_argument("--top", type=int, default=8)
a = ap.parse_args()
from areas import Registry
from simulate import Model
from drone.config import Config
from drone.georef import AreaFrame
from drone.world import AreaIndex
from drone.truth import Truth
from drone.priority import Blocks
data = ROOT / "data"
m = Model(Registry(data).load_exposure(a.area), data_dir=data); b = m.bounds
m.run((b[1] + b[3]) / 2, (b[0] + b[2]) / 2, 10, a.mag, "night")
cfg = Config(); fr = AreaFrame((b[0] + b[2]) / 2, (b[1] + b[3]) / 2); idx = AreaIndex(m, fr, cfg); blocks = Blocks(idx, cfg)
occ = m.occ_by["night"]; pc = m.last["p_collapse"].to_numpy(float)
vals = blocks.values(pc, occ, np.zeros(idx.n))["value"]
top = [k for k in np.argsort(-vals) if blocks.active[k]][:a.top]
rows = []
for seed in range(1, a.seeds + 1):
    t = Truth(idx, m, cfg, seed)
    inblk = np.isin(blocks.inv[t.s_building], top)
    rows.append((seed, int(inblk.sum()), int(t.s_count[inblk].sum()), len(t.s_building)))
print("seed  sites in the top blocks  people  sites overall")
for r in rows: print(f"{r[0]:4d}  {r[1]:22d}  {r[2]:6d}  {r[3]:13d}")
med = np.median([r[1] for r in rows])
best = min(rows, key=lambda r: abs(r[1] - 1.3 * med) if r[1] >= 5 else 1e9)
print(f"\nmedian {med:.0f} sites in the top {a.top} blocks. Suggested showcase seed: {best[0]} ({best[1]} sites there): enough to find, not a flood.")
