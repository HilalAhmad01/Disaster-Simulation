"""Responder report: PDF (WeasyPrint, decision D6) and CSV for one simulation (MASTER_PLAN 6.3).

Everything comes from a plain dict built by build_context(), so the same content feeds both formats and tests can check it.
The methods-and-caveats section lists every placeholder or unverified input by name; it is part of the report, not an extra.
"""
import base64, csv, datetime, html, io
from places import reverse_name

TOD = {"night": "night", "day": "daytime", "commute": "evening commute"}
CAVEATS = [
    ("Fragility curves", "UNCALIBRATED placeholders (not fitted to survey data, not from HAZUS or GEM). Absolute damage numbers carry this."),
    ("Casualty rates", "UNVERIFIED placeholders in the Hazus structure; compare with the PAGER figure shown. Outdoor casualties are not modelled."),
    ("Debris blocking streets", "Placeholder constants, not calibrated; low-rise blockage is probably under-estimated."),
    ("Construction types", "Probabilities from a documented rule with an ASSUMED census prior, not measured."),
    ("Storeys", "Measured from satellite heights where the height product is usable; the rest are estimates (see the data note)."),
    ("People", "Typical-occupancy capacity scaled to GHS-POP (modelled 2025 estimate) by 1 km cells; day/night shares are approximate."),
    ("Shaking", "Weighted mix of BA08/BSSA14 (and Sharma 2009 in the Himalayan region); a rectangular Wells & Coppersmith rupture is shorter than very large real ruptures."),
    ("Validation", "Replays of Turkiye 2023 and Nepal 2015 showed weak or inconclusive ranking skill (docs/validation.md)."),
    ("Ranges", "10th to 90th percentile of a Monte Carlo that varies shaking, fragility (assumed +-25%), construction type and population (assumed +-20%)."),
]
ATTRIBUTION = ("Buildings: Overture Maps Foundation, OpenStreetMap contributors. Map: OpenStreetMap contributors. Population: GHSL GHS-POP R2023A (JRC). "
               "Heights: Google Open Buildings 2.5D Temporal. Soil: USGS Vs30 mosaic when available. Facilities and roads: OpenStreetMap, Overture. "
               "Ground-motion models: Boore & Atkinson 2008, Boore et al. 2014, Sharma et al. 2009.")

def build_context(model, area, mc=None, access=None, dispatch=None, image_png=None, names=True):
    """Collect what the report shows. model: Model after a run; area: registry meta dict; mc: uncertainty report; access: the
    responder layer's result for this run (summary dict); dispatch: list of assignments; image_png: bytes of the map image."""
    s, sc = model.summary, model.scenario
    cells = [f["properties"] | {"lon": f["geometry"]["coordinates"][0], "lat": f["geometry"]["coordinates"][1]} for f in model.cells if f["properties"]["rank"]]
    cells.sort(key=lambda p: p["rank"])
    for c in cells:
        c["name"] = (reverse_name(c["lat"], c["lon"]) if names else "") or ""
    worst = {w["rank"]: w for w in (mc or {}).get("worst_case_cells", [])}
    return {"generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), "run": model.run_id, "area": area or {}, "scenario": sc, "summary": s,
            "cells": cells, "mc": mc, "worst": list(worst.values()), "access": access, "dispatch": dispatch or [], "image": image_png,
            "data_notes": {k: s.get(k) for k in ("storeys_measured_share", "height_source", "population_source", "ghs_pop_total", "soil")}}

def _n(v):
    return f"{int(round(v)):,}" if v is not None else "-"

def _range(t):
    return f"{_n(t['p10'])} to {_n(t['p90'])} (median {_n(t['p50'])})"

def render_html(ctx):
    e = html.escape
    s, sc, mc = ctx["summary"], ctx["scenario"], ctx["mc"]
    rows = [("Buildings assessed", _n(s["buildings"])), ("Moderate damage or worse", _n(s["heavy"])), ("Severe damage or collapse risk", _n(s["severe"])),
            ("Expected collapses", _n(s["expected_collapses"])), (f"People at risk ({TOD.get(sc['time_of_day'], '')})", _n(s["people_at_risk"]))]
    rows += [(k, str(v)) for k, v in s.get("peak", [["Peak shaking (g)", s.get("max_pga")], ["Peak intensity (MMI)", s.get("max_mmi")]])]
    c = s.get("casualties")
    if c:
        rows += [("Estimated deaths (unverified rates)", _n(c["deaths"])), ("Estimated injured (all severities)", _n(c["injury_1"] + c["injury_2"] + c["injury_3"])),
                 ("USGS PAGER estimate of deaths (cross-check)", _n(c["pager_deaths"]) + (" - our figure differs a lot" if c["pager_disagreement"] else ""))]
    if mc:
        t = mc["totals"]
        rows += [("People at risk, range", _range(t["people_at_risk"])), ("Expected collapses, range", _range(t["expected_collapses"]))]
        if "deaths" in t:
            rows += [("Deaths, range", _range(t["deaths"])), ("Injured, range", _range(t["injuries"]))]
    srows = "".join(f"<tr><td>{e(k)}</td><td>{e(v)}</td></tr>" for k, v in rows)
    worst = {w["rank"]: w for w in ctx["worst"]}
    prows = "".join(f"<tr><td>{p['rank']}</td><td>{e(p['name'] or '(unnamed)')}</td><td>{_n(p['risk'])}</td><td>{_n(p['heavy'])}</td><td>{_n(p['severe'])}</td><td>{p['dist_km']}</td></tr>" for p in ctx["cells"])
    wrows = "".join(f"<tr><td>{w['rank']}</td><td>{w['lat']:.4f}, {w['lon']:.4f}</td><td>{_n(w['risk_p90'])}</td><td>{_n(w['risk_p50'])}</td></tr>" for w in ctx["worst"])
    acc = ctx["access"]
    arow = ""
    if acc:
        arow = (f"<h2>Hospital access</h2><p>{acc['share_within_30min']:.0%} of people are within 30 minutes of a working hospital; {acc['share_no_access']:.0%} have no route to one. "
                f"Typical travel time {acc['median_minutes']} min. {acc['hospitals_may_not_work']} of {acc['hospitals']} hospitals may not be working; "
                f"{acc['streets_likely_blocked']:,} of {acc['streets']:,} street segments are likely blocked by debris (constants not calibrated).</p>")
    drows = "".join(f"<tr><td>{d['order']}</td><td>{e(d['team'])} ({e(d['facility'])})</td><td>area {d['cell_rank']} ({_n(d['people_at_risk'])} at risk)</td><td>{d['travel_min']}</td><td>{d['p_route_open']:.0%}</td></tr>" for d in ctx["dispatch"])
    img = f'<img class="map" src="data:image/png;base64,{base64.b64encode(ctx["image"]).decode()}">' if ctx["image"] else ""
    dn = ctx["data_notes"]
    notes = f"<li>Storeys known for {dn['storeys_measured_share']:.0%} of buildings ({e(str(dn['height_source']))}); the rest are estimated.</li>" if dn.get("storeys_measured_share") is not None else ""
    notes += f"<li>People: {e(str(dn['population_source']))}.</li>" if dn.get("population_source") else ""
    notes += f"<li>Soil: {e(str(dn['soil']))}.</li>" if dn.get("soil") else ""
    cav = "".join(f"<li><b>{e(k)}.</b> {e(v)}</li>" for k, v in CAVEATS)
    strike = f", strike {sc['strike']}&deg;, dip {sc['dip'] if sc['dip'] is not None else 'auto'}" if sc.get("strike") is not None else ""
    hz = sc.get("hazard", "earthquake")
    if hz == "earthquake":
        scenario_html = (f"<p><b>Magnitude {sc['mag']}</b>, depth {sc['depth_km']} km, epicentre {sc['lat']:.4f}, {sc['lon']:.4f}{strike}; time of day: {TOD.get(sc['time_of_day'], '')}. "
                         f"Shaking model: {e(' + '.join(s.get('gm_models') or []))} ({e(str(s.get('distance_method', '')))}).</p>")
    else:
        extra = ", ".join(f"{e(str(k))} {e(str(v))}" for k, v in sc.items() if k not in ("hazard", "time_of_day", "lat", "lon") and v is not None)
        scenario_html = f"<p><b>{e(hz.title())} scenario</b> at {sc.get('lat', 0):.4f}, {sc.get('lon', 0):.4f}; {e(extra)}; time of day: {TOD.get(sc['time_of_day'], '')}. {e(str(s.get('hazard_note', '')))}</p>"
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Earthquake scenario report</title><style>
@page {{ size: A4; margin: 16mm 14mm; @bottom-center {{ content: "Planning estimate, not confirmed damage - page " counter(page); font: 8pt sans-serif; color:#5b6874 }} }}
body {{ font: 10pt/1.4 sans-serif; color:#1c2630 }} h1 {{ font-size: 18pt; margin: 0 0 2pt }} h2 {{ font-size: 12pt; margin: 14pt 0 4pt; border-bottom: 1px solid #cfd6db }}
table {{ border-collapse: collapse; width: 100% }} td, th {{ border-bottom: 1px solid #e3e8eb; padding: 3pt 4pt; text-align: left; vertical-align: top }} th {{ background:#f2f4f5 }}
.map {{ width: 100%; max-height: 100mm; object-fit: contain; margin: 6pt 0 }} .small {{ font-size: 8.5pt; color:#41505d }} .warn {{ background:#fdf3d6; border:1px solid #f2b705; padding:5pt 7pt; margin:6pt 0 }}
</style></head><body>
<h1>Earthquake damage scenario</h1>
<p class="small">{e(str(ctx['area'].get('name', '')))} &middot; generated {ctx['generated']} &middot; simulation {ctx['run']}</p>
{scenario_html}
<div class="warn"><b>Read the caveats on the last page.</b> Fragility curves and casualty rates in this version are uncalibrated or unverified placeholders; use the numbers to compare areas, not as predictions.</div>
{img}
<h2>Summary</h2><table>{srows}</table>
<h2>Priority neighbourhoods</h2><table><tr><th>#</th><th>Place</th><th>People at risk</th><th>Moderate or worse</th><th>Severe</th><th>km from epicentre</th></tr>{prows}</table>
{('<h2>Worst case (90th percentile) priorities</h2><table><tr><th>#</th><th>Location</th><th>People at risk, bad case</th><th>Median</th></tr>' + wrows + '</table>') if wrows else ''}
{arow}
{('<h2>Suggested dispatch order</h2><table><tr><th>#</th><th>Team</th><th>Go to</th><th>Min</th><th>Route open</th></tr>' + drows + '</table><p class="small">Teams go where they reach the most people at risk per minute. A planning suggestion, not an operational order.</p>') if drows else ''}
<h2>Methods and caveats</h2><ul>{notes}{cav}</ul><p class="small">{e(ATTRIBUTION)}</p>
</body></html>"""

def to_pdf(ctx):
    import weasyprint
    return weasyprint.HTML(string=render_html(ctx)).write_pdf()

def to_csv(ctx):
    """Priority cells (expected and worst case), access summary and dispatch, as one CSV with a 'section' column."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["section", "rank", "place", "lat", "lon", "people_at_risk", "moderate_or_worse", "severe", "people_at_risk_p10", "people_at_risk_p50", "people_at_risk_p90", "team", "facility", "travel_min", "route_open"])
    worst = {x["rank"]: x for x in ctx["worst"]}
    for p in ctx["cells"]:
        x = worst.get(p["rank"], {})
        w.writerow(["priority", p["rank"], p["name"], round(p["lat"], 5), round(p["lon"], 5), p["risk"], p["heavy"], p["severe"], "", "", "", "", "", "", ""])
    for x in ctx["worst"]:
        w.writerow(["worst_case", x["rank"], "", round(x["lat"], 5), round(x["lon"], 5), "", "", "", x["risk_p10"], x["risk_p50"], x["risk_p90"], "", "", "", ""])
    for d in ctx["dispatch"]:
        w.writerow(["dispatch", d["order"], f"area {d['cell_rank']}", "", "", d["people_at_risk"], "", "", "", "", "", d["team"], d["facility"], d["travel_min"], d["p_route_open"]])
    return out.getvalue()
