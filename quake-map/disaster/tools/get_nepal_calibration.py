"""Fetch everything for the Nepal 2015 fragility calibration and its comparisons, then prepare the inputs.

  python tools/get_nepal_calibration.py          # downloads (~300 MB), then run tools/prepare_nepal_survey.py

Downloads (all with plain curl; resumable; Python's urllib is refused by some of these hosts):
 * USGS ShakeMap for Gorkha (us20002926): grid.xml (PGA %g, MMI), uncertainty.xml, plus the DYFI 10 km intensities and the
   finite-fault rupture (used by validate_gorkha_delhi.py)                          -> data/calibration/nepal/, .../gorkha/
 * Building table of the Nepal 2015 Household Registration for Housing Reconstruction survey, 762,106 buildings in the 11 census
   districts (district/VDC/ward ids, floors, materials, damage grade, condition after the earthquake). OPEN MIRROR of the derived
   table (csv_building_structure.csv, from the Nepal Open Data Portal); the official source is the NSO microdata catalog 69
   (registration needed): https://microdata.nsonepal.gov.np/index.php/catalog/69. Terms of the derived data: non-commercial research
   and education, attribution to the Government of Nepal (NPC / CBS) and Kathmandu Living Labs. Check that mirror's integrity against
   the official file if you need more than research use.                              -> data/external/nepal_raw/
 * HRRP federal-structure ward boundaries (HDX, CC0): "Ward_Boundary_31_Districts"   -> data/external/nepal_wards/
 * GEM Global Vulnerability Model, Nepal and India structural files (GitHub gem/global_vulnerability_model; CC BY-NC-SA 4.0,
   decision D5: non-commercial only, kept in data/ and never copied into the repository) -> data/external/gem/

Not available by script: the Anbazhagan & Bajaj (2023) chapter (paywalled), and the official survey itself (registration).
"""
import json, pathlib, shutil, subprocess, sys, urllib.request, zipfile
HERE = pathlib.Path(__file__).resolve().parent.parent
D = HERE / "data"

def curl(url, dest):
    dest = pathlib.Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        print("have", dest)
        return dest
    print("downloading", url)
    subprocess.run(["curl", "-fSL", "-C", "-", "-o", str(dest), url], check=True)
    return dest

def shakemap():
    out = D / "calibration" / "nepal"
    d = json.load(urllib.request.urlopen("https://earthquake.usgs.gov/fdsnws/event/1/query?eventid=us20002926&format=geojson", timeout=60))
    pr = d["properties"]["products"]
    c = pr["shakemap"][0]["contents"]
    for name in ("grid.xml", "uncertainty.xml"):
        key = next(k for k in c if k.endswith("/" + name))
        curl(c[key]["url"], out / name)
    curl(c["download/rupture.json"]["url"], out / "gorkha" / "rupture.json")
    curl(pr["dyfi"][0]["contents"]["dyfi_geo_10km.geojson"]["url"], out / "gorkha" / "dyfi_geo_10km.geojson")

def survey_and_wards():
    z = curl("https://github.com/bh555/sc1015_z137_grp6/raw/main/csv_building_structure.zip", D / "external" / "nepal_raw" / "csv_building_structure.zip")
    with zipfile.ZipFile(z) as f:
        f.extract("csv_building_structure.csv", D / "external" / "nepal_raw" / "extracted")
    meta = json.load(urllib.request.urlopen("https://data.humdata.org/api/3/action/package_show?id=administrative-boundaries-of-nepal", timeout=60))
    res = next(r for r in meta["result"]["resources"] if r["name"].startswith("Ward_Boundary_31"))
    rar = curl(res["url"], D / "external" / "nepal_wards" / "ward_boundary_31_districts.rar")
    out = D / "external" / "nepal_wards" / "ward"
    out.mkdir(exist_ok=True)
    if not list(out.glob("*.shp")):
        tool = shutil.which("bsdtar") or shutil.which("unrar") or shutil.which("7z")
        if not tool:
            sys.exit(f"need bsdtar, unrar or 7z to unpack {rar} into {out}")
        subprocess.run([tool, "-xf", str(rar), "-C", str(out)] if "bsdtar" in tool else [tool, "x", "-y", str(rar), str(out)], check=True)

def gem():
    base = "https://raw.githubusercontent.com/gem/global_vulnerability_model/main"
    for c in ("India", "Nepal"):
        for f in ("structural", "fatalities"):
            curl(f"{base}/South_Asia/{c}/vulnerability_{f}.xml", D / "external" / "gem" / c / f"vulnerability_{f}.xml")
    curl(f"{base}/LICENSE.txt", D / "external" / "gem" / "LICENSE.txt")

if __name__ == "__main__":
    shakemap(); survey_and_wards(); gem()
    print("done. Next: python tools/prepare_nepal_survey.py, then tools/calibrate_fragility.py (see its docstring), tools/compare_gem.py,"
          " tools/validate_gorkha_delhi.py")
