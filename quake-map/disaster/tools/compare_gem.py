"""Plan 3.5: compare our fragility curves with the GEM Global Vulnerability Model (Nepal and India files) and write
docs/calibration/gem_comparison.md + gem_comparison.png.

  python tools/compare_gem.py            # needs data/external/gem/{Nepal,India}/vulnerability_structural.xml (see get_nepal_calibration.py)

GEM gives mean STRUCTURAL LOSS RATIO against an intensity measure. We turn our fragility curves into a mean damage ratio,
MDR(PGA) = sum_k P(DS = k) * DR_k, with the damage ratios 0.02 / 0.10 / 0.50 / 1.00 (slight/moderate/extensive/complete; an
assumption in the spirit of Hazus repair-cost ratios, NOT taken from GEM, whose own damage-to-loss ratios differ), and compare
the two at PGA. Only GEM classes whose intensity measure is PGA are used (low-rise); mid-rise GEM classes are in SA(1.0) and are
not comparable without a spectrum. GEM is CC BY-NC-SA: the XML stays in the git-ignored data/ folder (decision D5).

Class pairing (approximate, by construction type and height in storeys):
  URM    <-> MUR+CLBRS/LWAL (clay brick masonry load-bearing wall), H:1 and H:2
  RC_LOW <-> CR/LFINF (RC infilled frame), H:1 and H:2
"""
import json, pathlib, re, sys
import numpy as np
from scipy.special import ndtr
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
DR = np.array([0.02, 0.10, 0.50, 1.00])
PAIRS = {"URM": r"^MUR\+CLBRS/LWAL/.*/H:[12]/(RES|COM)$", "RC_LOW": r"^CR/LFINF/.*/H:[12]/(RES|COM)$"}

def read_gem(path):
    t = pathlib.Path(path).read_text()
    out = {}
    for m in re.finditer(r'<vulnerabilityFunction id="([^"]+)"[^>]*>\s*<imls imt="([^"]+)"\s*>([^<]*)</imls>\s*<meanLRs>([^<]*)</meanLRs>', t):
        i, imt, x, y = m.groups()
        out[i] = (imt, np.array(x.split(), float), np.array(y.split(), float))
    return out

def mdr(pga, med, beta):
    E = ndtr((np.log(np.maximum(pga, 1e-6))[:, None] - np.log(np.asarray(med, float))[None, :]) / beta)    # P(DS >= k)
    P = np.column_stack([E[:, 0] - E[:, 1], E[:, 1] - E[:, 2], E[:, 2] - E[:, 3], E[:, 3]])
    return P @ DR

def gem_band(models, pattern, grid):
    curves = []
    for name, d in models.items():
        for i, (imt, x, y) in d.items():
            if imt == "PGA" and re.search(pattern, i):
                curves.append(np.interp(grid, x, y, left=0.0))
    c = np.array(curves)
    return (c.min(0), np.median(c, 0), c.max(0), len(c)) if len(c) else None

def main():
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    models = {c: read_gem(HERE / "data" / "external" / "gem" / c / "vulnerability_structural.xml") for c in ("Nepal", "India")}
    frag = json.loads((HERE / "priors" / "fragility.json").read_text())
    fits = {}
    for c in PAIRS:
        f = HERE / "data" / "calibration" / "nepal" / f"fit_{c}.json"
        if f.exists():
            fits[c] = json.loads(f.read_text())
    grid = np.linspace(0.02, 1.5, 75)
    fig, axs = plt.subplots(1, 2, figsize=(11, 4.2))
    lines = ["# Comparison with GEM vulnerability functions (plan 3.5)", "",
             "Mean structural damage ratio against PGA. Ours: MDR = sum P(DS=k) x (0.02, 0.10, 0.50, 1.00) (assumed ratios). GEM: "
             "`meanLRs` of the GEM Global Vulnerability Model v2026.0.0 (CC BY-NC-SA 4.0), Nepal and India files, low-rise (H:1-2) classes "
             "with PGA as intensity measure; band = min..max over the matching functions, line = median.", "",
             "| Class | GEM functions | PGA (g) | GEM MDR (min / median / max) | Ours (placeholder) | Ours (Nepal fit, NOT applied) |", "|---|---|---|---|---|---|"]
    for ax, (c, pat) in zip(axs, PAIRS.items()):
        band = gem_band(models, pat, grid)
        ours = mdr(grid, frag["pga_g_medians_slight_moderate_extensive_complete"][c], frag["beta"][c])
        ax.plot(grid, ours, "k-", label="ours: shipped placeholder")
        fitm = None
        if c in fits and "theta_slight" in fits[c] and np.isfinite(fits[c]["theta_complete"]["mle"]) and 0.1 < fits[c]["beta"]["mle"] < 2:
            th = [fits[c][k]["mle"] for k in ("theta_slight", "theta_moderate", "theta_extensive", "theta_complete")]
            fitm = mdr(grid, th, fits[c]["beta"]["mle"])
            ax.plot(grid, fitm, "r--", label="ours: Nepal survey fit (not applied)")
        if band:
            ax.fill_between(grid, band[0], band[2], color="tab:blue", alpha=0.25, label=f"GEM min..max ({band[3]} fns)")
            ax.plot(grid, band[1], color="tab:blue", label="GEM median")
        ax.set_title(c); ax.set_xlabel("PGA (g)"); ax.set_ylabel("mean damage ratio"); ax.legend(fontsize=7); ax.set_ylim(0, 1)
        if band:
            for g in (0.1, 0.2, 0.3, 0.5, 0.8, 1.2):
                k = int(np.argmin(abs(grid - g)))
                lines.append(f"| {c} | {band[3]} | {grid[k]:.2f} | {band[0][k]:.3f} / {band[1][k]:.3f} / {band[2][k]:.3f} | {ours[k]:.3f} | "
                             + (f"{fitm[k]:.3f}" if fitm is not None else "n/a (fit degenerate or missing)") + " |")
    fig.tight_layout(); out = HERE / "docs" / "calibration"; out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / "gem_comparison.png", dpi=130)
    lines += ["", "![comparison](gem_comparison.png)", ""]
    (out / "gem_comparison.md").write_text("\n".join(lines))
    print("\n".join(lines))

if __name__ == "__main__":
    main()
