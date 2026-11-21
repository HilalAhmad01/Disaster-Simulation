# Quake Damage Map

A city-scale earthquake damage estimator. You enter an earthquake (epicentre, depth, magnitude) and the app shows, on an interactive map, how hard every part of the city shakes, which buildings are likely to be damaged, which neighbourhoods need responders first, and how many people are likely at risk. You can zoom from the whole city down to individual buildings, in 2D or 3D.

**Status:** working prototype (v1.x). The numbers are plausible and internally consistent, but the damage curves are still uncalibrated placeholders. Treat output as a planning aid, not as a prediction.

---

## 1. Motive

When a disaster hits, first responders need to know within minutes where the worst damage is, how many people are affected, and where to send teams first. Official loss tools exist, but they are usually national in scale and not building-level or easy to explore.

This project aims at a city-wide tool built on open data (OpenStreetMap-derived footprints, USGS soil data) and open methods:

- **Input:** disaster parameters, e.g. earthquake epicentre (it can be outside the city, as in real life), depth and magnitude.
- **Pipeline:** physics-based ground shaking, building-by-building damage estimates, and (later) machine learning to fill data gaps and calibrate against real earthquakes.
- **Output:** a red/yellow/green/blue damage map down to individual buildings, ranked priority neighbourhoods with the numbers a responder needs, and (planned) fastest routes to them.

Earthquakes first; other hazards (flood, cyclone) are planned to plug into the same layers later.

---

## 2. What it does today

- Draw a box on the map to **download buildings for any area** (up to ~30 km across) and switch to it; download the processed data as parquet.
- Set the epicentre by typing coordinates, dragging the diamond marker, shift-clicking, or the **Pick epicentre on map** button.
- Run a simulation and get a **heat map** of damage, which turns into **colour-coded buildings** when you zoom in.
- Click a building: use (estimated), construction type, storeys, shaking (MMI and g), soil stiffness, chance of heavy damage and of collapse, estimated occupants and people at risk.
- **Top 8 priority neighbourhoods**, ranked by people at risk, with pins, a side list, and names (from OpenStreetMap's Nominatim service, filled in one by one).
- **3D view:** buildings extruded by storeys, tilt up to 75°, about 190,000 buildings in view (nearest 40,000 with full detail, next 150,000 simplified). Optional "lower buildings by collapse chance" visual cue.
- **Save map as image:** drag a box; get a PNG with title, parameters, legend, summary and OSM/Overture attribution.
- Summary panel: buildings assessed, severe/moderate counts, expected collapses, people at risk, peak shaking and intensity (MMI), median soil stiffness.

---

## 3. How it works

```
 earthquake (lat, lon, depth, magnitude)
        |
        v
 [1] HAZARD      Boore & Atkinson 2008 ground-motion model, per-building soil stiffness (Vs30),
                 hypocentral distance (so depth matters) -> PGA (g) -> MMI (Worden 2012)
        |
 [2] EXPOSURE    Overture building footprints -> likely use, storeys, construction type, occupants
        |
 [3] DAMAGE      lognormal fragility curves by construction type -> P(slight/moderate/extensive/collapse)
        |
 [4] IMPACT      expected damage state (colour class), people at risk = occupants x P(extensive or worse)
        |
 [5] AGGREGATE   500 m cells -> heat map + top-8 priority neighbourhoods -> names via Nominatim
        |
        v
 MapLibre map: heat map, 2D/3D buildings, popups, priority list, PNG export
```

**Colour classes** (from expected damage state): blue = little or none, green = light, yellow = moderate, red = severe / collapse risk.

**Occupancy** is estimated by building use (gross floor area per person): residential 15 m², office 12, retail 10, hotel 30, school 7, medical 20, factory 40, warehouse 150, religious 10, sheds ~400. Unlabelled buildings are guessed from footprint size (tiny = shed, over 1,500 m² = large building of unknown use at 100 m²/person, otherwise housing). Industrial and warehouse buildings are capped at 2 floors and treated as concrete-frame, not masonry. Cap of 4,000 people per building.

**Assumptions worth knowing**
- Point-source earthquake; distance is hypocentral, mechanism unspecified.
- The shaking model is built for shallow crustal quakes within about 200 km. Distant Himalayan-arc events need a regional model (planned).
- Soil data is the USGS Global Vs30 Mosaic at ~900 m resolution: good for broad soft-vs-stiff ground, not for street-level detail.
- **Fragility curves and median values are placeholders.** Calibration against real damage data is the top planned fix.
- Construction type is a storeys-based heuristic. ML replaces it in v2.
- Numbers describe expected damage, not any single real outcome.

---

## 4. What has been done

| Stage | Work |
|---|---|
| v1 | End-to-end pipeline: OSM-style footprints, placeholder shaking and damage, MapLibre map, heat map, colour-coded buildings, clickable popups, priority neighbourhoods |
| v1.x | Buildings load per map view (483k buildings in Noida would freeze the browser otherwise) |
| v1.x | Fixed damage-class thresholds/labels (yellow had been mislabelled "heavy"); rescaled heat map |
| v1.x | Replaced placeholder shaking with Boore & Atkinson 2008 + MMI output; added sanity tests (`pytest`) |
| v1.x | Click-to-pick and draggable epicentre |
| v1.x | Better people estimates by building use (a large warehouse no longer shows 10,000+ people) |
| v1.x | UI **Load a new area** (downloads Overture buildings for a drawn box), parquet download; **Save map as image** |
| v1.x | Parquet startup cache (faster restarts); neighbourhood names via Nominatim |
| v1.x | Real soil stiffness per building from the USGS Vs30 Mosaic (needs the .tif, see below) |
| v1.x | 3D view with collapse-lowering option; large 3D render distance (near + far rings) |
| v1.x | Speed: gzip responses; buildings sent with only what the map draws (details load on click); the map skips reloading while the view stays inside what it already has; new areas download only the 5 needed columns (about 28% fewer bytes). 2D view about 10x faster (2.4 s, 9.3 MB to 0.15-0.3 s, under 1 MB); 3D 2.1 s, 39 MB to 1.3 s, 5 MB |

Noida (about 483,000 buildings from Overture) was used as the test city.

---

## 5. Project layout

```
disaster/
  app.py              FastAPI server: serves the page and the API
  simulate.py         Model class: hazard -> damage -> impact, cells, per-view building queries
  gmpe.py             Boore & Atkinson 2008 PGA model + PGA -> MMI
  soil.py             Vs30 sampling from the USGS GeoTIFF (flat 250 m/s fallback)
  exposure.py         Footprints -> use, storeys, construction type, occupants
  overture.py         Downloads buildings for a bounding box from Overture Maps
  places.py           Neighbourhood names (Nominatim, rate-limited, cached)
  build_exposure.py   Command-line way to build data/buildings.parquet from a footprint file
  index.html          The whole front end (MapLibre GL JS, loaded from a CDN)
  requirements.txt
  tests/test_model.py Sanity tests
  data/               Your data (not included): buildings.parquet, the Vs30 .tif, areas/
```

The code also works if you put the Python files in a `backend/` folder and `index.html` in a `frontend/` folder.

---

## 6. How to make it work

### 6.1 Set up the environment

Conda is recommended (geopandas and rasterio install more reliably from conda-forge):

```bash
conda create -n dis python=3.11
conda activate dis
conda install -c conda-forge geopandas rasterio pyarrow shapely
pip install fastapi "uvicorn[standard]" scipy overturemaps pytest
pip install osmnx        # only needed for the Overpass fallback in build_exposure.py
```

### 6.2 Get building footprints (pick one)

**A. Easiest: from the app.** Start the server (6.5), open the page, click **Load a new area**, and drag a box. This needs internet on the server and the `overturemaps` package. The area is saved in `data/areas/`.

**B. Command line, Overture Maps:**
```bash
overturemaps download --bbox=77.30,28.42,77.55,28.65 -f geoparquet --type=building -o data/noida.parquet
```
Bounding box order is **west,south,east,north** (longitude first). That box covers roughly Noida. Use a smaller box first to test.

### 6.3 Build the exposure file (needed for the default area)

```bash
python build_exposure.py data/noida.parquet
```
This writes `data/buildings.parquet` and prints estimated people in total and by building use. Sanity check that total against what you expect for the area; if it is far off, tune the densities in `exposure.py` (`DENS`).

### 6.4 Add soil data (optional but recommended)

1. Download `vs30_mosaic_median_30c.tif` from the USGS Global Vs30 Mosaic data release: https://data.usgs.gov/datacatalog/data/USGS:67be4ac3d34e8876fcbfbd89
2. Save it in `data/` with exactly that name (or set `VS30_TIF=/full/path/file.tif`).
3. On startup the terminal should print `Soil: USGS Global Vs30 Mosaic, median Vs30 ... m/s`. If it says `flat 250 m/s`, the file was not found (the message shows the path searched).

### 6.5 Run

From the project folder:
```bash
uvicorn app:app --reload
```
Open http://localhost:8000. The first start may be slow while buildings load.

### 6.6 Use it

1. (Optional) **Load a new area** and drag a box over the place you want.
2. Set the epicentre: type coordinates, **Pick epicentre on map**, drag the diamond, or shift-click.
3. Set magnitude and depth, click **Run simulation**.
4. Zoom in to see buildings. Click a building or a numbered pin for details.
5. **Switch to 3D view** for the tilted view (right-drag or Ctrl-drag to rotate and tilt).
6. **Save map as image** to export a PNG of a selected area.

Useful test scenarios: M6.5 at 15 km near the city centre (mostly green/blue, some yellow); M7.5 at 10 km (red appears); the same quake moved 50 km away or made deeper (damage should drop sharply).

### 6.7 Tests

```bash
pytest -q
```
Checks: shaking falls with distance and depth, rises with magnitude, soft soil amplifies, plausible anchor values, damage-curve ordering, an end-to-end run on four synthetic buildings, people estimates, soil interpolation and fallback, name selection, lean building queries and popup details, column-only Overture downloads.

---

## 7. Getting data off-site (manual downloads)

The app needs internet for: loading new areas, neighbourhood names, the map background tiles (OpenStreetMap), and the map library from a CDN. If the machine running the server has poor or no internet, download data elsewhere and copy it in.

**General tips**
- Download on a good connection, then move files with a USB drive or `scp`/`rsync` into `data/`.
- For big files use a resumable download: `curl -L -C - -o file.tif "URL"` (or `wget -c URL`).
- Check sizes after copying; a half-downloaded parquet or tif fails with confusing errors. If the source publishes a checksum, compare with `sha256sum file`.
- Keep `data/` out of version control (it is large). Keep the original downloads; the pipeline can always rebuild from them.
- Start with a small area to test the whole chain before downloading a whole city.
- Bounding boxes are **west,south,east,north** in degrees; swapping latitude and longitude is the most common mistake.

**Building footprints**
- *Overture Maps* (recommended): the `overturemaps download` command above, run on any machine, then copy the parquet and run `python build_exposure.py data/yourfile.parquet`. In Indian cities it is usually more complete than OSM alone.
- *BBBike extracts* (extract.bbbike.org): draw a box, choose GeoJSON or PBF, a download link arrives by email in a few minutes. Pure OSM data. `build_exposure.py` accepts any file geopandas can read.
- *Geofabrik*: country/regional OSM extracts. Big (hundreds of MB to over 1 GB), needs clipping.
- *Overpass API*: avoid for whole cities. The public servers rejected city-sized queries (502 "at capacity"). Fine for small queries only.

**Soil data**
- The Vs30 GeoTIFF above is a one-off download; copy it into `data/`.

**Calibration data (planned, see roadmap)**
- Nepal 2015 Gorkha building damage survey: CBS/NSO Nepal Microdata Catalog (https://microdata.nsonepal.gov.np/index.php/catalog/69, may need registration) or the Kaggle mirror "Earthquake magnitude damage and impact" (by arashnic).
- USGS ShakeMap for that quake: event page https://earthquake.usgs.gov/earthquakes/eventpage/us20002926, download `grid.xml` from the ShakeMap section.
- Nepal district boundaries (e.g. from HDX).

**Working fully offline** is not supported yet. It would need a local basemap (e.g. vector tiles), a bundled copy of the map library, and no name lookups (they fail gracefully, labels stay "Priority N").

---

## 8. Settings you can tune

| Where | What |
|---|---|
| `simulate.py` `FRAG`, `BETA` | Fragility medians (PGA, g) per construction type and spread. **Placeholders.** |
| `simulate.py` `np.digitize(ds, [0.5, 1.5, 2.5])` | Boundaries between the four colour classes |
| `simulate.py` `buildings_3d(near=40000, far=150000)` | 3D render budget. Lower it if 3D is slow, raise it if smooth |
| `simulate.py` `buildings_in(limit=60000)` | Most buildings sent for one 2D view (nearest the centre are kept) |
| `index.html` `PAD` | Extra margin loaded around the view (0.25 = a quarter of the view on each side), so small pans need no new request |
| `exposure.py` `DENS`, `SINGLE_STOREY`, cap | Occupancy densities and rules |
| `soil.py` `FLAT` | Soil stiffness used when no map is found (250 m/s) |
| `app.py` `MAX_DEG2` | Largest area the UI will download (about 30 km x 30 km) |
| `VS30_TIF` environment variable | Path to the soil raster if not in `data/` |

---

## 9. API (for reference)

| Endpoint | Purpose |
|---|---|
| `GET /` | The page |
| `GET /api/meta` | City centre and bounds |
| `POST /api/simulate` | `{lat, lon, depth_km, magnitude}` -> run number, cells, summary |
| `GET /api/buildings?w&s&e&n` | Buildings of the last run inside a view (2D): id, damage class, storeys, collapse chance |
| `GET /api/buildings3d?w&s&e&n&clat&clon` | Near (full detail) and far (simplified) rings for 3D, same lean properties |
| `GET /api/building/{id}?run` | Popup details of one building; 409 if `run` is not the latest simulation |
| `POST /api/area` | `{w,s,e,n}` -> download buildings, build exposure, switch area |
| `GET /api/area/download` | Download the latest loaded area as parquet |
| `GET /api/name?lat&lon` | Neighbourhood name via Nominatim |

---

## 10. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Overpass `502 Bad Gateway` | Public server overloaded. Use Overture or a BBBike extract instead |
| Blank page, "map failed to start" | Map library blocked (ad-blocker/network). Reload; check the browser console and Network tab |
| `Cannot read properties of undefined (reading 'startsWith')` | `index.html` and the server files are out of sync. Copy all files from the same version and restart |
| Summary shows soil as "flat" | The `.tif` is missing or named differently; see the startup `Soil:` line |
| `Load a new area` fails | Server has no internet, `overturemaps` not installed, or the box is over ~30 km |
| 3D view slow | Lower `far` in `buildings_3d`; zoom in closer |
| Names stay "Priority N" | Nominatim unreachable or the place has no mapped name; harmless |
| Red buildings never appear | Normal for moderate quakes; try M7.5 at 10 km |
| Popup says "results are out of date" | A newer simulation or area replaced the one on screen; run the simulation again |

---

## 11. Roadmap

Development plan: v1, v1.x fixes between versions, v2, v3.

**Remaining in v1.x**
1. **Calibrate the damage curves** (the most important fix). Fit fragility curves to the Nepal 2015 survey joined to the USGS ShakeMap. Caveats: the survey has no building coordinates (only district/municipality/ward IDs), so shaking will be a district average; its buildings are mostly rural masonry, so it will anchor masonry curves better than concrete ones. Municipality boundaries changed after 2017, so start at district level.
2. **Uncertainty ranges:** run the model many times with random inputs; show an expected case and a plausible worst case.
3. Verify the shaking model against OpenQuake's `BooreAtkinson2008` and add a regional model for distant Himalayan events.
4. Faster 3D via real vector tiles.

**v2: responder layer and machine learning**
- Hospitals, fire stations and shelters from OSM; routing over the road network with debris/blocked-road penalties; fastest route to a chosen neighbourhood; dispatch ordering.
- ML for building attributes (construction type, age, storeys) from footprint shape, area, neighbourhood and any labels; calibrate against real damage data.
- Better population and casualty estimates (including day/night occupancy).
- Validate by replaying a past earthquake and comparing with observed damage.

**v3: polish and extend**
- Confidence display, fast surrogate model, multi-city support, export/report for responders.
- Plug-in structure so floods and cyclones reuse the exposure, routing and map layers.

**Visual ideas (optional)**
- Seismic wave animation (P and S fronts, seconds of warning per neighbourhood), dark map theme, shake effect scaled to local intensity.

---

## 12. Data sources and attribution

- Map background and OSM data: © OpenStreetMap contributors (ODbL).
- Building footprints: Overture Maps Foundation (includes OpenStreetMap and other sources); follow its licence terms.
- Soil stiffness: USGS Global Vs30 Mosaic.
- Shaking model: Boore & Atkinson (2008), PGA coefficients as published. Intensity conversion: Worden et al. (2012).
- Neighbourhood names: OpenStreetMap Nominatim public service. Keep usage light (about one request per second, which the code enforces).
- Map rendering: MapLibre GL JS.

Exported map images include the OSM and Overture attribution automatically.
