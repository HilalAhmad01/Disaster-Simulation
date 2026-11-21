# Quake Damage Map: Master Implementation Plan

This is the complete plan to take the Quake Damage Map from the current v1.x prototype to the full roadmap in `README.md` (v1.x remaining items, v2, v3). It is written so a new chat or developer with no memory of earlier discussions can start work. Read this file and `quake-map/disaster/README.md` first.

**How to use this plan**
- Work phase by phase in the order given. Each phase depends on the ones before it (section 3 explains why).
- Every task lists: goal, steps, files, tests and "done when" criteria. Tick the checkbox in section 13 when a task is done and note the date.
- After each task: run `pytest -q`, update the README (sections 2, 4, 8, 9 and 10 as relevant) and record any numbers that changed in `CHANGELOG.md`.
- Facts marked **VERIFY** came from web research and were not confirmed against the primary source. Check them before relying on them.
- Do not lower quality or accuracy for speed. The owner of this project has said so explicitly. Any approximation must be measured and shown to be invisible or negligible, then written down.

---

## 1. Current state

### 1.1 What the app is
A FastAPI + MapLibre web app that estimates earthquake damage building by building for a city.

Pipeline:
1. **Shaking:** Boore & Atkinson 2008 (BA08) gives peak ground acceleration (PGA) per building, using soil stiffness (Vs30) from the USGS Vs30 Mosaic GeoTIFF. The Worden 2012 relation converts PGA to intensity (MMI).
2. **Damage:** lognormal fragility curves per construction type give the probability of each damage state.
3. **People at risk:** occupants × P(extensive damage or worse).
4. **Aggregation:** 500 m cells, a heat map and the top-8 priority neighbourhoods, named through Nominatim.

The test city is Noida, about 483,358 Overture buildings.

### 1.2 Files (all in `quake-map/disaster/`)
| File | Role | Key functions |
|---|---|---|
| `app.py` | FastAPI server and API | `load_default()`, `simulate`, `buildings`, `building/{i}`, `buildings3d`, `load_area`, `place_name`, `download_area` |
| `simulate.py` | Core model | `FRAG`, `BETA`, `haversine`, class `Model` (`__init__`, `run`, `buildings_in`, `buildings_3d`, `building`, `_features`, `_nearest_first`, `_geom_json`) |
| `gmpe.py` | BA08 PGA, PGA→MMI | `pga_g(mag, dist_km, vs30, mech)`, `mmi_from_pga` |
| `soil.py` | Vs30 sampling | `vs30_at(lat, lon, tif)`, `bilinear`, `FLAT=250` |
| `exposure.py` | Footprints → use, storeys, type, occupants | `attributes(b, area)`, `make_exposure(b)`, `DENS`, `LABEL` |
| `overture.py` | Overture download (only the 5 needed columns) | `fetch_buildings(bbox)`, `_read_columns` |
| `places.py` | Nominatim names (rate-limited, cached) | `reverse_name`, `pick_name` |
| `build_exposure.py` | Command-line way to build `data/buildings.parquet` | n/a |
| `index.html` | The whole front end in one file (MapLibre 4.7.1) | `show`, `loadBld`, `covered`, `resetBld`, `makePng`, `loadArea`, `set3d`, `nameAreas` |
| `tests/test_model.py` | 14 sanity tests | n/a |

`README.md` exists twice: at the project root and in `quake-map/disaster/`. The two are currently identical; keep them so until decision D1 in section 12 is made.

### 1.3 Performance work already done (fixes 1–4 of the performance report)
- gzip responses. Building JSON is built as a string directly, skipping FastAPI's encoder.
- Lean building payloads: `id`, `cat`, `levels`, `pc`. Popup details come from `GET /api/building/{id}?run=`, which returns 409 when the map shows an older run.
- Front end: requests wait 150 ms after panning stops, stale requests are cancelled with AbortController, and a 25% padded box is loaded so later requests are skipped while the view stays covered.
- Overture download fetches only the needed columns: 93 MB → 67 MB for Noida.
- Result: a 2D view went from 2.4 s / 9.3 MB to 0.15–0.27 s / 0.95 MB; 3D from 2.1 s / 39 MB to 1.3 s / 5 MB.
- One small approximation: distant 3D buildings are rounded to 5 decimals instead of snapped with `set_precision`. 96.6% of shapes are identical; the rest differ by about 1 m (99th percentile 2.9 m). See decision D2.

### 1.4 Measured facts that drive this plan
- **Labels are almost absent** in Overture for Noida. Out of 483,358 buildings: `num_floors` 0.21%, `height` 0.01%, `class` 1.05%. The labelled ones lean towards tall apartment blocks (median 5 floors). So 99.8% of storeys and almost all construction types are guesses.
- **Occupancy looks several times too high.** The model puts 9,114,688 people in the Noida box (lon 77.30–77.55, lat 28.42–28.65). For scale, Gautam Buddha Nagar district had about 1.65 M people in the 2011 Census (**VERIFY**), and the box adds only parts of East Delhi and Ghaziabad. "People at risk" is inflated by the same factor.
- **Shaking distance:** BA08 is defined for Joyner-Boore distance (Rjb, distance to the surface projection of the rupture). The code passes hypocentral distance as if the quake were a point. For M ≥ 7 the rupture is tens to over 100 km long, so near-fault shaking away from the epicentre is underestimated.
- **Speed today:** a simulation takes about 0.2 s. Downloading an area is limited by bandwidth (about 3 MB/s from AWS us-west-2 on the owner's connection; parallel requests do not help).
- **Environment:** the README recommends a conda env `dis` with Python 3.11. Development tests also ran in a Python 3.14 venv. `data/` is empty in the repo; it contains only `.gitkeep`.
- **No git yet.**

---

## 2. Target end state

1. **Building data:** measured storeys for most buildings (Google Open Buildings 2.5D heights), construction-type *probabilities* from census priors, and occupancy matched to GHS-POP population, with separate day and night numbers.
2. **Shaking:** finite-fault distance, BA08 checked against OpenQuake, a regional weighted mix of models for distant Himalayan events, and a ground-motion spread with spatial correlation.
3. **Damage:** fragility curves fitted to Nepal 2015 survey data, compared with GEM and HAZUS, with documented sources and confidence ranges.
4. **Uncertainty:** Monte Carlo runs giving expected values plus 10–90% ranges and a worst-case priority map.
5. **Validation:** a replay of past earthquakes, with skill scores, written up in `docs/validation.md`.
6. **Responder layer:** hospitals, fire stations, police and shelters (flagged if likely damaged); a road network with chances of blocking from debris; travel times, routes and a dispatch order; casualties by time of day.
7. **Platform:** an area registry (multiple cities, cached areas), background jobs with progress, vector tiles for 3D, a responder report export, and a hazard plug-in interface with flood and cyclone modules.
8. **Extras:** seismic-wave animation with warning times, and a dark theme.

---

## 3. Order and why

```
Phase 0  Foundations (git, baselines, startup without data, area registry, background jobs)
Phase 1  Building data: storeys, type probabilities, population, day/night
Phase 2  Shaking: OpenQuake check, finite fault, regional models, ground-motion spread
Phase 3  Calibrate fragility (Nepal 2015) + validate (held-out Nepal, Kathmandu, Türkiye 2023)
Phase 4  Uncertainty ranges (Monte Carlo)
Phase 5  Responder layer: facilities, roads + blockage, routing, dispatch, casualties
Phase 6  Platform: vector tiles, multiple cities, export/report, confidence display
Phase 7  Hazard plug-ins: interface, flood, cyclone
Phase 8  Visual extras
```

**Rule: calibrate last among the model parts.** If damage curves are fitted while storeys, types, population or distance are wrong, the curves absorb those errors and stop meaning anything. So the order is building data (1), then shaking (2), then calibration (3). Uncertainty (4) needs the spreads from 2 and 3. The responder layer (5) needs trustworthy damage numbers.

Rough effort for one developer: Phase 0 about 1 week, 1 about 3 weeks, 2 about 3 weeks, 3 about 3 weeks, 4 about 1 week, 5 about 4 weeks, 6 about 2–3 weeks, 7 about 3–4 weeks, 8 about 1 week. **About 5 months in total.** For a deadline, see the minimum viable path in section 11.

---

## 4. Cross-cutting rules (apply in every phase)

- **Style:** match the existing code. That means compact, vectorised numpy and pandas (no per-building Python loops on hot paths), short docstrings stating units, a single-file front end (`index.html`) and plain English in the UI.
- **Units in names:** `_g` for PGA in g, `_km`, `_m`, `_mps`. Write coordinates as (lon, lat) for GeoJSON and (lat, lon) in model function signatures, as in the code today.
- **Provenance:** every model constant (fragility medians, densities, GMPE weights, casualty rates) needs a comment or `docs/` entry giving the source, table number and version.
- **Data layout** (created in Phase 0):
  ```
  data/
    areas/<area_id>/raw.parquet, exposure.parquet, meta.json
    rasters/vs30_mosaic_median_30c.tif
    rasters/ghs_pop/...        rasters/google_heights/...
    priors/construction_mix.csv, occupancy.csv
    calibration/nepal/..., shakemaps/...
    validation/<event>/...
    roads/<area_id>.graphml     facilities/<area_id>.parquet
  ```
  `data/` stays out of git. Each phase adds `tools/get_<dataset>.py` download scripts so data can always be rebuilt.
- **Regression baseline:** after any change to the model, run `python tools/baseline.py` and compare with `tests/baselines/*.json` (see Phase 0). Every changed number must be explained in `CHANGELOG.md`.
- **Tests:** each new module gets tests in `tests/`, and the suite is split by phase (`test_exposure.py`, `test_hazard.py`, ...). Tests that need large data or network access must `pytest.skip` cleanly when it's missing. Tests that need OpenQuake skip when it isn't installed.
- **Performance:** do not make building views, 3D or popups slower (see section 1.3 for today's numbers). A simulation must stay under 1 s; Monte Carlo runs in the background.
- **Licences:** record each dataset's licence in `docs/data_sources.md`. GEM models and some datasets are non-commercial (CC BY-NC-SA, **VERIFY**). Flag that in the README.
- **README:** keep sections 2 (features), 4 (what has been done), 8 (settings), 9 (API), 10 (troubleshooting) and 11 (roadmap) up to date.

---

## 5. Phase 0: Foundations (about 1 week)

### 0.1 Version control
- `git init` at the project root, with a `.gitignore` covering `data/*` except `.gitkeep`, `__pycache__/`, `.pytest_cache/`, `*.parquet`, `*.tif`, `.venv/` and `venv/`.
- First commit: "v1.x baseline (after performance fixes 1–4)".
- **Done when:** the repo exists and `git status` is clean.

### 0.2 Server starts without data
**Problem:** `load_default()` in `app.py` raises an error when `data/buildings.parquet` and `.geojson` are both missing, so a fresh clone cannot start.

**Change:**
- With no data, start with `model = None`.
- `/api/meta` returns `{"center": [77.39, 28.57], "bounds": null, "empty": true}`. Use a configurable default centre via the env var `DEFAULT_CENTER`.
- `simulate`, `buildings`, `buildings3d` and `building` return 409 "Load an area first."
- The front end shows a hint pointing to **Load a new area** when `empty`.

Tests: use FastAPI `TestClient` with a temporary `root` (refactor so `root` is configurable through the env var `QUAKE_DATA_DIR`).

**Done when:** a fresh clone starts, the page loads, and loading an area works without running `build_exposure.py`.

### 0.3 Baseline harness
Create `tools/baseline.py`. It:
- loads a fixed area: the 6×5 km box `77.30,28.55,77.36,28.60`, downloaded once to `data/areas/baseline/`;
- runs 3 scenarios: (a) M6.5 at 15 km depth near the centre, (b) M7.5 at 10 km depth, (c) M7.5 at 10 km depth placed 50 km away;
- writes `summary` plus percentiles of PGA and damage-state probabilities to `tests/baselines/<scenario>.json`.

Also add a synthetic 1,000-building fixture with a fixed seed, generated in code and needing no download, to `tests/test_regression.py`, so CI has a deterministic baseline.

**Done when:** `python tools/baseline.py --check` reports the differences against the saved baselines.

### 0.4 Area registry (performance fix 5, done without loss of accuracy)
**Goal:** never download the same area twice, and keep each area's raw footprints so building data can be rebuilt as the methods improve.

New `areas.py`:
- Each area lives in `data/areas/<id>/` with `raw.parquet` (the KEEP columns from Overture), `exposure.parquet` and `meta.json`. The metadata holds the bbox, the Overture release, the creation time, the building count and the exposure-method version string.
- `find_covering(bbox)` returns an area whose bbox contains the requested one.
- **Clip rule:** must match Overture's own rule exactly. Overture selects buildings whose *bbox struct* intersects the query box. Raw footprints do keep the `bbox` column if it is added to the downloaded columns, so add `bbox` to `KEEP` in `overture.py` and clip with the same comparisons `(xmin < e) & (xmax > w) & (ymin < n) & (ymax > s)`.
- Rebuild the exposure from the clipped raw footprints with `make_exposure`. Do not clip the stored exposure, because the choice of UTM zone and later area-level steps (population scaling) depend on the area.
- **Release check:** call `overturemaps.core.get_latest_release()` (about 0.5 s). If it is newer than the cached area's release, offer "Update to the latest Overture release" and don't re-download silently.
- `load_default()` reopens the most recently used area.

API: `GET /api/areas` (list) and `POST /api/areas/{id}/activate`. The existing `POST /api/area` checks the registry first.

Tests: the clip rule matches a direct `_read_columns` result on a fake dataset; an area inside a cached one makes no network call (patch `fetch_buildings`).

**Done when:** selecting the same or a smaller area a second time takes under 2 s for Noida, and the results are identical to a fresh download (compare `exposure.parquet` row by row on a test box).

### 0.5 Background jobs with progress (performance fix 6)
- New `jobs.py`: an in-process job registry (`ThreadPoolExecutor(max_workers=2)`). Each job has an id, a state (queued, running, done, error), a progress number from 0 to 1, a message and a result.
- `POST /api/area` returns `{"job": id}` straight away. `GET /api/jobs/{id}` polls the job (the front end polls once per second).
- The download reports progress per Overture row group: iterate `dataset.get_fragments(flt)` and `split_by_row_group`.
- The area being worked on stays usable until the new one is ready, then the switch happens in one step.
- The Monte Carlo in Phase 4 reuses this job system.

**Done when:** loading an area shows "Downloading 12/23 parts…" and the map stays usable meanwhile.

### 0.6 Optional: region pre-download (performance fix 7)
`tools/prefetch_region.py --bbox ... --name delhi-ncr` downloads a large region once, for example overnight, and stores it as a registry area that later boxes are clipped from. Same release-check rule as 0.4.

---

## 6. Phase 1: Building data (about 3 weeks)

**Goal:** replace the guesses for storeys, construction type and occupancy with measured or calibrated values. Show users what is measured and what is estimated.

### 1.1 Storeys from Google Open Buildings 2.5D Temporal
**Data:** Google Open Buildings 2.5D Temporal (2016–2023, yearly, about 4 m effective resolution, building presence/count/height, height in [0, 100] m above ground, CC-BY 4.0 / ODbL, covers India). Access options:
- (a) the download links on https://sites.research.google/gr/open-buildings/temporal; find the Google Cloud Storage bucket and tile scheme there (**VERIFY** the exact path);
- (b) an Earth Engine export of collection `GOOGLE/Research/open-buildings-temporal/v1` (**VERIFY** the ID) for the area bbox, as GeoTIFF.

Write `tools/get_google_heights.py --area <id>`, which downloads the tiles for the 2021, 2022 and 2023 rasters, clipped to the area bbox plus a small pad, into `data/rasters/google_heights/<area_id>/`.

**Method** (new `heights.py`):
1. Check the native pixel size of the files. The *effective* resolution is about 4 m but the stored pixels may be finer.
2. Per building, take the zonal **median** height over the pixels inside the footprint, for each year. Rasterise footprint indices onto the raster grid with `rasterio.features.rasterize`, using `all_touched=False`, then group with numpy (sort by building index, take the median per group). For footprints covering fewer than 2 pixels, use bilinear sampling at the centroid.
3. Combine the years with the median over 2021–2023 (Google notes heights are not stable from year to year). Record `h_spread` = max − min across the years.
4. `levels_est = clip(round(h / 3.0), 1, 40)`. Calibrate the 3.0 m storey height in 1.4.

Priority in `exposure.attributes()`: `num_floors` / `building:levels` label > `height` label / 3 > Google height > today's heuristic. Add the column `levels_src` with values `label`, `label_height`, `google`, `guess`. For Google-derived heights where `h_spread` > 6 m or h < 2 m, fall back to the heuristic and flag `levels_src = "guess_unstable"`.

UI: the popup shows "3 storeys (measured from satellite heights)" or "(estimated)".

Tests: a synthetic raster with known heights and footprints gives the right median; the priority order holds; behaviour is graceful when there is no raster (everything becomes `guess`).

**Done when:** in Noida, more than 80% of buildings get `google` or `label` storeys, and the check in 1.4 passes.

### 1.2 Construction-type probabilities (replacing the single `btype`)
**Why:** the construction type is the biggest unknown and no labels exist. A probability mix is more honest than one guess, and it is what the Phase 4 uncertainty work needs.

**Data:** Census of India 2011 Houselisting tables. HL-03, "Households by predominant material of roof and wall", at district or town level for urban areas (https://censusindia.gov.in/nada/index.php/catalog/9392 and the state HL-03 and HH series; **VERIFY** which table has town or ward level). Optionally, GEM global exposure model India shares at state level (https://github.com/gem/global_exposure_model), for cross-checking.

**Caveat to document:** census "burnt brick wall" does not separate load-bearing brick masonry from an RC frame with brick infill. A concrete roof is common to both. So the census gives a *prior*, not an answer. The storey count is the strongest signal.

**Method:**
- `data/priors/construction_mix.csv` with columns `region_id, urban, storeys_bin, p_URM, p_RC_LOW, p_RC_MID, source`. Storey bins: 1, 2, 3–4, 5–7, 8+.
- Build it with `tools/build_construction_priors.py` from the census wall and roof shares and these documented rules (starting values; refine with GEM shares where available):
  - storeys ≥ 5: RC_MID 0.95, RC_LOW 0.05;
  - storeys 3–4: RC_LOW = share with concrete roof (clipped to [0.6, 0.95]), the rest URM;
  - storeys 1–2: URM = share with burnt-brick or stone walls and non-concrete roofs, plus half of burnt-brick with concrete roof; the rest RC_LOW;
  - engineered uses (industrial, warehouse, office, retail, hotel, education, medical) or area ≥ 1,500 m²: RC_LOW at least 0.8 (keeps today's rule as a prior).
- `exposure.make_exposure` writes `p_URM, p_RC_LOW, p_RC_MID` and keeps `btype` as the most likely type for display.
- `simulate.Model.run`: compute exceedance probabilities per type and mix them:
  ```
  for t in types: P_t = Φ((ln PGA − ln θ_t,k) / β_t)      # shape (n, 4) each
  P = Σ_t w_t · P_t                                      # w from p_<type> columns
  ```
  This costs about 3× the damage step, which is still well under 1 s for 483k buildings. Older parquet files without `p_*` columns: derive one-hot weights from `btype`.
- Allow `BETA` per type: `BETA = {"URM": ..., ...}`. Phase 3 needs this.
- The popup shows "Likely concrete frame, low-rise (70%); masonry (30%)".

Tests: one-hot weights reproduce today's results exactly; probabilities sum to 1; the mix stays monotonic in PGA.

**Done when:** the baseline scenarios run with mixed types and the changes are explained in CHANGELOG.

### 1.3 Population matched to GHS-POP, with day and night
**Data:** GHSL GHS-POP, latest epoch (e.g. 2025), 3 arc-second (~100 m) WGS84 tiles, from https://human-settlement.emergency.copernicus.eu/download.php (**VERIFY** the epoch and product name). The IIHS 2024 assessment found GHS-POP the most accurate gridded population dataset for India. WorldPop 100 m is an alternative for cross-checking.

**Method** (new `population.py`):
1. *Building capacity:* today's `occupants` (floor area × 0.8 / m² per person by use) is the **capacity** at typical occupancy. Rename it `capacity`; keep `occupants` as an alias for compatibility.
2. *Night (residential) population:* aggregate GHS-POP to 1 km cells to soften misalignment. For each cell, scale the residential capacity of buildings in it so that their sum equals the GHS-POP count: `night_occ = cap_res × (POP_cell / Σ cap_res_cell)`. Cap the scaling factor at [0.05, 3]; cells outside the range get flagged and fall back to the neighbourhood median factor. Non-residential buildings get a small night share (security and staff: 2% of capacity, a documented assumption).
3. *Day population:* workers and students fill non-residential buildings at a share of capacity. Residential buildings keep the share of people at home by day. Use HAZUS-style time-of-day splits (2 AM, 2 PM, 5 PM) from the Hazus 6.1 Earthquake Technical Manual, *Casualty* chapter (**VERIFY** table numbers). Keep the shares in `data/priors/occupancy.csv` with their sources.
4. Scenario input `time_of_day ∈ {night, day, commute}` goes into `/api/simulate` (default `night`, the conservative case for housing) and into the UI as a selector.
5. Check: print the total population in the area alongside the GHS-POP total and today's estimate.

Tests: the cell totals match GHS-POP after scaling; the day and night totals keep population consistent within a set tolerance; a missing raster falls back to capacity with a warning in the summary.

**Done when:** the Noida box's night total is within ±10% of the GHS-POP total for the box, and the summary panel shows the time of day used.

### 1.4 Checks and storey-height calibration
- Compare the Google-derived storeys with the 1,025 Noida buildings that have a `num_floors` or `height` label. Report the confusion matrix, the share within ±1 storey and the bias.
- Choose the storey height (2.8–3.5 m) that minimises bias. Record the result in `docs/validation.md#exposure`.
- Also spot-check 50 random unlabelled buildings against imagery (manual, documented). Optional but recommended.
- **Done when:** the storey-height choice is documented and ±1-storey agreement is reported (target ≥ 70%; if lower, document it and keep the `guess_unstable` safeguards).

### 1.5 ML for building attributes (only after 1.1–1.4; optional within v2)
**When to do it:** only if storeys or use class are still weak after 1.1–1.4.

**Labels:**
- OSM/Overture `class`, `building:levels` and `building:material` from **several Indian cities** (Delhi, Mumbai, Bengaluru, Ahmedabad, Chennai), to reduce the bias towards Noida's tall apartment blocks.
- The Nepal survey can't be used as labels (no footprints).

**Features:**
- footprint area, perimeter, compactness (4πA/P²), number of vertices, orientation, elongation;
- distance to the nearest road (Phase 5 road graph) and road class;
- building density and mean area within 100 m and 250 m;
- Google height and its spread;
- the GHS built-up fraction (optional).

**Model:** LightGBM or scikit-learn `HistGradientBoostingClassifier`, validated with **spatial** cross-validation (hold out whole 2 km tiles or whole cities). Output probabilities, which feed the type and use priors in 1.2 rather than replacing them.

**Done when:** it beats the 1.2 rule-based prior on the held-out log-loss. Otherwise don't ship it.

---

## 7. Phase 2: Shaking model (about 3 weeks)

### 2.1 Check BA08 against OpenQuake (v1.x #3, part 1)
- Create a separate env (OpenQuake supports specific Python versions, typically 3.10–3.12; **VERIFY**) and run `pip install openquake.engine`.
- `tools/verify_gmpe.py`: for M ∈ {5, 6, 7, 8}, Rjb ∈ {1, 5, 10, 20, 50, 100, 200} km, Vs30 ∈ {180, 250, 400, 760, 1100} m/s and mechanism unspecified, compare `gmpe.pga_g` with `openquake.hazardlib.gsim.boore_atkinson_2008.BooreAtkinson2008` (**VERIFY** the class path) for PGA. Note that `pga_g` adds `H` internally (r = √(R² + H²)), so pass Rjb as `dist_km`.
- Add `tests/test_gmpe_openquake.py`, which skips when openquake isn't importable and asserts a relative difference under 1%.
- Also take the BA08 standard deviations (between-event τ, within-event φ, total σ) for PGA, unspecified mechanism, from OpenQuake or BA08 Table 8. They are about τ ≈ 0.26 and φ ≈ 0.50 in natural-log units (**VERIFY**). Store them in `gmpe.py` for Phase 4.
- **Done when:** the test passes or each difference is explained and fixed.

### 2.2 Finite-fault distance
**Why:** BA08 (and BSSA14, and Sharma 2009) expect Rjb. Treating the quake as a point (hypocentral distance) underestimates shaking along large ruptures.

New `rupture.py`:
- Inputs: `lat, lon, depth_km, mag`, and optionally `strike, dip, rake`.
- **Known strike:** build a rectangular rupture.
  - Size from Wells & Coppersmith (1994), all slip types: `log10(L_km) = −2.44 + 0.59 M` (subsurface rupture length), `log10(W_km) = −1.01 + 0.32 M` (down-dip width) (**VERIFY** against the paper).
  - Clip W so the rupture stays within the seismogenic layer: `W ≤ (z_bottom − z_top) / sin(dip)` with z_top ≥ 0 and z_bottom = 20 km by default (a setting). If clipped, lengthen L to keep the area from `log10(A) = −3.49 + 0.91 M`.
  - Place the hypocentre at the centre of the rupture along strike and at 60% down the width (a documented convention).
  - Compute the surface projection polygon in the area's UTM CRS, which the model already computes (`self.x`, `self.y`). Then `Rjb = shapely.distance(points, polygon)`, vectorised (about 0.1 s for 483k points), and 0 for sites inside the projection.
- **Unknown strike** (the default UI case): use the USGS `ps2ff` approach, i.e. tables converting epicentral distance to the average Rjb over random strikes, by magnitude (https://github.com/usgs/ps2ff, **VERIFY** the package and table format). Ship the needed table as a small CSV in `data/priors/ps2ff_rjb.csv`, or compute it once with ps2ff and record which version was used.
- Below M 6, a point source is fine: Rjb ≈ epicentral distance, and depth enters through Ztor (top-of-rupture depth) only for models that use it.
- `Model.run(lat, lon, depth_km, mag, strike=None, dip=None, rake=None)`. Keep `mech` mapped from rake for BA08 (strike-slip, normal or reverse).
- UI: an optional "Fault direction (strike °)" field and "Dip °" under an "Advanced" toggle. With strike given, draw the rupture outline on the map.
- The README's "(so depth matters)" statement changes. Depth now acts through the rupture position (Ztor) and through models that use Rrup. Update the README.

Tests:
- Rjb is 0 above the rupture.
- Rjb grows away from the fault trace.
- For a small M, Rjb is close to epicentral distance.
- Strike rotation changes the PGA pattern as expected.
- The baselines change and the changes are explained.

**Done when:** an M7.5 scenario shows elongated shaking along the strike, and the README section 3 assumptions are updated.

### 2.3 Regional models for distant Himalayan events (v1.x #3, part 2)
**Context:** Delhi NCR is about 200–300 km from the Himalayan arc. BA08 is meant for distances up to about 200 km. Suitability studies rank models for North India by distance band (<100 km, 100–300 km, >300 km): Anbazhagan & Bajaj, Springer 2023; Selvan & Sinha 2024 (arXiv 2410.05918). Read both and choose the models and weights from their rankings.

Candidates available in OpenQuake hazardlib:
- `BooreEtAl2014` (BSSA14, NGA-West2, valid to about 400 km, has regional attenuation variants);
- `SharmaEtAl2009` (Indian Himalaya, uses Rjb, data from the Zagros and the Himalaya; check its distance range);
- the other NGA-West2 models (ASK14, CB14, CY14);
- `RaghukanthIyengar2007` is for *peninsular* India (stable continental), not the Himalaya.

**Design decision:** keep the app lightweight. **Port** the chosen models' PGA equations into a new `gmm.py` (one function per model, same signature as `pga_g`). **Verify each port against OpenQuake** in tests, as in 2.1. Don't make openquake a runtime dependency.

- `gmm.py` holds a registry `MODELS = {"BA08": ..., "BSSA14": ..., "SHARMA09": ...}` and `LOGIC = [(max_dist_km, {model: weight})...]`, chosen per distance band from the papers. Combine models on ln PGA (a weighted mean of the medians in log space). Combine standard deviations as √(Σw(σ² + (μ_i − μ̄)²)), which keeps the spread between models.
- `Model.run` calls `gmm.pga(mag, rjb, vs30, ztor, rake, region="himalaya" | "crustal")`. Pick the region automatically from where the epicentre is: a polygon of the Himalayan arc in `data/priors/regions.geojson`, documented.
- Settings: the weights table lives in `gmm.py` with citations.

**Done when:** every ported model matches OpenQuake within 1%; a Nepal-border scenario 250 km from Delhi produces documented PGA values compared with published intensity reports for a past event (2015 Gorkha intensities in Delhi were low, around MMI IV, **VERIFY**).

### 2.4 Soil (small improvement)
Keep the USGS Vs30 Mosaic. Add `vs30_src` to the summary. Optionally, an override layer: `data/rasters/vs30_override.tif` with local microzonation where it is available (the Delhi microzonation by NCS/MoES is **VERIFY** for availability). The override takes priority over the mosaic.

---

## 8. Phase 3: Calibrate fragility and validate (about 3 weeks)

### 3.1 Data
- **Nepal 2015 survey:** the household registration for housing reconstruction, a full count of damaged houses run by CBS with NPC. Sources: CBS/NSO microdata catalog 69 (https://microdata.nsonepal.gov.np/index.php/catalog/69, may need registration) or the Kaggle mirror "Earthquake magnitude damage and impact" (by arashnic). Building-level records carry IDs for district, VDC/municipality and ward, superstructure (materials), floors before the quake, age, plinth area and damage grade 1–5. **VERIFY:** the exact columns, whether the ward codes are pre- or post-2017, and which districts are covered (sources mention 11, 14 or 17 districts; Kathmandu valley appears to be excluded).
  - **Do not use** the DrivenData "Richter's Predictor" subset. Its geography IDs are obfuscated, so it can't be joined to shaking.
- **ShakeMap for Gorkha** (event us20002926): `grid.xml` (PGA in %g, so divide by 100; MMI; Vs30) and `uncertainty.xml` (the standard deviation of ln PGA), from https://earthquake.usgs.gov/earthquakes/eventpage/us20002926 (ShakeMap → Downloads).
- **Boundaries:** VDC/municipality and ward polygons matching the survey's coding, from HDX (search "Nepal administrative boundaries" for pre-2017 VDCs). If ward polygons are missing, use VDC level.

Write `tools/get_nepal_calibration.py` with written-down manual steps for any registration-gated download.

### 3.2 Joining shaking to buildings
- For each ward (or VDC), take the population-weighted mean of ShakeMap ln PGA over its polygon, using the GHS-POP grid for Nepal (avoid plain centroids, since much of the terrain is mountainous). Also record the spread of ln PGA within the ward, plus the ShakeMap σ.
- Output `data/calibration/nepal/ward_pga.parquet`.

### 3.3 Mapping classes and damage states
**Classes:**
| Nepal superstructure | Model class | Use for fitting? |
|---|---|---|
| Brick in cement mortar, ≤ 2 floors | URM (Indian urban brick analogue) | Yes |
| RC, non-engineered | RC_LOW | Yes (small sample) |
| RC, engineered | RC_LOW / RC_MID by floors | Yes (very small sample, wide CI) |
| Mud-mortar stone or brick, adobe, timber, bamboo | not used | No: much weaker than Indian urban stock |

**Damage states:** grade 1 → slight, 2 → moderate, 3 → extensive, 4 and 5 → complete. Test the alternative (5 alone = complete) as a sensitivity check and document it.

### 3.4 Fitting
- Likelihood per building class: for each building i with ward ln PGA x_i and observed state s_i,
  ```
  P(DS ≥ k | x) = Φ((x − ln θ_k) / β),   k = 1..4, with θ_1 < θ_2 < θ_3 < θ_4
  P(DS = s) = P(DS ≥ s) − P(DS ≥ s+1)
  ```
  Maximise Σ ln P(DS = s_i) with `scipy.optimize.minimize`. Parametrise the medians as ln θ_1 plus cumulative softplus increments so the order is guaranteed. Use a shared β per class.
- **Speed:** group the buildings by (class, ward) into counts per damage state, since ward PGA is shared. The likelihood is then a sum over groups weighted by counts, so 760k rows become a few thousand groups.
- **Shaking uncertainty:** inflate β with a measurement-error correction (fit β_total, then report β_capacity = √(β_total² − σ_IM²), using the ward's ShakeMap σ and spread). Or integrate over x numerically: Gauss–Hermite with 7 points per group. Prefer the integration and document the choice.
- **Confidence:** bootstrap over wards (resample wards, 200 times) to get confidence intervals for θ_k and β. Save them as `data/calibration/nepal/fit_<class>.json`.
- **Plots:** empirical exceedance by PGA bin against the fitted curves, saved to `docs/calibration/*.png`.

### 3.5 Comparison and choosing the final curves
- Compare with the GEM global vulnerability model (https://github.com/gem/global_vulnerability_model; the taxonomy mapping CSVs give India's building classes, e.g. masonry with clay brick in cement mortar, and non-ductile RC with infill; **VERIFY** the class strings). If GEM provides vulnerability (loss-ratio) functions rather than fragility, compare the implied damage at matching PGA. Also compare with HAZUS curves for similar classes (Hazus 6.1 technical manual).
- **Final rule:** use the Nepal fit where the bootstrap CI is narrow (for example, θ_3 CI width under 50% of θ_3). Otherwise use a weighted combination with GEM or HAZUS, documented per class in `docs/calibration/README.md`.
- Update `FRAG` and `BETA` in `simulate.py` with comments pointing to the docs, and keep the bootstrap samples for Phase 4.
- Update `test_fragility_ordering`. Note that the existing test asserts URM medians below RC_LOW; keep it only if the data supports it.

### 3.6 Validation (v2 "validate by replaying a past earthquake")
Keep validation data **separate** from calibration data.
1. **Held-out Nepal districts:** fit on all districts except 3, predict the damage-grade distribution in the held-out ones, and report log-loss, Brier score and calibration plots.
2. **Kathmandu valley 2015, urban:** Copernicus EMS rapid-mapping damage grading for the Nepal activation (**VERIFY** that the activation code is EMSR125). Use Overture footprints, the Gorkha ShakeMap and the full pipeline (Phases 1–2 building data where data exists). Match EMS damaged/destroyed points or polygons to buildings, then compute AUC for P(extensive or worse) and the ranking of 500 m cells.
3. **Türkiye 2023 (Kahramanmaraş M7.8),** urban, a different building stock: Copernicus EMSR648 grading vectors (about 4,042 buildings or blocks flagged in its areas), Overture buildings for Antakya and Kahramanmaraş, and the USGS ShakeMap (event id us6000jllz, **VERIFY**). Report the ranking skill only (AUC, top-cell hit rate), since absolute counts aren't comparable.
4. Write everything up in `docs/validation.md` with tables and maps, and add a "Validation" section to the README.

**Done when:** the curves are fitted with CIs and documented; validation results are reported, whatever they turn out to be, with honest limitations.

---

## 9. Phase 4: Uncertainty ranges (about 1 week)

**Sources of uncertainty, sampled per run:**
1. **Between-event term:** one value per realisation, η ~ N(0, τ²), added to ln PGA everywhere.
2. **Within-event field, spatially correlated:** Jayaram & Baker (2009), ρ(h) = exp(−3h / b). For PGA, b ≈ 8.5 km when Vs30 is spatially clustered or about 40.7 km when it isn't (**VERIFY**). Use b = 8.5 km for city areas with variable soil and expose it as a setting.
   - Generate the field on the existing 500 m cell grid (a few thousand cells): build the covariance φ²·ρ(h) and factorise it once per area with Cholesky (cache it). Above 6,000 cells, use FFT-based circulant embedding or a coarser 1 km grid.
   - Assign each building its cell value. Bilinear interpolation between cell centres keeps the field smooth.
3. **Multi-model spread** from Phase 2.3 (included in σ).
4. **Fragility parameters:** draw (θ, β) from the Phase 3 bootstrap samples, one draw per realisation per class.
5. **Construction type:** draw each building's type from p_type (Phase 1.2) per realisation, or keep the expected mix. Drawing is more correct for totals. Use a fixed seed for reproducibility.
6. **Occupancy:** a multiplicative lognormal on the scaling factor per area (σ from the Phase 1.3 check spread).

**Engine** (new `uncertainty.py`):
- `run_mc(model, scenario, n=200, seed=...)` runs as a background job (Phase 0.5).
- It keeps running sums per building (P(extensive or worse), P(collapse)), per-cell people-at-risk samples and the full distribution of the totals. Use memory-friendly loops over realisations with float32 arrays.
- Target: under 60 s for Noida with n = 200. If it's slower, compute damage once per (cell, type, Vs30 bin) group with shared PGA. Only do that if tests show the results match the per-building computation within 1%.

**Outputs:**
- Summary: the expected values (from the fast deterministic run) **and** the 10th, 50th and 90th percentiles of people at risk, expected collapses and buildings with moderate-or-worse damage.
- A worst-case priority list: the top 8 by 90th-percentile people at risk.
- Per building: P(extensive or worse) as a mean over realisations, and the 90th percentile in the popup ("chance of heavy damage 12% (up to 25% in a bad case)").

**UI:**
- After **Run simulation**, the deterministic result shows immediately as today. Then "Computing uncertainty… 35%" appears and the ranges and a worst-case toggle are added when it finishes.
- `makePng` includes the ranges in the footer.

**API:** `POST /api/simulate` gains `uncertainty: true|false` (default true) and returns `job`; add `GET /api/uncertainty/{run}`.

Tests:
- With all σ = 0 and n = 10, MC equals the deterministic result.
- Fixed seeds give identical results.
- The width of the distribution grows with τ.
- Sample spatial correlation from generated fields matches ρ(h) within tolerance.

**Done when:** ranges show in the UI for Noida within 60 s and their meaning is described in the README.

---

## 10. Phase 5: Responder layer (about 4 weeks)

### 5.1 Facilities
- Sources: OSM through Overpass for small area queries (amenity = hospital, clinic, doctors; emergency = ambulance_station; amenity = fire_station, police; emergency = assembly_point; amenity = shelter; leisure = park and landuse = recreation_ground as open-space shelters; amenity = school as possible shelters). Also Overture **places** with categories hospital, fire_station and police (download like buildings, theme `places`).
- New `facilities.py`: deduplicate OSM against Overture (within 50 m and with a similar name), store `data/facilities/<area_id>.parquet` with `kind, name, lon, lat, source, beds (if tagged)`.
- **Facility damage:** link each facility to the building footprint containing it (spatial join). Facility functionality = 1 − P(extensive or worse) of that building. Below 0.5, mark it "may not be working".
- Map layer: icons per kind, grey when it may not be working. The popup shows its damage chance.

### 5.2 Road network
- `roads.py`: OSMnx `graph_from_bbox` with `network_type="drive"`, padded by 2 km around the area, cached as `data/roads/<area_id>.graphml`. Add travel speeds and times with `ox.add_edge_speeds` and `ox.add_edge_travel_times`; use India-appropriate default speeds per road class in a documented table.
- Alternative: Overture `transportation` segments. Use OSMnx for its graph tools unless OSM coverage is poor.
- Street width: the OSM `width` tag if present, otherwise defaults per road class and lane count (`data/priors/street_width.csv`, documented).

### 5.3 Chance of road blocking from debris
For each road edge e with width W_e, take the buildings within a search buffer (30 m):
```
debris reach of building i:  D_i = c · H_i     (H_i = levels × storey height; c ≈ 0.5 for collapse, documented)
setback s_i = distance from footprint to edge centreline − W_e/2
P(i blocks e) = P_collapse_i · 1[D_i ≥ s_i + W_e·f]   (f = share of width that must be covered to block, e.g. 0.5–0.7)
             + P_extensive_i · g · 1[D_i/2 ≥ s_i + W_e·f]   (partial debris, g small)
P(e blocked) = 1 − Π_i (1 − P(i blocks e))
```
Calibrate the rule's form and constants from the literature:
- Yu & Gardoni 2022, road-blockage fragility from Haiti 2010 data, by building type, damage level and road features;
- the MDPI Applied Sciences 2020 road fragility model based on remaining free width (Amatrice case);
- the ISPRS 2018 Tokyo street-blockage probability model.

Record the chosen equations in `docs/roads.md`. Vectorise it: use STRtree queries from edges to buildings and numpy products in log space.

### 5.4 Routing, access and dispatch
- **Expected travel cost:** cost_e = t_e × (1 + λ·P(e blocked)) with a large λ (e.g. 20), or treat edges with P > 0.5 as closed. Better: Monte Carlo over closures (sample each edge as open or closed, 50 samples) to get P(reachable) and the distribution of travel times. Make that a background job, cached per run.
- **Access map:** a multi-source Dijkstra from all working hospitals (`networkx.multi_source_dijkstra_path_length`) gives each node a time to the nearest hospital. Aggregate to 500 m cells and show it as a layer ("minutes to nearest working hospital") and in the priority list.
- **Routes:** `GET /api/route?from=<facility_id>&to=<cell_rank>` returns a GeoJSON line, the travel time, the chance the route is open and a list of risky segments. The front end draws the line and the popup.
- **Dispatch order:** given N teams per facility kind (a UI input), assign teams to priority cells by maximising people-at-risk reached per minute with `scipy.optimize.linear_sum_assignment` on cost = travel_time / people_at_risk. Output an ordered list, and show it in the sidebar and in the exported report.
- Performance: Noida's drive graph should have about 10⁵ edges (**VERIFY**). A Dijkstra pass takes about 1 s in NetworkX. If it's too slow, switch to `igraph` or `scipy.sparse.csgraph.dijkstra`.

### 5.5 Casualties (v2 "better casualty estimates")
- HAZUS indoor casualty rates by structural damage state and severity (1–4, where 4 is deaths), with collapse fractions given complete damage per model building type. Take them from the Hazus 6.1 Earthquake Technical Manual, casualty chapter (**VERIFY** table numbers and values; record them in `data/priors/casualty_rates.csv`). Map our classes: URM → HAZUS URML/URMM, RC_LOW → C3L, RC_MID → C3M (**VERIFY** the mapping).
- `casualty.py`: expected casualties per severity = Σ_buildings occupants(time of day) × Σ_ds P(ds) × rate(ds, severity, type), with the collapse case split out.
- **Sanity check:** USGS PAGER empirical fatality model for India (Jaiswal & Wald, OF 2009-1136; India parameters θ, β are in the report, **VERIFY**): fatality rate = Φ(ln(MMI/θ)/β), applied to population exposed per MMI level. Show the PAGER estimate next to ours. A large gap gets flagged in the summary.
- The summary shows casualties by severity with Phase 4 ranges, for the chosen time of day.

**Phase 5 done when:** facilities, access times, routes and the dispatch list work for Noida; the blockage and casualty methods are documented with sources; tests cover the blockage maths, routing on a toy graph and the casualty maths.

---

## 11. Phases 6–8 (v3 and extras), plus a minimum viable path

### Phase 6: Platform (about 2–3 weeks)
- **6.1 Vector tiles for 3D (v1.x #4):** MapLibre already cuts GeoJSON into tiles in the browser (geojson-vt). Move that to the server without loss: serve per-run vector tiles at `/tiles/{run}/{z}/{x}/{y}.pbf`.
  - Geometry tiles are made once per area with **tippecanoe** settings that match today's look: `--no-simplification-of-shared-nodes`, extent 8192 to match MapLibre's GeoJSON extent, `-zg` or max zoom 16, and no feature dropping at zoom ≥ 15. Use the PMTiles format.
  - Per-run attributes (cat, pc) join through feature-state, or through a small per-run attribute tile with the same ids.
  - Compare the rendering before and after with screenshots (pixel diff) to prove there is no quality loss.
  - A pure-Python MVT encoder was measured at 0.1–0.3 s per tile; precompute or cache, never encode tiles per request.
- **6.2 Multiple cities:** builds on the area registry (0.4). Add an area switcher in the UI. Make the Vs30 mosaic and GHS-POP global; Google heights load per area.
- **6.3 Responder report export:** `GET /api/report/{run}.pdf` and `.csv`. The PDF has the scenario, summary with ranges, the priority table with names, access times and dispatch order, a map image (reuse `makePng` logic server-side or accept the browser PNG), the methods and caveats, and attribution. Use `reportlab` or HTML to PDF with `weasyprint`; choose one and record it.
- **6.4 Confidence display:** badges per building (storeys measured or estimated, type probability), per cell (data completeness), and a global "model confidence" panel listing validation scores from `docs/validation.md`.
- **6.5 Fast approximate model:** only if Phase 4 Monte Carlo is too slow in practice. Build it from per-cell, per-type look-up tables of P(ds) against ln PGA, which is exact for lognormal curves. That's a refactor, not ML.

### Phase 7: Hazard plug-ins (about 3–4 weeks)
- **7.1 Interface:** `hazards/base.py`
  ```python
  class Hazard:
      name: str
      def intensity(self, model, params) -> dict[str, np.ndarray]   # per building, e.g. {"pga_g": ...} or {"depth_m": ...}
      def damage(self, model, intensity) -> np.ndarray               # (n, 4) exceedance probs
      def describe(self) -> dict                                     # UI inputs, legend, units
  ```
  Move the earthquake code into `hazards/earthquake.py` without changing behaviour (baselines must match exactly). `Model.run(hazard, params)` delegates; the building data, aggregation, responder layer and UI are shared.
- **7.2 Flood:** intensity = water depth at each building from a raster, either a user-uploaded scenario depth GeoTIFF or a return-period map (JRC/Copernicus global flood hazard maps, **VERIFY** the source and resolution). Damage: JRC global depth-damage functions (Huizinga et al. 2017, EUR 28552 EN, Asia residential, commercial and industrial curves), converted to damage states via loss-ratio thresholds (documented). The UI adds a flood scenario selector.
- **7.3 Cyclone:** a wind field from a parametric model (Holland 1980) given the track, central pressure and radius of maximum wind; gust at the building with a terrain roughness factor. Damage: HAZUS hurricane-style wind fragility for the closest building classes (**VERIFY** availability; otherwise document approximate curves as placeholders, the way the v1 earthquake curves were).
- **Done when:** the earthquake behaviour is unchanged (exact baseline match) and the flood module works end to end on a test raster.

### Phase 8: Visual extras (about 1 week)
- **Seismic wave animation:** expanding P (≈ 6.0 km/s) and S (≈ 3.5 km/s) rings from the hypocentre (using hypocentral distance). Per cell: S-arrival time minus (P detection time + alert delay, a setting of e.g. 5 s) = warning seconds. Show a "warning time" layer and a play button. Use `requestAnimationFrame` plus a MapLibre circle layer or a canvas overlay.
- **Dark theme:** CSS tokens already exist on `:root`. Add a dark map style (darker raster tint) and a toggle stored in `localStorage`.
- **Shake effect:** a CSS transform jitter scaled to the MMI at the map centre, short and off by default (an accessibility setting).

### Minimum viable path (if there is a deadline)
In priority order: 0.1–0.3, then 1.1 (Google heights), 1.3 (population), 2.1 (OpenQuake check), 2.2 (finite fault), 3.1–3.5 (calibration), 4 (uncertainty, simple version without fragility sampling), then 5.1 + 5.4 access map. About 8–10 weeks.

---

## 12. Decisions for the owner

| ID | Question | Recommendation |
|---|---|---|
| D1 | Keep two identical READMEs (root and `quake-map/disaster/`)? | Keep the full one in `quake-map/disaster/`; make the root README a short pointer to it, to avoid drift |
| D2 | Distant 3D buildings: keep rounding (fast) or go back to `set_precision` (exactly as before, about +1 s in 3D)? | Owner prefers no quality loss, so go back to `set_precision` unless the owner accepts the measured ~1 m difference |
| D3 | OpenQuake as a runtime dependency or only for tests? | Tests only; port equations (Phase 2.3) |
| D4 | Default time of day for scenarios | Night (conservative for housing); show the selector prominently |
| D5 | Licence constraints (GEM non-commercial etc.) | Decide on intended use (research or commercial) before Phase 3.5 |
| D6 | Report format: PDF library | `weasyprint` (HTML templates, easy to style) |

---

## 13. Progress checklist

Status on 2026-11-21 (updated after the real-data pass). `[x]` done and tested; `[~]` built and tested but a stated part is missing or a target was missed (see note); `[ ]` not done.
Details: `quake-map/disaster/CHANGELOG.md`, `docs/validation.md`, `docs/calibration/README.md`.

Phase 0
- [x] 0.1 git init + .gitignore + baseline commit
- [x] 0.2 server starts without data
- [x] 0.3 baseline harness + synthetic regression test
- [x] 0.4 area registry + clip rule + release check
- [x] 0.5 background jobs + progress UI
- [x] 0.6 region pre-download (`tools/prefetch_region.py`; run on central Delhi, 0.35 x 0.30 deg: 1,214,387 buildings, 22.8 million people scaled to GHS-POP, storeys from Google heights (`--method overview`; the default `nearest` would take hours at this size), under a memory cap; now also fetches heights and population, which it silently skipped before. The plan's 1 x 0.8 deg example was not run: about 14x Noida's memory)

Phase 1
- [~] 1.1 Google 2.5D heights -> storeys, `levels_src`. **Target missed:** 66.9% measured in Noida (> 80% wanted; 81.8% in the baseline box). Cause: 30% of footprints read < 2 m
- [x] 1.2 construction-type probabilities + mixed damage curves + per-type beta. Census 2011 urban wall and roof shares are now real (India, Delhi, Uttar Pradesh rows, from the MoHUA/NBO compendium); caveat: concrete roofs are lumped with metal sheets, so the roof share is an upper bound. Areas built earlier keep the old prior until rebuilt
- [~] 1.3 GHS-POP matching + day/night occupancy + time-of-day input. Night total within 2.2% of GHS-POP. **Time-of-day shares follow Hazus Table 12-2 where it has a value (commercial, industrial, education, hotels); residential day/evening, retail, medical, religious are judgements**
- [~] 1.4 exposure checks + storey-height calibration (2.8 m). **+/-1 storey agreement 68.8% (target 70% missed)**; imagery spot-check: tool and a 50-building sheet are built (`tools/spotcheck_storeys.py`), **the looking has to be done by a person**
- [~] 1.5 ML attributes: **built and shipped as a satellite-supervised storey model** (a label-trained model passed the plan's test but was biased); extrapolates below the training height range, no construction-material labels, no road features. docs/ml_attributes.md

Phase 2
- [x] 2.1 BA08 matches OpenQuake (machine precision) + standard deviations stored. (Found and fixed a v1 normal/reverse offset swap)
- [x] 2.2 finite-fault Rjb (known strike) + ps2ff (unknown strike) + UI
- [~] 2.3 ported regional models (BSSA14, Sharma 2009; each < 1% of OpenQuake) + weighted mix by distance band. **Weights from one paper's IGP/Himalaya scores; Sharma's 0.2 is a judgement; Anbazhagan & Bajaj 2023 is paywalled and not read.** Gorkha-in-Delhi check done (docs/validation_gorkha_delhi.md): **the mix under-predicts at ~700 km by about 3 intensity units** (MMI 1.0 versus DYFI 4.0; beyond 400 km the mix is BSSA14 alone); damage results unaffected (PGA that small harms nothing), far-field PGA/MMI maps unreliable
- [x] 2.4 Vs30 override layer

Phase 3
- [x] 3.1 downloads via scripts: ShakeMaps, EMS grading and (new) the Nepal building table (an open mirror of the derived table, not the official file), HRRP ward polygons, GEM files, Gorkha DYFI. The official survey still needs registration (NSO catalog 69)
- [~] 3.2 ward PGA + sigma: run on 900 real wards (94.9% of buildings); **ward means are not population-weighted** (no Nepal GHS-POP fetched)
- [x] 3.3 class and damage-state mapping documented (column names to verify against the real data dictionary)
- [x] 3.4 maximum-likelihood fit run on the real survey (untruncated, undamaged buildings counted as state 0; the earlier assumption that the survey lists damaged houses only was wrong). **Result: URM fit has held-out skill 0.03, RC_LOW and RC_MID degenerate; nothing applied** (docs/calibration/README.md)
- [~] 3.5 GEM comparison done (docs/calibration/gem_comparison.md): our placeholders are far more fragile than GEM below 0.5 g. **No Hazus comparison** (Hazus structural fragility is in spectral displacement; no PGA table). No curve adopted: curves stay UNCALIBRATED placeholders, with the reasons recorded
- [~] 3.6 validation: Turkiye 2023 and Kathmandu replays (weak/inconclusive); 3.6.1 held-out districts now run on the real survey (districts 22, 29, 36 left out: URM skill 0.03, RC_LOW -5.98, RC_MID 0.03); the earlier leave-one-town-out EMS test is kept as a second check

Phase 4
- [x] uncertainty.py Monte Carlo (between-event, correlated within-event, models, fragility, type, occupancy). Fragility and occupancy spreads are assumptions
- [x] job + UI ranges + worst-case priorities + PNG footer

Phase 5
- [x] 5.1 facilities + facility damage
- [x] 5.2 road graph + widths (speeds and widths are assumed tables)
- [~] 5.3 debris blockage: rule and 3.5 m free-width criterion now from Anelli et al. (2020) (equations 2-11), by building type. **Direction weights (1/3 each) assumed; debris from the two sides is not added up (under-states narrow streets); not validated against observed blockages;** Yu & Gardoni 2022 not read (paywalled), the Tokyo model's constants are in inaccessible Japanese sources
- [x] 5.4 access map, routes, dispatch order
- [x] 5.5 casualties: Hazus 6.1 indoor rates per type (tables 12-3 to 12-8) and PAGER India parameters (theta 11.53, beta 0.14, checked against OFR 2009-1136; the earlier 14.05/0.185 were Indonesia's) now verified. US stock mapped by judgement to URML/C3L/C3M; outdoor casualties not modelled

Phase 6
- [x] 6.1 vector tiles with pixel-diff check (tippecanoe built from source; <= 1.7% pixel difference after edge tolerance; docs/tiles.md)
- [x] 6.2 multi-city switcher (registry + UI)
- [x] 6.3 responder report export (PDF via WeasyPrint, CSV)
- [x] 6.4 confidence display
- [x] 6.5 look-up-table fast model: not needed (Monte Carlo 26 s)

Phase 7
- [x] 7.1 hazard interface (earthquake refactor, exact baseline match)
- [x] 7.2 flood module end to end on a real JRC 100-year map (`tools/get_jrc_flood.py`); depth-damage curve is the JRC Huizinga 2017 Asia residential curve. **Caveat: the map floods 55% of Noida's buildings, almost certainly too many; absolute flood totals are not credible**
- [~] 7.3 cyclone module (**wind fragility is a placeholder**)

Phase 8
- [x] wave animation + warning times
- [x] dark theme
- [x] shake effect

## 14. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| Nepal survey ward codes don't match the available boundaries | Calibration only possible at VDC or district level (blurrier shaking) | Fall back to VDC level, widen the CIs, document it |
| Nepal survey has few RC buildings | Concrete curves weakly constrained | Blend with GEM/HAZUS per rule 3.5; show wide CIs |
| Google 2.5D heights noisy for small buildings | Wrong storeys for low-rise housing | Stability and size checks fall back to the heuristic; check against labels (1.4) |
| Census wall material can't separate RC frame from masonry | Type priors uncertain | Storeys dominate; carry the uncertainty in p_type into Phase 4 |
| GHS-POP misaligned with footprints | Population in the wrong buildings | Aggregate to 1 km before scaling; cap the factors |
| Regional model choice debated in the literature | Distant-event shaking uncertain | Weighted mix plus spread between models in σ; document the rankings used |
| OpenQuake version/API drift | Verification scripts break | Pin the OpenQuake version in `tools/requirements-verify.txt` |
| Licences (GEM non-commercial) | Limits on use | Decision D5; `docs/data_sources.md` |
| Monte Carlo too slow on big areas | Long waits | Background jobs, Cholesky caching, coarser correlation grid, look-up-table model (6.5) |
| Overture schema changes | Download breaks | `_read_columns` already falls back; pin the `overturemaps` version in requirements |

---

## 15. Reference sources (from the research; open these first)
- Nepal survey: NSO microdata catalog 69, https://microdata.nsonepal.gov.np/index.php/catalog/69 · KLL portal, https://kathmandulivinglabs.org/our-work/earthquake-data-portal · Stanford G-DIF (building-level data restricted), https://purl.stanford.edu/gn368cq4893
- USGS ShakeMap Gorkha: https://earthquake.usgs.gov/earthquakes/eventpage/us20002926
- GEM vulnerability: https://docs.openquake.org/vulnerability/ · repo https://github.com/gem/global_vulnerability_model
- GEM exposure: https://github.com/gem/global_exposure_model · South Asia docs https://docs.openquake.org/global_risk_model/exposure/South_Asia/index.html
- OpenQuake models: BSSA14 https://docs.openquake.org/old/oq-hazardlib/0.21/gsim/boore_2014.html · Sharma 2009 https://docs.openquake.org/old/oq-hazardlib/0.21/gsim/sharma_2009.html · correlation models https://docs.openquake.org/oq-engine/manual/master/_sources/api-reference/openquake.hazardlib.correlation_models.rst.txt
- Model suitability for North India: Anbazhagan & Bajaj https://link.springer.com/chapter/10.1007/978-981-19-3330-1_7 · Selvan & Sinha 2024 https://arxiv.org/html/2410.05918v1
- Google Open Buildings 2.5D Temporal: https://sites.research.google/gr/open-buildings/temporal · Earth Engine catalog https://developers.google.com/earth-engine/datasets/catalog/GOOGLE_Research_open-buildings-temporal_v1
- Census 2011 HL-03 roof/wall material: https://censusindia.gov.in/nada/index.php/catalog/9392
- Population: IIHS accuracy study https://iihs.co.in/knowledge-gateway/?p=21464 · WorldPop India https://hub.worldpop.org/geodata/summary?id=49804 · GHSL download https://human-settlement.emergency.copernicus.eu/download.php
- Road blockage: Yu & Gardoni 2022 https://ideas.repec.org/a/eee/reensy/v219y2022ics0951832021006980.html · MDPI 2020 https://www.mdpi.com/2076-3417/10/4/1289 · ISPRS 2018 https://isprs-archives.copernicus.org/articles/XLII-4/303/2018/isprs-archives-XLII-4-303-2018.pdf
- Validation: Copernicus EMS Türkiye https://e.copernicus.eu/CEMS_IB165 · QuickQuakeBuildings https://arxiv.org/html/2312.06587v1
- Casualties: PAGER empirical model https://pubs.usgs.gov/of/2009/1136 · Hazus 6.1 EQ technical manual https://www.fema.gov/sites/default/files/documents/fema_hazus-earthquake-model-technical-manual-6-1.pdf
- Flood damage: JRC global depth-damage functions https://publications.jrc.ec.europa.eu/repository/handle/JRC105688
- Finite-fault distances: USGS ps2ff https://github.com/usgs/ps2ff (**VERIFY**)
