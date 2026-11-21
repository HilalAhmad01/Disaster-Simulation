"""Plan 1.4: manual imagery spot-check of 50 random UNLABELLED buildings (storeys). A person has to do the looking; this tool prepares
the sheet and scores it.

  python tools/spotcheck_storeys.py make  --area noida [--n 50] [--seed 1]     -> data/spotcheck/<area>_sheet.csv + .html
  # open the .html, follow each satellite / street-level link, and type the storeys you count into the observed_storeys column of
  # the CSV (leave blank if you cannot tell). Street-level views show storeys far better than roof imagery.
  python tools/spotcheck_storeys.py score --area noida                          -> docs/spotcheck_<area>.md

Sampling: random among buildings whose storeys came from the height product or the satellite model (not from a mapped label),
stratified so every predicted storey class (1, 2, 3-4, 5+) has an equal number, so tall buildings, which are rare, are checked too.
The sheet lists the predicted storeys and where they came from, but the HTML hides nothing from the checker; to keep the check
blind, only look at the predicted column after you have entered what you see.
"""
import argparse, pathlib, sys
import numpy as np, pandas as pd
HERE = pathlib.Path(__file__).resolve().parent.parent
BINS = [(1, 1), (2, 2), (3, 4), (5, 99)]

def centroids(area):
    import geopandas as gpd
    d = pd.read_parquet(HERE / "data" / "areas" / area / "exposure.parquet")
    g = gpd.GeoSeries.from_wkb(d.geometry.values, crs=4326)
    c = g.representative_point()
    return d.assign(lon=c.x.values, lat=c.y.values)

def make(a):
    d = centroids(a.area)
    d = d[d.levels_src.isin(["google", "satellite_model"])]
    rng = np.random.default_rng(a.seed)
    per = -(-a.n // len(BINS))                      # ceil, trimmed to n below
    pick = []
    for lo, hi in BINS:
        sub = d[(d.levels >= lo) & (d.levels <= hi)]
        pick.append(sub.sample(min(per, len(sub)), random_state=int(rng.integers(1 << 31))))
    s = pd.concat(pick).sample(frac=1.0, random_state=a.seed).head(a.n).reset_index()
    out = HERE / "data" / "spotcheck"
    out.mkdir(parents=True, exist_ok=True)
    sheet = pd.DataFrame({"id": range(1, len(s) + 1), "lat": s.lat.round(6), "lon": s.lon.round(6), "use": s.use,
                          "predicted_storeys": s.levels.astype(int), "source": s.levels_src, "observed_storeys": "", "notes": ""})
    sheet.to_csv(out / f"{a.area}_sheet.csv", index=False)
    rows = "".join(
        f"<tr><td>{r.id}</td><td>{r.lat}, {r.lon}</td>"
        f"<td><a href='https://www.google.com/maps/@{r.lat},{r.lon},20z/data=!3m1!1e3' target=_blank>satellite</a></td>"
        f"<td><a href='https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={r.lat},{r.lon}' target=_blank>street level</a></td>"
        f"<td><a href='https://www.openstreetmap.org/?mlat={r.lat}&mlon={r.lon}#map=19/{r.lat}/{r.lon}' target=_blank>OSM</a></td>"
        f"<td>{r.use}</td></tr>" for r in sheet.itertuples())
    (out / f"{a.area}_sheet.html").write_text(
        f"<!doctype html><meta charset=utf-8><title>Storey spot-check, {a.area}</title><style>body{{font:14px sans-serif;margin:24px}}"
        f"td,th{{padding:4px 10px;border-bottom:1px solid #ccc}}</style><h1>Storey spot-check: {a.area}</h1>"
        f"<p>For each building open a view and count the storeys. Write the number in <code>{a.area}_sheet.csv</code>, column "
        f"<code>observed_storeys</code>. Do not look at the predicted column first.</p>"
        f"<table><tr><th>#</th><th>position</th><th colspan=3>views</th><th>use</th></tr>{rows}</table>")
    print(f"wrote {out}/{a.area}_sheet.csv and .html ({len(sheet)} buildings)")

def score(a):
    f = HERE / "data" / "spotcheck" / f"{a.area}_sheet.csv"
    s = pd.read_csv(f)
    s["observed_storeys"] = pd.to_numeric(s.observed_storeys, errors="coerce")
    d = s.dropna(subset=["observed_storeys"])
    if len(d) < 10:
        sys.exit(f"only {len(d)} rows filled in {f}; fill in at least 10")
    err = d.predicted_storeys - d.observed_storeys
    lines = [f"# Storey spot-check against imagery: {a.area}", "",
             f"{len(d)} of {len(s)} sampled buildings judged by eye (random, unlabelled, stratified by predicted storeys 1 / 2 / 3-4 / 5+).", "",
             f"* exact agreement: {(err == 0).mean():.0%}", f"* within +/-1 storey: {(err.abs() <= 1).mean():.0%}",
             f"* mean error (predicted - observed): {err.mean():+.2f} storeys; mean absolute error {err.abs().mean():.2f}", "",
             "| predicted | n | observed mean | within +/-1 |", "|---|---|---|---|"]
    for lo, hi in BINS:
        m = (d.predicted_storeys >= lo) & (d.predicted_storeys <= hi)
        if m.any():
            lines.append(f"| {lo}{'-' + str(hi) if hi != lo and hi < 99 else '+' if hi == 99 else ''} | {int(m.sum())} | {d.observed_storeys[m].mean():.1f} | {(err[m].abs() <= 1).mean():.0%} |")
    lines += ["", "By source: " + ", ".join(f"{k}: {(g.predicted_storeys - g.observed_storeys).abs().le(1).mean():.0%} within +/-1 (n={len(g)})" for k, g in d.groupby('source')), ""]
    out = HERE / "docs" / f"spotcheck_{a.area}.md"
    out.write_text("\n".join(lines))
    print("\n".join(lines))

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["make", "score"])
    ap.add_argument("--area", required=True)
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    {"make": make, "score": score}[a.cmd](a)
