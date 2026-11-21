# Quake Damage Map

A city-scale disaster damage estimator. You describe a scenario (an earthquake, a cyclone, or a flood map) and the app shows, on an interactive map, how hard each part of the city is hit, which buildings are likely damaged, which neighbourhoods need responders first, how many people are at risk, and (for the area's hospitals, fire stations and roads) how responders could reach them. You can zoom from the whole city down to individual buildings, in 2D or 3D.

**Status: research prototype.** The pipeline runs end to end on real data (tested on Noida, 483,358 buildings), but several inputs are **uncalibrated or unverified placeholders** (section 3.1). Use the numbers to compare areas and scenarios, not as predictions. A past-earthquake replay showed weak ranking skill (docs/validation.md).

---

## 1. Motive

When a disaster hits, first responders need to know within minutes where the worst damage is, how many people are affected, and where to send teams first. Official loss tools are usually national in scale and not building-level or easy to explore. This project builds a city-wide tool on open data (Overture/OpenStreetMap footprints, Google building heights, GHSL population, USGS soil data) and open methods, with the uncertainty and the data quality shown rather than hidden.

---

## 2. What it does

- **Load any area** (drag a box up to ~30 km across). Downloads run as background jobs with progress; areas are saved and reused without re-downloading, and a saved-area switcher lets you move between cities. A newer Overture release is offered, never fetched silently.
- **Earthquake scenarios:** epicentre, depth, magnitude, time of day (night / day / commute), and optionally strike, dip and rake for a rectangular rupture (the outline is drawn on the map).
- **Cyclone scenarios:** a straight track through a chosen point with heading, speed, pressure drop and eye radius (Holland 1980 wind field). **Flood scenarios:** upload a water-depth GeoTIFF.
- **Heat map and colour-coded buildings;** click a building for use, construction-type probabilities, storeys (and whether they were measured or estimated), shaking, chance of heavy damage and collapse (with an upper "bad case" value), people.
- **Uncertainty ranges** (earthquakes): 200 Monte Carlo runs give 10th-90th percentile ranges for people at risk, collapses, deaths and injuries, and a **worst-case priority list**.
- **Casualties by severity** with a PAGER cross-check (rates are unverified, see 3.1).
- **Top 8 priority neighbourhoods**, named from OpenStreetMap.
- **Responder layer:** hospitals, clinics, fire and police stations and shelters (greyed out when their building is likely damaged); a road network with the chance debris blocks each street; people-weighted access to the nearest working hospital; routes with the chance they are open; a suggested dispatch order.
- **3D view,** **Save map as image,** **Report (PDF)** and **Table (CSV)** exports, a **"How much to trust this"** panel, a **seismic-wave animation with warning times,** a **dark theme,** and an optional (off by default) **shake effect**.

---

## 3. How it works

```
 scenario (earthquake | cyclone | flood)  +  time of day
        |
 [1] HAZARD     earthquake: finite-fault Rjb distance (Wells & Coppersmith rupture, or the USGS ps2ff table when the
                strike is unknown) -> Boore & Atkinson 2008 / Boore et al. 2014 (+ Sharma et al. 2009 in the Himalaya),
                per-building Vs30 -> PGA -> MMI (Worden 2012).  cyclone: Holland 1980.  flood: depth raster.
        |
 [2] EXPOSURE   Overture footprints; storeys from Google Open Buildings 2.5D heights (else labels, else a guess);
                construction type as probabilities (masonry / low-rise concrete / mid-rise concrete);
                people = typical occupancy scaled to GHS-POP, with night / day / commute totals.
        |
 [3] DAMAGE     lognormal fragility curves mixed over construction types -> P(slight/moderate/extensive/complete)
        |
 [4] IMPACT     expected damage class, people at risk = occupants x P(extensive or worse), casualties (earthquake)
        |
 [5] AGGREGATE  500 m cells -> heat map + priority neighbourhoods;  Monte Carlo ranges;  roads, access, routes, dispatch
```

Colour classes: blue = little or none, green = light, yellow = moderate, red = severe / collapse risk.

### 3.1 What is solid, what is a placeholder

| Piece | Status |
|---|---|
| BA08, BSSA14 and Sharma 2009 equations | **Checked against OpenQuake** (BA08 to machine precision, others < 1%): docs/gmpe_verification.md. v1.0 had BA08's normal/reverse offsets swapped (not used by the app); fixed |
| Distance, rupture | Wells & Coppersmith 1994 rectangle (shorter than the biggest real ruptures); ps2ff table from USGS (public domain). Depth acts through Ztor: the distance given to the ground-motion model is sqrt(Rjb² + Ztor²) |
| Model weights | BA08 0.65 / BSSA14 0.35 from Selvan & Sinha (2024) log-likelihood scores; **Sharma 2009's 0.2 near the source is a judgement** |
| Soil | USGS global Vs30 map (`tools/get_vs30.py` downloads and installs it; 30 arcsec, -56 to 84 deg latitude); without it a flat 250 m/s, stated in the summary |
| Storeys | Measured for **67% of Noida** (82% in the 6 x 5 km baseline box). The 30% of footprints the height product cannot resolve (< 2 m: small, low buildings) now get a **satellite-supervised model** (unbiased on usable-height buildings, beats the old area rule in 8/9 held-out cities; docs/ml_attributes.md) - an extrapolation, not independently verified for those buildings, and reported as `modelled`, not measured. Agreement with labelled buildings: 69% within one storey (target 70% missed), bias -0.45 storeys at 2.8 m/storey |
| Construction types | Probabilities from a documented rule applied to **Census 2011 urban wall and roof shares** (India, Delhi, Uttar Pradesh rows; read from the MoHUA/NBO compendium). Census 'concrete roof' is lumped with metal sheets, so the roof share is an upper bound. Areas built earlier keep the prior they were built with until rebuilt |
| People | Scaled to GHS-POP (a modelled estimate; Noida night total within 2.2% of it; v1 gave ~1.9x). Time-of-day shares now follow Hazus 6.1 Table 12-2 where it gives a value (commercial, industrial, education, hotels); residential day/evening, retail day, medical and religious shares remain judgements |
| **Fragility curves** | **UNCALIBRATED v1 placeholders.** The fit was run on the real Nepal 2015 building table (762k buildings) and rejected: URM has no held-out skill, RC fits are degenerate. Compared with GEM: ours are far more fragile below 0.5 g (docs/calibration/README.md) |
| **Casualty rates** | **Hazus 6.1 values** per building type (tables 12-3 to 12-8, read from the manual) for US stock, mapped by judgement to Indian types (URML, C3L, C3M); outdoor casualties not modelled. PAGER India parameters (theta 11.53, beta 0.14) checked against USGS OFR 2009-1136 (an earlier draft had Indonesia's values) |
| Debris blocking | Rule and 3.5 m free-width criterion from Anelli et al. (2020); direction weights assumed, no observed blockages to validate against (docs/roads.md). Road speeds and street widths are assumptions |
| Flood damage curve | JRC Huizinga et al. (2017) Asia residential curve (a JRC return-period map is fetched with `tools/get_jrc_flood.py`). **Treat absolute flood totals with great caution**: the JRC 100-year map puts 55% of Noida's buildings in flooded cells (it floods whole river plains on a 90 m model with no defences). Wind fragility is still a placeholder |
| Monte Carlo spreads | Ground-motion spread from the models; **fragility (±25%) and population (±20%) spreads are assumptions** |
| Validation | Replays of Türkiye 2023 and Nepal 2015: weak or inconclusive (docs/validation.md) |

Occupancy by use (m² per person): residential 15, office 12, retail 10, hotel 30, school 7, medical 20, factory 40, warehouse 150, religious 10, entertainment 8, sheds 400, large unknown 100. Cap of 4,000 people per building.

---

## 4. What has been done

| Stage | Work |
|---|---|
| v1, v1.x | End-to-end pipeline, MapLibre map, heat map, popups, priorities, BA08 shaking, soil from Vs30, 3D view, area loading, PNG export, speed work (gzip, lean payloads, padded loading: 2D 2.4 s → ~0.2 s) |
| Phase 0 | git; server starts without data; area registry (cached areas, Overture's own clip rule, release check); background jobs with progress; regression baselines (`tools/baseline.py`) |
| Phase 1 | Google 2.5D heights → storeys; construction-type probabilities with mixed damage curves; GHS-POP-matched people with day/night; exposure checks and storey-height calibration |
| Phase 2 | OpenQuake verification; finite-fault distance (+ ps2ff); BA08 + BSSA14 + Sharma 2009 mix by region and distance; optional local Vs30 override |
| Phase 3 | Fragility fitting chain (grouped likelihood, shaking-uncertainty, ward bootstrap, held-out scoring) tested on synthetic data; **not run on real survey data (unavailable)**. Real replays of Türkiye 2023 and Nepal 2015 against Copernicus EMS grading |
| Phase 4 | Seeded Monte Carlo (26 s for 483k buildings), correlated within-event field, ranges, worst-case priorities |
| Phase 5 | Facilities, roads + debris blockage, access, routes, dispatch, casualties |
| Phase 6 | PDF/CSV report, confidence panel, saved-area switcher, static vector tiles (6.1) |
| Phase 7 | Hazard plug-in interface; earthquake moved with identical numbers; flood and cyclone modules |
| Phase 8 | Wave animation with warning times, dark theme, shake effect |
| Follow-up | 6.1 vector tiles (static PMTiles via tippecanoe; ~50x less data per 3D view, docs/tiles.md); 1.5 ML storeys (a label-trained model passed the plan's test but was biased, so a satellite-supervised one shipped; docs/ml_attributes.md); held-out Nepal towns on EMS gradings (no transferable skill; the real survey is unobtainable) |
| Not done | 3.5 GEM/HAZUS comparison and a real fragility calibration (need the Nepal survey and Hazus/GEM tables), true district-level held-out validation. See CHANGELOG and MASTER_PLAN.md section 13 |

---

## 5. Project layout

```
disaster/
  app.py  jobs.py  areas.py          server, background jobs, area registry
  simulate.py                        Model: run_hazard() -> damage -> impact, cells, queries
  hazards/                           base interface, earthquake, flood, cyclone
  gmpe.py  gmm.py  rupture.py        BA08, ported regional models + mix, finite-fault distance
  soil.py  exposure.py  heights.py  population.py  priors.py    building data
  calibration.py  validation.py  uncertainty.py                 fitting, replay scores, Monte Carlo
  facilities.py  roads.py  responders.py  responder_layer.py    responder layer
  casualty.py  confidence.py  report.py                         casualties, trust panel, PDF/CSV
  overture.py  places.py  build_exposure.py
  priors/                            small tables shipped with the code (fragility, casualty, census, occupancy, ps2ff ...)
  tiles.py  attrs_ml.py              vector tiles; storey model (priors/models/)
  tools/                             data downloads, baselines, verification, validation, calibration, ML training
  tests/  docs/  index.html  CHANGELOG.md
  data/                              your data (not in git): areas/, rasters/, roads/, facilities/, validation/ ...
```

---

## 6. How to make it work

### 6.1 Set up the environment

```bash
python -m venv .venv && . .venv/bin/activate     # developed and tested on Python 3.14
pip install -r requirements.txt
```
(Conda with conda-forge geopandas/rasterio also works.) WeasyPrint needs Pango/Cairo on the system for the PDF report.

OpenQuake is **not** needed to run the app, only for `tools/verify_gmpe.py` and `tests/test_gmpe_openquake.py`; see `tools/requirements-verify.txt` (a separate environment).

### 6.2 Run

```bash
uvicorn app:app --reload          # http://localhost:8000
```
A fresh clone starts with no data and says so. Click **Load a new area**, drag a box, and wait for the job. Environment variables: `QUAKE_DATA_DIR` (data folder), `DEFAULT_CENTER` ("lon,lat" for the empty map), `VS30_TIF`, `VS30_OVERRIDE_TIF`.

### 6.3 Add data for the best results (all optional; the app says what is missing)

| For | Command | Notes |
|---|---|---|
| Measured storeys | `python tools/get_google_heights.py --area <id> [--method overview]` | public Google bucket, no account; `nearest` (default) is most faithful but ~100 s per year per 30 km², `overview` ~16x faster, measured difference in docs/validation.md |
| Population | `python tools/get_ghs_pop.py --area <id>` | JRC GHSL bucket, no account |
| Soil | `python tools/get_vs30.py` | downloads the 610 MB USGS grid with curl and converts it to a compressed GeoTIFF (~30 s); a GeoTIFF you already have can be passed as an argument. ScienceBase (the newer mosaic) blocks scripts; this host does not |
| Flood map | `python tools/get_jrc_flood.py --bbox w,s,e,n --rp 100 --name delhi` | JRC/Copernicus global river flood maps (3 arc-second tiles, ~45 MB); appears in the flood scenario list. Not an official hazard map |
| Vector tiles (3D/2D speed) | `sh tools/install_tippecanoe.sh` | builds tippecanoe into `.tools/` (needs g++, make, zlib, sqlite headers; no root); tiles are then cut automatically per area, otherwise GeoJSON is used |
| Region pre-download | `python tools/prefetch_region.py --bbox w,s,e,n --name delhi-ncr` | footprints, then heights and population, then the exposure; later boxes inside it are cut with no network. Run on central Delhi (0.35 x 0.30 deg): 1.2 million buildings, about 2.5 GB peak memory; the plan's 1 x 0.8 deg example would need about 14x Noida's memory |
| Nepal calibration data | `python tools/get_nepal_calibration.py`, then `tools/prepare_nepal_survey.py` | building table (open mirror), HRRP ward polygons, ShakeMap, GEM files |
| Storey spot-check | `python tools/spotcheck_storeys.py make --area noida` | writes a sheet of 50 buildings for you to check by eye; `score` computes agreement. Needs a person |

Area ids are in the saved-area list (`GET /api/areas`). After adding a raster the area's exposure rebuilds automatically on next load (about 70 s for 483k buildings).

### 6.4 Use it

1. Pick a hazard, set the scenario, **Run simulation**. Ranges appear after a few seconds for earthquakes.
2. **Show hospitals, fire stations and roads** (first time needs internet), then **Estimate hospital access**, press **Route** next to a neighbourhood, **Suggest dispatch order**.
3. **Report (PDF)** / **Table (CSV)** / **Save map as image**; **Play wave animation** for warning times.

### 6.5 Tests

```bash
pytest -q                                   # ~130 tests, no network needed
python tests/test_regression.py --update    # regenerate the synthetic baseline (explain every change in CHANGELOG)
python tools/baseline.py --check            # real-data baselines (needs the downloaded baseline area)
.venv-verify/bin/python -m pytest tests/test_gmpe_openquake.py    # OpenQuake comparison, separate environment
```

---

## 7. Getting data off-site (manual downloads)

Needed from the internet: loading areas (Overture), neighbourhood names (Nominatim), facilities (Overpass) and roads (OSM) the first time, map tiles, the map library CDN. Download on a good connection and copy into `data/`; use resumable downloads (`curl -L -C -`); bounding boxes are **west,south,east,north**. Keep `data/` out of git. Fully offline use is not supported.

Not scriptable and needing a person: the official Nepal 2015 survey (registration at microdata.nsonepal.gov.np/index.php/catalog/69; those hosts also do not answer from some networks), the Anbazhagan & Bajaj (2023) chapter (paywalled), and the 50-building storey spot-check (`tools/spotcheck_storeys.py`). The Hazus manual is read from the Internet Archive (FEMA refuses scripted downloads); the JRC depth-damage workbook and the census compendium likewise.

---

## 8. Settings you can tune

| Where | What |
|---|---|
| `priors/fragility.json` (or `data/priors/fragility.json`) | Fragility medians and dispersion per construction type; carries its own status text. **Placeholders** until calibrated |
| `priors/casualty_rates.csv`, `casualty_params.json` | Hazus 6.1 casualty rates per type, collapse fractions, PAGER parameters (verified against the sources) |
| `priors/census_materials.csv` → `tools/build_construction_priors.py` | Census wall/roof shares behind the type probabilities |
| `priors/occupancy.csv` | Night/day/commute shares by use |
| `priors/street_width.csv`, `road_speeds.csv`, `wind_fragility.json` | Assumed road and wind tables (the flood curve `flood_damage.csv` is the JRC one) |
| `gmm.py` `LOGIC` | Ground-motion model weights by distance (with the evidence in the comments) |
| `rupture.py` `Z_BOTTOM`, `DEFAULT_DIP` | Seismogenic depth and default dips |
| `heights.py` `STOREY_M` | Metres per storey (2.8) |
| `uncertainty.py` `B_KM`, `FRAG_LN_SD`, `OCC_LN_SD` | Correlation range and the assumed spreads |
| `roads.py` `FREE_MIN_M`, `DIR_W`, `LAMBDA` | Free width for emergency vehicles (Anelli et al. 2020), debris-direction weight (assumed), route penalty |
| `population.py` `CELL`, `FACTOR_RANGE` | GHS-POP scaling grid and limits |
| `simulate.py` `buildings_3d(near, far)`, `buildings_in(limit)` | View budgets |
| `app.py` `MAX_DEG2` | Largest downloadable area (~30 km) |
| `exposure.METHOD_VERSION` | Bump when the exposure method changes; saved areas rebuild from raw footprints |

---

## 9. API

| Endpoint | Purpose |
|---|---|
| `GET /` , `GET /api/meta` | Page; centre/bounds (`empty: true` with no area) |
| `POST /api/area` | `{w,s,e,n,refresh}` → `{job}`; `GET /api/jobs/{id}` progress/result |
| `GET /api/areas`, `POST /api/areas/{id}/activate` | Saved areas |
| `POST /api/simulate` | Earthquake: `{lat,lon,depth_km,magnitude,time_of_day,strike,dip,rake,uncertainty}` → run, cells, rupture, summary (incl. casualties), `uncertainty_job` |
| `GET /api/uncertainty/{run}` | Ranges, worst-case cells, assumptions (or progress) |
| `GET /api/hazards`, `POST /api/hazard/cyclone`, `POST /api/hazard/flood`, `POST /api/flood/upload?name=` | Other hazards; raster upload |
| `GET /api/buildings`, `/api/buildings3d`, `/api/building/{id}?run=` | View data and popups |
| `POST /api/responders/prepare` → `GET /api/facilities`, `POST /api/access`, `GET /api/access/{run}`, `GET /api/route?rank&source`, `POST /api/dispatch` | Responder layer |
| `POST /api/report/{run}/image`, `GET /api/report/{run}.pdf`, `.csv` | Report export |
| `GET /api/confidence`, `GET /api/name`, `GET /api/area/download` | Trust items; names; parquet |

---

## 10. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "Load an area first." | Fresh install: use **Load a new area** |
| Area load fails | No internet, `overturemaps` missing, empty box, or over ~30 km |
| Soil shows "flat 250" | Vs30 raster not installed (`tools/get_vs30.py`) |
| "Storeys known for 0%" / people look far too high | No height or population raster for that area; run `tools/get_google_heights.py` / `tools/get_ghs_pop.py` |
| Responder layer slow or fails | First run downloads OSM data. The facility lookup retries on a second Overpass server; the road download is a single OSMnx request, so try again if Overpass is busy. Facilities are cached once complete |
| PDF fails | WeasyPrint's system libraries (Pango/Cairo) missing |
| Ranges never appear | Earthquake only; they need a few seconds. Other hazards have none |
| Popup says "results are out of date" | A newer run replaced the one on screen; run again |
| Blank page / map failed | Map library blocked by an ad-blocker or no network; reload |

---

## 11. Roadmap (what remains)

1. **Calibrate the fragility curves** (the most important fix): get the Nepal 2015 survey, then `tools/calibrate_fragility.py --apply`; compare with GEM/HAZUS; verify the casualty tables against the Hazus manual.
2. **Better shaking for the largest events:** use a supplied rupture polygon (e.g. USGS) instead of a rectangle; the replays show a 2-3x under-estimate near the long Türkiye rupture.
3. **Verify the storey model for small buildings** with ground truth (the satellite cannot resolve them), and add road-distance features.
4. Calibrate debris blocking against the Haiti/Tokyo/Amatrice models; real depth-damage and wind-fragility curves; casualty and Monte Carlo for flood and cyclone.

---

## 12. Data sources and attribution

Full list with licences: `docs/data_sources.md`. In brief: © OpenStreetMap contributors (ODbL) for the basemap, roads and facilities; Overture Maps Foundation for footprints and places; Google Open Buildings 2.5D Temporal (CC BY 4.0) for heights; GHSL GHS-POP R2023A (JRC) for population; USGS Vs30 mosaic and ShakeMaps; Copernicus Emergency Management Service grading for validation; USGS ps2ff (public domain); Boore & Atkinson (2008), Boore et al. (2014), Sharma et al. (2009), Worden et al. (2012), Holland (1980); MapLibre GL JS. Exported images and reports carry the attribution automatically.
