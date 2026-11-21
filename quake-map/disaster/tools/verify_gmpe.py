"""Compare our ground-motion equations with OpenQuake's. Needs `pip install openquake.engine` in a separate environment
(see tools/requirements-verify.txt) and prints the worst relative difference per model.

  python tools/verify_gmpe.py            # writes docs/gmpe_verification.md
"""
import itertools, pathlib, sys
import numpy as np
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

def oq_pga(gsim_cls, mag, rjb, vs30, rake=0.0, **extra):
    """Median PGA (g) and (total, inter, intra) sigma from OpenQuake for arrays of equal length."""
    from openquake.hazardlib.contexts import simple_cmaker
    cm = simple_cmaker([gsim_cls()], ["PGA"])
    n = len(rjb)
    ctx = cm.new_ctx(n)
    ctx.mag, ctx.rake, ctx.rjb, ctx.vs30 = mag, rake, rjb, vs30
    for k, v in extra.items():
        setattr(ctx, k, v)
    out = cm.get_mean_stds([ctx])
    return np.exp(out[0, 0, 0]), out[1, 0, 0], out[2, 0, 0], out[3, 0, 0]

def grid():
    return list(itertools.product([5.0, 6.0, 7.0, 8.0], [1, 5, 10, 20, 50, 100, 200.0], [180, 250, 400, 760, 1100.0]))

def ba08_table():
    import gmpe
    from openquake.hazardlib.gsim.boore_atkinson_2008 import BooreAtkinson2008
    rows, worst = [], 0.0
    for mech, rake in (("ss", 0.0), ("rs", 90.0), ("ns", -90.0)):
        g = grid()
        m, r, v = (np.array(x) for x in zip(*g))
        oq = oq_pga(BooreAtkinson2008, m, r, v, rake)[0]
        ours = gmpe.pga_g(m, r, v, mech)
        rel = np.abs(ours / oq - 1)
        worst = max(worst, rel.max())
        rows.append((mech, rel.max(), np.median(rel)))
    return rows, worst

if __name__ == "__main__":
    rows, worst = ba08_table()
    out = ["# BA08 PGA vs OpenQuake", "", "Grid: M 5-8, Rjb 1-200 km, Vs30 180-1100 m/s (60 x 3 mechanisms).", "",
           "| mechanism | max relative difference | median |", "|---|---|---|"]
    out += [f"| {a} | {b:.2e} | {c:.2e} |" for a, b, c in rows]
    out += ["", f"Worst case {worst:.2e}. 'unspecified' has no OpenQuake counterpart; see tests/test_gmpe_openquake.py."]
    (HERE / "docs").mkdir(exist_ok=True)
    (HERE / "docs" / "gmpe_verification.md").write_text("\n".join(out) + "\n")
    print("\n".join(out))
