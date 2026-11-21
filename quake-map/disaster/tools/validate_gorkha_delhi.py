"""Plan 2.3 check: what does the ground-motion mix predict in Delhi-NCR for the 2015 Gorkha earthquake (M7.8, ~700 km away),
compared with USGS 'Did You Feel It?' intensities reported from the same area?

  python tools/validate_gorkha_delhi.py     # needs data/calibration/nepal/gorkha/{rupture.json,dyfi_geo_10km.geojson}
                                            # (tools/get_nepal_calibration.py downloads them); writes docs/validation_gorkha_delhi.md

Distance: Rjb from the USGS finite-fault rupture polygon to each 10 km DYFI cell centre. Vs30: the installed USGS map (flat 250 m/s
if absent). The models (BA08, BSSA14, SHARMA09) are shown next to the mix because at ~700 km all of them are extrapolated
(valid to ~200-400 km); the mix's own beyond_validity flag is reported. DYFI intensities are self-reported, cells with fewer than 5
responses are dropped, and the survey over-represents people in tall buildings; PGA is converted to MMI with Worden et al. (2012).
"""
import json, pathlib, sys
import numpy as np
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import gmm, gmpe, soil
G = HERE / "data" / "calibration" / "nepal" / "gorkha"
MAG, MIN_RESP = 7.8, 5

def rjb_km(rupture, lon, lat):
    import shapely
    from shapely.geometry import shape, Point
    from pyproj import Transformer
    poly = shape(rupture["features"][0]["geometry"])
    lon0, lat0 = float(np.mean(lon)), float(np.mean(lat))
    t = Transformer.from_crs(4326, f"+proj=aeqd +lat_0={lat0} +lon_0={lon0} +units=m", always_xy=True)
    flat = shapely.force_2d(poly)
    px = shapely.transform(flat, lambda a: np.column_stack(t.transform(a[:, 0], a[:, 1])))
    x, y = t.transform(np.asarray(lon), np.asarray(lat))
    return np.array([px.distance(Point(a, b)) for a, b in zip(x, y)]) / 1000.0

def main():
    rup = json.loads((G / "rupture.json").read_text())
    dy = json.loads((G / "dyfi_geo_10km.geojson").read_text())
    lon, lat, cdi, n = [], [], [], []
    for f in dy["features"]:
        ring = np.array(f["geometry"]["coordinates"][0])
        cx, cy = ring[:, 0].mean(), ring[:, 1].mean()
        if 76.6 <= cx <= 77.8 and 28.2 <= cy <= 29.1 and f["properties"]["nresp"] >= MIN_RESP:
            lon.append(cx); lat.append(cy); cdi.append(f["properties"]["cdi"]); n.append(f["properties"]["nresp"])
    lon, lat, cdi, n = map(np.array, (lon, lat, cdi, n))
    r = rjb_km(rup, lon, lat)
    vs, src = soil.vs30_at(lat, lon)
    epi_lat, epi_lon = 28.2305, 84.7314
    region = gmm.region_for(epi_lat, epi_lon)
    mu, sig, tau, phi, info = gmm.pga(MAG, r, vs, "rs", region)
    per = {m: f(MAG, r, vs, "rs")[0] for m, f in gmm.MODELS.items()}
    mmi = lambda ln: gmpe.mmi_from_pga(np.exp(ln))
    wm = lambda a: float(np.average(a, weights=n))
    lines = ["# 2015 Gorkha earthquake in Delhi-NCR: model versus 'Did You Feel It?' (plan 2.3)", "",
             f"M{MAG} thrust, epicentre {epi_lat}N {epi_lon}E (region used: **{region}**), USGS finite-fault rupture for distance. "
             f"{len(lon)} DYFI cells of 10 km in lon 76.6-77.8, lat 28.2-29.1 with >= {MIN_RESP} responses ({int(n.sum())} responses). Vs30: {src}.", "",
             f"Rjb to the cells: {r.min():.0f}-{r.max():.0f} km (beyond the models' validity: **{info['beyond_validity']}**). "
             f"Vs30 {vs.min():.0f}-{vs.max():.0f} m/s.", "",
             "| quantity | response-weighted mean |", "|---|---|",
             f"| DYFI intensity (MMI) | {wm(cdi):.2f} (range {cdi.min():.1f}-{cdi.max():.1f}) |",
             f"| Mix: PGA (g) | {wm(np.exp(mu)):.4f} |", f"| Mix: MMI from median PGA | {wm(mmi(mu)):.2f} |",
             f"| Mix: MMI at median +1 sigma / -1 sigma (sigma_lnPGA {wm(sig):.2f}) | {wm(mmi(mu + sig)):.2f} / {wm(mmi(mu - sig)):.2f} |"]
    for m, v in per.items():
        lines.append(f"| {m} alone: PGA (g) / MMI | {wm(np.exp(v)):.4f} / {wm(mmi(v)):.2f} |")
    d = wm(mmi(mu)) - wm(cdi)
    w = gmm.weights(np.array([float(r.mean())]), region)
    verdict = ("within about one intensity unit, the usual scatter between DYFI and PGA-derived intensity at this range" if abs(d) <= 1.0
               else f"outside one intensity unit: the mix {'under' if d < 0 else 'over'}-predicts far-field shaking (see below)")
    lines += ["", f"**Difference (mix MMI - DYFI MMI) = {d:+.2f} intensity units: {verdict}.**", "",
              "Mix weights at this distance: " + ", ".join(f"{m} {float(v[0]):.2f}" for m, v in w.items()) + ". "
              "Beyond 400 km the mix is BSSA14 alone, which has an anelastic term and decays fastest; SHARMA09 has no such term and decays "
              "slowest. Selvan & Sinha (2024) also find BSSA14 under-predicting on the Indo-Gangetic Plain.", "",
              "What this does and does not affect: PGA of a few thousandths of g damages no building, so damage and casualty results for "
              "scenarios this far away are unaffected. The PGA/MMI maps for distant epicentres are not reliable; the run summary already "
              "flags `beyond_validity` above 400 km. The weights were not re-tuned to this one event (that would be fitting to a single "
              "data point); a far-field Himalayan model would be the proper fix.", "",
              "Caveats: DYFI is self-reported and biased towards people in tall buildings and towards felt events; the conversion "
              "PGA -> MMI at PGA of a few thousandths of g is the weakest part of Worden et al. (2012); all models are used far outside "
              "their stated distance range; one event only.", ""]
    out = HERE / "docs" / "validation_gorkha_delhi.md"
    out.write_text("\n".join(lines))
    print("\n".join(lines))

if __name__ == "__main__":
    main()
