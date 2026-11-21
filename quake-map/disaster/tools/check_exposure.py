"""Exposure checks (MASTER_PLAN 1.4): Google-derived storeys vs buildings that carry a num_floors label.

  python tools/check_exposure.py --area noida            # prints results and writes docs/exposure_check_<area>.md

Reports the confusion matrix, share within +/-1 storey and bias, scans the metres-per-storey value (2.8-3.5 m) and names
the one with the smallest absolute bias. Also compares the exposure's night population with the GHS-POP total.
"""
import argparse, pathlib, sys
import numpy as np, pandas as pd
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", required=True)
    a = ap.parse_args()
    import areas, heights, geopandas as gpd
    reg = areas.Registry(HERE / "data")
    hd, pop, _ = reg.raster_inputs(a.area)
    if hd is None:
        sys.exit("No Google heights for this area: run tools/get_google_heights.py first.")
    raw = gpd.read_parquet(reg.path(a.area) / "raw.parquet")
    fl = pd.to_numeric(raw.get("num_floors"), errors="coerce")
    sel = fl.notna() & (fl >= 1) & (fl <= 40)
    lab = raw[sel].reset_index(drop=True)
    floors = fl[sel].to_numpy(float)
    h = heights.building_heights(lab.geometry.values, hd)
    ok = h.h_google.notna().to_numpy() & (h.h_google.to_numpy() >= 2) & (h.h_spread.to_numpy() <= 6)
    out = [f"# Exposure check: {a.area}", "",
           f"Buildings with a `num_floors` label: {len(lab):,} (of {len(raw):,}). Google height usable (>= 2 m, spread <= 6 m): {ok.sum():,}.", ""]
    hh = h.h_google.to_numpy()
    best = None
    rows = []
    for sm in np.arange(2.8, 3.51, 0.1):
        est = np.clip(np.round(hh[ok] / sm), 1, 40)
        err = est - floors[ok]
        rows.append((round(sm, 1), float(err.mean()), float((est == floors[ok]).mean()), float((abs(err) <= 1).mean())))
    tab = pd.DataFrame(rows, columns=["storey_m", "bias_storeys", "exact", "within_1"])
    best = tab.loc[tab.bias_storeys.abs().idxmin()]
    strata = [(1, 2), (3, 4), (5, 6), (7, 40)]
    srows = []
    for sm in (2.6, 2.8, 3.0, 3.2):
        est_s = np.clip(np.round(hh / sm), 1, 40)
        srows.append({"storey_m": sm, **{f"{x}-{y} floors": float((est_s - floors)[ok & (floors >= x) & (floors <= y)].mean()) for x, y in strata}})
    med = {f"{x}-{y} floors": (int(((floors >= x) & (floors <= y) & ok).sum()), float(np.median(hh[ok & (floors >= x) & (floors <= y)]))) for x, y in strata}
    out += ["## Storey height scan", "", tab.round(3).to_markdown(index=False), "",
            "### Bias (estimated - labelled storeys) by labelled height group", "", pd.DataFrame(srows).round(2).to_markdown(index=False), "",
            "Median Google height per labelled group (n, metres): " + ", ".join(f"{k}: n={v[0]}, {v[1]:.1f} m" for k, v in med.items()) + ". "
            "The height barely rises between 3 and 6 labelled floors, so the residual bias is the height product compressing "
            "heights (low-rise read high, tall read low), not something a single storey height can remove.", "",
            f"Smallest absolute bias at **{best.storey_m} m per storey** (bias {best.bias_storeys:+.3f} storeys, "
            f"exact {best.exact:.1%}, within +/-1 {best.within_1:.1%}).", ""]
    s0 = heights.STOREY_M
    est = np.clip(np.round(hh[ok] / s0), 1, 40).astype(int)
    f = floors[ok].astype(int)
    bins = [1, 2, 3, 4, 5, 6, 8, 12, 41]
    lab_b = pd.Series(pd.cut(f, bins, right=False), name="labelled storeys"); est_b = pd.Series(pd.cut(est, bins, right=False), name=f"estimated ({s0} m/storey)")
    cm = pd.crosstab(lab_b, est_b)
    out += [f"## Confusion matrix at the configured {s0} m", "", cm.to_markdown(), "",
            f"Exact {np.mean(est == f):.1%}; within +/-1 storey {np.mean(abs(est - f) <= 1):.1%}; bias {np.mean(est - f):+.3f} storeys.", "",
            "Labelled buildings lean towards tall apartment blocks (median floors "
            f"{np.median(floors):.0f}), so these numbers describe the labelled subset, not the unlabelled majority.", ""]
    m = reg.meta(a.area)
    st = m.get("stats", {})
    if st.get("ghs_total"):
        out += ["## Population", "", f"Night population in the exposure: {st['night_total']:,.0f}; GHS-POP total for the area: {st['ghs_total']:,.0f} "
                f"({st['night_total']/st['ghs_total']-1:+.1%}). Typical-occupancy capacity (v1 method): {st['capacity_total']:,.0f} "
                f"({st['capacity_total']/st['ghs_total']:.2f}x GHS-POP).", "",
                f"1 km cells: {st['cells']} ({st['cells_flagged']} used the neighbourhood median factor)."]
    if st.get("levels_src"):
        out += ["", "## Where storeys come from", "", pd.Series(st["levels_src"]).rename("buildings").to_frame().to_markdown()]
    p = HERE / "docs" / f"exposure_check_{a.area}.md"
    p.parent.mkdir(exist_ok=True)
    p.write_text("\n".join(out) + "\n")
    print("\n".join(out))
    print("\nwrote", p)

if __name__ == "__main__":
    main()
