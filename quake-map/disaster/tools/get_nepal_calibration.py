"""Fetch what can be fetched for the Nepal 2015 fragility calibration and print the manual steps for the rest.

  python tools/get_nepal_calibration.py

AUTOMATIC: USGS ShakeMap for the Gorkha earthquake (event us20002926): grid.xml (PGA in %g, MMI, Vs30) and uncertainty.xml
(standard deviation of ln PGA) -> data/calibration/nepal/.

MANUAL (registration-gated or unreachable from scripts; not available when this code was written):
 1. Building-level damage survey: 'Household Registration for Housing Reconstruction' (CBS/NSO with NPC; a full count of damaged
    houses). Microdata catalog 69: https://microdata.nsonepal.gov.np/index.php/catalog/69 (register, request the Building file),
    or the Kathmandu Living Labs earthquake data portal. Needed columns: district, VDC/municipality, ward ids; superstructure
    (materials); floors before the earthquake; damage grade 1-5. Check in the data dictionary: the exact column names, whether
    ward codes are pre- or post-2017, and which districts are covered (Kathmandu valley appears to be excluded).
    Do NOT use the DrivenData 'Richter's Predictor' subset: its geography ids are obfuscated and cannot be joined to shaking.
 2. Ward (or VDC) boundary polygons matching the survey's coding: HDX, search 'Nepal administrative boundaries' (pre-2017 VDCs).
 Save as data/calibration/nepal/survey.csv and wards.gpkg, then run tools/calibrate_fragility.py (its docstring has the command).
"""
import json, pathlib, sys, urllib.request
HERE = pathlib.Path(__file__).resolve().parent.parent
out = HERE / "data" / "calibration" / "nepal"
out.mkdir(parents=True, exist_ok=True)
d = json.load(urllib.request.urlopen("https://earthquake.usgs.gov/fdsnws/event/1/query?eventid=us20002926&format=geojson", timeout=60))
c = d["properties"]["products"]["shakemap"][0]["contents"]
for name in ("grid.xml", "uncertainty.xml"):
    key = next(k for k in c if k.endswith("/" + name))
    dest = out / name
    if not dest.exists():
        print("downloading", name, c[key].get("length"), "bytes")
        dest.write_bytes(urllib.request.urlopen(c[key]["url"], timeout=300).read())
    print("have", dest)
print(__doc__.split("MANUAL")[1])
