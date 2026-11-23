# Changelog

Every change to a model number must be explained here (see MASTER_PLAN.md section 4).

## Unreleased (master-plan implementation)

### Interface pass (2026-11-23)
No model numbers change. The first command-centre layer was removed in favour of one placement for each thing: a **topbar** (name, scenario, four headline numbers,
Guided tour, Ask, Report, View, Dispatch), a **map toolbar** (3D, before/after, fly-through) as the only control over the map, **Compare** and **Aftershock** under
Run, **Ask** inline in the Summary tab, **Overlays** (uncertainty hatching, knock-on failures) in the Map tab, live phone, safe and SOS counts at the top of the
Dispatch panel with one primary action (Optimise all teams), and the demo clock inside Demo. The tour is a slim bar at the bottom instead of a floating panel.
Removed: gradients, glows, glass blur, uppercase tracked labels, glyph and emoji icons (now one 16 px line set), looping pulses (only calls waiting for help ring),
bars down the side of call rows. Follows the unslop and impeccable guidance: a 4 px grid, graphite ramp, two weights, sentence case, one primary action per region.
The phone page lost its gradient button, sonar rings and glow. Front end is in `ui/command.css` and `ui/command.js`.

### Command centre: AI report, assistant, optimiser, knock-on failures, compare, tour (2026-11-23)
No existing model number changes (all baseline tests are unchanged). New, all clearly labelled as assumptions where they are:
- **Aftershock** (`hazards/aftershock.py`, `Model.run_aftershock`, `POST /api/aftershock`): a second quake on the damage the first left. The
  aftershock's shaking is scaled by 1 + 0.6 x (expected damage state / 4) of each building (an assumed weakening), then its damage chances
  are combined with the earlier ones as 1 - (1 - p1)(1 - p2), which keeps the damage states in order. Totals are cumulative; the response
  says what the second shock added. Casualties of the second shock are added to the first's (slightly double counts people in the same buildings).
- **Knock-on failures** (`cascade.py`, `GET /api/cascade/{run}`): fires (ignition chance per heavily damaged or collapsed building, spread 1.2 m/min
  scaled by local density, stopping at 300 m), gas leaks (12% ignite later), power cuts per 500 m cell (lognormal in PGA, median 0.35 g, plus building
  damage) and bridge closures (OpenStreetMap bridges, lognormal in PGA, median 0.45 g). All rates are assumptions; results are seeded by the run.
- **Uncertainty hatching** (`GET /api/confmap/{run}`): squares of the heat grid ranked by the Monte Carlo spread of the chance of heavy damage.
- **Compare scenarios** (`POST /api/compare`): two earthquakes run on the same area; the scenario on the map is restored afterwards
  (`Model.snapshot_state` / `restore_state`).
- **Optimise all teams** (`POST /api/live/optimise`): one assignment of every free unit to every open call (Hungarian method on priority-weighted
  driving minutes, unserved calls charged 90 min) against the one-by-one nearest-unit baseline. Stations have assumed units (fire 2, ambulance 2, hospital 1,
  police 2) and a simulated fuel store; `GET /api/live/resources` shows the fleet, fuel and hospital beds against the seriously injured the model expects.
  Teams drive along their routes on a demo clock (`POST /api/live/speed`, default x10), on the dashboard and on the phone.
- **Situation report and assistant** (`insights.py`, `POST /api/brief/{run}`, `POST /api/assistant/{run}`): Claude writes from a facts sheet when
  `ANTHROPIC_API_KEY` is set and the `anthropic` package is installed (model `BRIEF_MODEL`, default `claude-opus-5-5`); otherwise, or if the call fails or is
  declined, a built-in writer and keyword answerer use the same facts. The facts sheet holds scenario numbers, place and facility names and counts only.
- **Real earthquake replays** (`presets.py`, `GET /api/presets`): Gorkha 2015, Türkiye 2023 and Bhuj 2001 on the loaded city, Gorkha at its real epicentre, a Himalayan M8 scenario.
- **Front end** (`ui/command.css`, `ui/command.js`): command-centre theme, KPI strip with count-ups and a citizen-mesh counter, glowing hotspots, pulsing SOS markers,
  animated route dashes, moving teams, before/after swipe, cinematic 3D tour, sound design, judge mode with a guided tour. Phone page: countdown ring,
  haptics, sonar SOS button, team driving to the phone with a live countdown. Tests: `tests/test_features.py` (29).

### Live demo fixes (2026-11-22)
No model numbers change. A phone that reconnected or reloaded heard the last alert again (each alert now reaches a phone once; ids stay
unique across restarts). An open SOS now follows its phone when it moves more than 15 m. After a server restart a phone re-sends its open SOS
with the same details instead of dropping it. Phone location pings no longer rebuild the dispatch list and details (open sections closed and a
click on Send could be lost). Team suggestions that said "load the streets first" are asked for again once they are loaded. "I'm safe" with an
open SOS offers to cancel it; an SOS the server rejects is not retried forever; the team route is zoomed to once, not on every update. UI:
"now ago", long notes, the alert form squeezing the list, toasts instead of browser alerts, a narrower panel on mid-size screens, the demo
card fades, the phone link is https only when the proxy runs, and the phone keeps its screen awake where the browser allows.

### Dispatch panel, one-tap SOS, phone GPS over https (2026-11-22)
No model numbers change. The live tools moved out of the left panel into a **Dispatch** panel on the right: calls one per line under Open /
En route / Done, a detail view with suggested teams (nearest working fire, ambulance/hospital and police stations by driving time around likely
debris, `GET /api/live/sos/{id}/options`; `assign` takes the chosen `facility_id`), alert and phone-link tools behind buttons. On the phone, SOS
is sent with one tap (details are optional afterwards), location is asked for on opening, a phone far from the area is moved into it
automatically for the demo, and placing yourself by hand is only a fallback. `tools/https_proxy.py` serves the app over https on the local
network (self-signed certificate) so phones share GPS.

### Live demo: phone page, alerts, SOS, safe-place routes (2026-11-22)
No model numbers change. New `live.py` and `sos.html`, and a **Live: phones and SOS** section on the dashboard (README 6.4.1). Phones that keep
`/sos` open get alerts sent to a circle on the map, send SOS calls that appear on the dashboard ranked by who is in trouble and the model's
damage at that building, mark themselves safe, and get a walking route to the nearest working open space or shelter that avoids streets
likely blocked by debris (plus a Google Maps link). Responders send the nearest working team along the existing debris-aware driving route; the
phone sees the team and its ETA. In-memory only, no accounts, no push or SMS: a demo of the idea. Tests: `tests/test_live.py`.

### Panel, heat map, 409 after restarts, storeys, downloads (2026-11-22, tester feedback)
Model numbers change in one place: the storey rules below (exposure method version 4 -> 5, so saved areas are rebuilt from their raw footprints the first time they are opened).
- **Storeys.** (1) A mapped floor count or height of 8 storeys or more is checked against the satellite height. If they differ by more than 2x and
  the satellite height is at least 6 m, the satellite value is used (`levels_src = google_over_label`, counted as measured). Example: a tower mapped
  as 80 floors / 300 m that the satellite sees at 94 m. (2) No building is taller than 6 times the side of its footprint (floor of 3 storeys).
  (3) 3D extrusion uses 2.8 m per storey like the model (it was 3 m). Effect on Noida (483k buildings): 86 buildings change storeys (25 rejected labels,
  61 by the shape cap), people in the area -0.08%. On the baseline area, 2 labels are rejected. This is a small correction, not a new model: the largest
  single-building occupancy there (8,428 people) is a tower the satellite does see at 33 storeys, capped at 4,000 people by capacity and scaled by the cell's
  population factor of 2.1; it may still be a tower that is not finished, which no data in the app can tell.
- **Route and dispatch 409.** Reproduced: it happens after a server restart (`uvicorn --reload` restarts on every saved .py file), when the open
  page still shows Route buttons. The server now rebuilds the responder layer from the cached facilities and roads (about 3 s) instead of answering
  "Prepare ... first"; a lost simulation answers 409 "Run the simulation again"; route, dispatch and access carry the page's run number so a stale page
  is told so. Errors are typed (no more guessing the status from the words in the message): not found 404, dispatch with no usable facility 422.
  The page shows the reason where the user is looking and recovers (an unprepared server hides the responder tools again).
- **Heat map.** The yellow cloud (blurred discs coloured by each 500 m cell's mean damage state, which sits in the middle everywhere) is replaced by a
  heat map on a 100-500 m grid (about 12,000 squares per area): mean chance of heavy damage on an absolute scale (green 0% to red 60% or more), or people
  at risk relative to the busiest places, switched in the legend. It fades out as individual buildings appear. The 500 m cells remain for priorities,
  routes and dispatch. New summary figure "Buildings likely heavily damaged" (sum of P(extensive or worse)); the old "Severe damage" count (expected
  damage state at least 2.5) read 0 next to 1,905 expected collapses and moved to "More numbers".
- **Panel.** Drag the edge to resize (300 px to 60% of the window, remembered, double-click resets, arrow keys work), a button hides it; every group folds
  and remembers its state; the summary keeps the five or six figures people act on with ranges beneath them, the rest folds; tabs and priority rows no
  longer truncate text; long explanations became tooltips. Everything that explains colours (damage, buildings, facilities with on/off switches,
  access minutes, route, warning time) is one legend on the map showing only what is on screen.
- **Dark mode.** The dark panel no longer darkens the street map; the street map has its own Light/Dark switch (dimmed OpenStreetMap tiles; dark tile
  services now require an API key).
- **Overlapping popups.** One popup at a time; a facility dot wins over the building under it; priority pins no longer open their own popup but go
  through the same manager and sit under it; pins that collide at a zoom level are fanned out around their true place. Clinics, shelters, schools and
  open spaces start hidden (switchable in the legend).
- **"Finding hospitals, fire stations and shelters" took too long.** Measured on Delhi core (34 x 33 km, never prepared before): the OpenStreetMap
  facility query 34 s, the Overture places read 31 s (one request at a time), and the road network download on top, all one after the other.
  Now the facilities and the places run at the same time and the street query is cut into tiles of about 10 km fetched two at a time (a single query
  over a 25 x 26 km city failed on two public servers after 1 and 6 minutes). Nothing is downloaded unless the button is pressed; an automatic
  background fetch was tried and removed because the street files of a big city are hundreds of MB.

- **3D pins and orbit.** At a low viewing angle the round priority pin sat on the ground point but looked like it hung on a tower's wall. Pins are now
  teardrops whose tip is the exact spot (checked at pitch 60 and 75, also while turning), re-fanned after tilt and rotation. New **Orbit view** (Map tab):
  turns the 3D view around the middle of the map at 3, 6 or 12 degrees a second (tilts into 3D first); dragging the map, Esc, "Back to 2D", or loading a
  new area stops it, zooming does not.
- **Downloads.** Overture row groups are fetched 8 at a time (6 parts: 11.3 s -> 6.5 s; `OVERTURE_THREADS` changes it), streets and facilities of a freshly
  downloaded area are fetched in the background so "Show hospitals" finds them cached, and the road and facility downloads fall back to two more public
  Overpass mirrors. Rotating IP addresses to get around the Overpass limits is deliberately not done: it breaks their usage policy and the slow part is
  the Overture reads, which have no per-address limit.

### Route 409, loading time, side panel (2026-11-21, tester feedback)
No model numbers change (Monte Carlo output is bit-identical for any thread count; blockage is identical with the cached street matching).
- **Route no longer fails with 409.** `/api/route` and `/api/dispatch` refused ("Compute access for this simulation first.") until
  "Estimate hospital access" had been pressed for that very run, but the Route buttons appeared as soon as facilities loaded. Access is now
  computed on demand (`Layer.ensure_access`, one computation per run shared by concurrent callers), and the page starts it itself with progress
  ("Estimating hospital access … first") before routing or dispatching.
- **Faster:** "Show hospitals, fire stations and roads" on a cached area 33 s -> 2.5 s on Noida (the built road graph is kept as
  `data/roads/<area>.net.pkl`, the street-building matching for debris blockage as `<area>.streets.npz`; both rebuild when their inputs change).
  Access for each later run 8.4 s -> 1.0 s (the matching was redone every run). Uncertainty ranges 30 s -> 23 s (realisations on 2 threads,
  random draws kept in the original order). Uncertainty and tile cutting run in their own job lanes, so responder and access jobs no longer queue
  behind them.
- **Side panel:** results are split into tabs (Summary, Priorities, Responders, Map), so turning tools on no longer makes one 4,500 px column.
  The legend moved onto the map (with facility colours once responders are shown; folded by default on phones); priority rows are one name line
  plus one number line with a small Route button, and the route picker and route result sit next to them; the dispatch table is a readable
  list; routine data notes fold away under "Data sources and caveats" while real warnings stay visible; a dot marks a tab with new results.
  Unnamed facilities read "unnamed fire station", not "fire_station".

### Bug, UI and performance pass (2026-11-21)
No model numbers change (people-at-risk ranges are bit-identical; casualty totals in the Monte Carlo differ only by summation order).
- **Monte Carlo 60 s -> 31 s on Noida (483k buildings).** Each realisation evaluated every fragility curve twice (damage and casualties) and
  did per-type masked indexing; with one sampled type per building it now gathers each building's curve and evaluates once
  (`simulate.exceedance`, `casualty.casualties_from`, `casualty.totals_one_type`; equivalence test in `tests/test_casualty.py`).
- A new simulation (or an area switch) cancels the previous run's Monte Carlo instead of letting stale jobs occupy both workers (`Job.cancel`).
- Memory no longer grows with the session: report images (several MB each), dispatch lists, access results and job ids keep only the newest runs.
  Flood uploads stream to disk (were read whole into memory, up to 300 MB) under a `.part` name so the raster list never shows half a file.
- Place names: failed lookups are remembered for 10 minutes and an unreachable service for 1 minute, so a report built offline no longer
  waits up to 8 x 6 s. The tile reader cache is thread-safe. "Nearest facility" routes use one multi-source Dijkstra.
- Report: no longer crashes when a dispatched team has no open route; title and distance column follow the hazard (was always "Earthquake ... km
  from epicentre"); flood reports show the raster name, not the server path, and no fake 0, 0 location; scenario text is escaped once, not twice.
- Page: job polling stops with a message when the server forgets a job (used to loop forever); validation errors are readable ("Magnitude: ...")
  and empty inputs are caught before sending (an empty box became 0); the flood file is uploaded once, not on every run; access circles, routes,
  wave rings and warning colours of the previous run are cleared, and switching area resets the responder panel (old hospitals and Route
  buttons stayed and then failed); building popups name the hazard of the run on the map, and only one is open at a time; Enter runs the
  scenario, Esc cancels epicentre picking; the theme is applied before first paint and outlines and rupture lines turn light in dark mode;
  popup arrows on left/right-anchored popups and the popup close button (styled as a full-width button) are fixed; the saved map image uses the
  page font and no longer says "peak MMI undefined" for cyclones and floods; the report's map image is capped at 2400 px.

### Phase 0: foundations
- git repository created; first commit is the v1.x baseline after performance fixes 1-4.
- Server starts without data (`/api/meta` returns `empty: true`; data endpoints return 409 "Load an area first.").
  `QUAKE_DATA_DIR` and `DEFAULT_CENTER` environment variables added. `app.create_app(data_dir)` factory.
- Area registry (`areas.py`): `data/areas/<id>/{raw,exposure}.parquet + meta.json`. Covered boxes are cut from saved raw
  footprints with Overture's own bbox rule; a newer Overture release is offered, never fetched silently.
- Background jobs (`jobs.py`): `POST /api/area` returns a job id; the page polls and shows "Downloading n/N parts".
- Baseline harness (`tools/baseline.py`) with 3 fixed scenarios on a 6 x 5 km Noida box; deterministic synthetic fixture.
- Decision D2: distant 3D buildings use `shapely.set_precision` again (identical to v1.0, no visual change).
- Soil raster is looked up in `data/rasters/` first. The USGS ScienceBase host blocks scripted downloads;
  `tools/get_vs30.py` documents the manual step. Without the file the model uses flat 250 m/s and says so.

**Reference numbers (v1.x model, flat soil, Overture 2026-11-18.0, baseline box, 32,400 buildings)** are stored in
`tests/baselines/*.json` in the first commit that contains them; later phases must explain every change from them.

### Real-data pass (2026-11-21): inputs that were placeholders because data was "unreachable"
Most of the "blocked" data turned out to be reachable: Python's urllib is answered with 403 by some hosts where curl is not, FEMA and
MDPI refuse scripts but the Internet Archive serves their files, and the Nepal survey table exists as an open mirror. Changes, with
their effect on the three baseline scenarios (flat soil, so the soil change is shown separately):
- **Casualty rates** (`priors/casualty_rates.csv`, now per building type: URM -> Hazus URML, RC_LOW -> C3L, RC_MID -> C3M) and collapse
  fractions are the Hazus 6.1 values (tables 12-3 to 12-8) instead of recalled numbers. Effect: deaths change by < 1% (a 454 vs 451, b 2,178
  vs 2,170, c 175 vs 173; collapse cases dominate and they were already right), injuries rise (severity 1: +32% a, +24% b, +35% c; severity 2:
  +37%, +15%, +66%) because URM has far higher moderate/extensive injury rates than RC. `casualty.load()` now returns a dict of per-type arrays.
- **PAGER India parameters**: theta 11.53, beta 0.14 (USGS OFR 2009-1136 country table; the old 14.05 / 0.185 were Indonesia's). PAGER deaths
  rise 2.0x to 4.3x (a 118 -> 327, b 1,304 -> 5,607, c 31 -> 61); the ratio of our deaths to PAGER becomes 1.39 / 0.39 / 2.86 (was
  3.83 / 1.66 / 5.63) and the false "disagreement" flag on scenario c is gone. For the M7.5 scenario our deaths are now 2.6x **below** PAGER.
- **Occupancy shares** (`priors/occupancy.csv`) follow Hazus Table 12-2 where it gives a value: office night 0.02 / day 0.97 / evening 0.49,
  industrial 0.10 / 0.72 / 0.45, hotel 0.999 / 0.19 / 0.299, education 0 / 0.72 / 0.10 (warehouse treated as industrial). Other rows are
  now labelled judgement. Effect only on areas built from now on (the exposure caches night/day/commute people).
- **Census prior** (`priors/census_materials.csv`, `construction_mix.csv`): real Census 2011 urban shares (India: walls 0.848, roof 0.691;
  Delhi 0.959 / 0.702; Uttar Pradesh 0.879 / 0.352). 1-2 storey URM share for the default region 0.525 -> 0.555; 3-4 storeys RC_LOW
  0.600 -> 0.691. Cached areas keep the prior they were built with until rebuilt.
- **Soil**: the real USGS Vs30 grid is installed (`tools/get_vs30.py` now downloads it). Noida median Vs30 229 m/s (was flat 250). Effect on
  the baseline scenarios: people at risk -3.5% (a), -8.2% (b), +0.4% (c); expected collapses -5.1%, -13.4%, +0.5%; median PGA 0.264 -> 0.260,
  0.496 -> 0.461 (b). Baselines in `tests/baselines/` were regenerated **with flat soil** (VS30_TIF set to a missing file) so they do not
  depend on the 621 MB raster.
- **Flood**: depth-damage curve is the JRC Huizinga et al. (2017) Asia residential curve (was the Europe curve under a placeholder label).
  `tools/get_jrc_flood.py` fetches a JRC return-period map. On the 100-year map 55% of Noida's buildings are flooded: almost certainly too many.
- **Debris blockage** (`roads.py`): replaced the placeholder reach/width rule by the Anelli et al. (2020) criterion (street unusable when under
  3.5 m stays free) and debris-width equations by building type; `blockage()` takes `p_masonry`. Blockage probabilities rise for low-rise
  buildings (a 3-storey masonry building can now block a 6 m street from 1 m away; it could not before). Direction weights are assumed.
- **Fragility**: the Nepal fit was run on real data and **not applied** (see docs/calibration/README.md): undamaged buildings are now counted
  as state 0 and the likelihood is untruncated; degenerate fits are flagged and a fit needs held-out skill >= 0.10. `tools/compare_gem.py`.
- **Gorkha in Delhi** (`tools/validate_gorkha_delhi.py`): the ground-motion mix predicts MMI 1.0 at ~700 km against DYFI 4.0. Not re-tuned.
- `tools/prefetch_region.py` now also downloads heights and population (it previously built the exposure without them), and was run on
  central Delhi (1,214,387 buildings). `tools/spotcheck_storeys.py` prepares the manual storey spot-check.
- Tests: 141 -> 146 passed (casualty rates per type, JRC flood curve, Anelli equations; road tests rewritten for the new rule).

### Frontend polish (2026-11-21, `index.html`)
Done with the unslop skill (CC BY 4.0, Damir Sokolovsky, github.com/heydamir/unslop) in polish mode: the page had craft debt, not AI tells.
- Design tokens on a 4px grid, graphite neutrals in light and dark, IBM Plex Sans with IBM Plex Mono for coordinates and figures, weights 400/500 only,
  no chrome accent (the damage colours are the only colour). One primary button, secondary buttons on a raised surface with hairline borders,
  hover and visible focus states, custom select and disclosure arrows, quieter popups and pins (neutral, not red).
- Results first: the scenario form is one `<details>` that folds to a readout ("M6.5, 15 km, night") after a run; Summary (with people at risk as a
  large figure), legend and priorities now come before export, waves and responders. No feature or ID changed.
- **Map damage layer**: the density heatmap is replaced by soft discs coloured by each cell's own mean damage state. The heatmap showed how many cells
  overlapped, so a 1.2-million-building scenario with 0 severe buildings drew a solid red slab. It now shows the real range (green to yellow here).
- Progress bars no longer animate width; reduced-motion removes transitions. No test depends on the markup.

### Phase 1: building data
- **Storeys** come from Google Open Buildings 2.5D Temporal heights (median over 2021-2023 of the zonal median inside each
  footprint, `heights.py`), then `num_floors`/height labels, then the old heuristic. New column `levels_src`
  (label, label_height, google, guess, guess_unstable). Heights with spread > 6 m or < 2 m are not trusted.
  Noida (483,358 buildings): 66.7% google + 0.2% labelled = 66.9% measured (the plan's 80% target is NOT met; the 6 x 5 km
  baseline box reaches 81.8%). 30% of Noida's footprints read < 2 m in the height product (62% of footprints < 20 m2, 47% of
  20-50 m2) and fall back to the heuristic by design; only 2.7% fail on year-to-year spread. See docs/validation.md. Storey height is 2.8 m (was 3.0 for height labels).
- **Construction type is now a probability mix** (`p_URM, p_RC_LOW, p_RC_MID`, `priors.py`). Damage is the mix of the three
  curves. One-hot weights reproduce v1 exactly (tests/test_regression.py passed unchanged at that step).
  The default census shares are an ASSUMPTION (priors/census_materials.csv), not looked up from the census tables.
- **People**: `capacity` (v1's typical occupancy) is scaled so each 1 km cell matches GHS-POP R2023A (epoch 2025); night /
  day / commute totals (`occupants`, `occ_day`, `occ_commute`); `time_of_day` is a scenario input. Noida box: v1 put 9.1 M
  people in the box, GHS-POP has 4.9 M; the new night total is within 2.2% of GHS-POP.
- Area exposure rebuilds automatically when `exposure.METHOD_VERSION` changes or a raster is added.

### Phase 2: shaking
- BA08 verified against OpenQuake to machine precision. **Bug fixed:** v1 had the normal and reverse mechanism offsets
  swapped (not used by the app, which always used "unspecified").
- Finite-fault distance (`rupture.py`): Wells & Coppersmith (1994) rupture, exact Rjb; unknown strike uses the USGS ps2ff
  table (public domain, copied into priors/). Depth acts through Ztor: r_eff = sqrt(Rjb^2 + Ztor^2).
- Regional mix (`gmm.py`): BA08 + BSSA14 (+ Sharma 2009 near the source in the Himalayan region), ported and checked
  against OpenQuake (< 1%). Weights from Selvan & Sinha (2024) IGP/Himalaya LLH scores; Sharma's 0.2 is a judgement.
- Optional local Vs30 override raster.

**Baseline changes vs v1.x (6 x 5 km Noida box, 32,400 buildings, flat soil):**

| scenario | max PGA g | buildings moderate+ | expected collapses | people at risk |
|---|---|---|---|---|
| a M6.5, 15 km, centre | 0.194 -> 0.264 | 21,758 -> 32,063 | 931 -> 2,017 | 54,935 -> 115,209 |
| b M7.5, 10 km | 0.288 -> 0.497 | 21,804 -> 32,400 | 3,289 -> 9,280 | 149,963 -> 302,755 |
| c M7.5, 50 km north | 0.155 -> 0.207 | 0 -> 0 | 348 -> 784 | 24,950 -> 60,130 |

Why: (1) distance now enters through Ztor (M6.5 at 15 km depth: ~8.9 km instead of the 15 km hypocentral distance), so PGA
near the source is higher; (2) types are mixed, so some "masonry" buildings are now concrete and vice versa; (3) people use
GHS-POP (the box total fell from ~771k capacity to ~527k). These are a *calibration gap*, not a finding: the fragility
curves are still the v1 placeholders until Phase 3.

### Phase 3: calibration and validation
- Fragility curves now load from `priors/fragility.json` (status: UNCALIBRATED placeholder). `calibration.py` + tools implement the full
  fitting chain, tested on synthetic survey data; the real Nepal survey was not obtainable, so no curve was fitted (docs/calibration/README.md).
- Event replays with real data (docs/validation.md): weak/inconclusive skill, reported as such.

### Phase 4: uncertainty ranges
- `uncertainty.py`: seeded Monte Carlo (200 runs, 26 s for 483k Noida buildings): between-event term, spatially correlated within-event field,
  fragility draws, construction-type draws, occupancy factor. `POST /api/simulate` returns `uncertainty_job`; `GET /api/uncertainty/{run}`.
  UI: ranges, worst-case priorities, popup "up to x% in a bad case", PNG footer. Fragility (+-25%) and occupancy (+-20%) spreads are assumptions.

### Phase 5: responder layer
- Facilities (OSM + Overture, linked to building damage), road network + debris-blockage model, Monte Carlo access to working hospitals,
  routes with chance-open, dispatch by linear assignment (`facilities.py`, `roads.py`, `responders.py`, `responder_layer.py`, docs/roads.md).
  Endpoints: `/api/responders/prepare`, `/api/facilities`, `/api/access`, `/api/route`, `/api/dispatch`. Debris constants, speeds and widths are
  assumptions (flagged in the UI).
- Casualties by severity (`casualty.py`) with a PAGER cross-check; **rates are unverified placeholders**, flagged in the summary and UI.
  Monte Carlo ranges include deaths and injuries.

### Phase 6: platform
- Report export: `GET /api/report/{run}.pdf` (WeasyPrint) and `.csv`; the browser sends its map image first. Scenario, summary with ranges, priorities, access,
  dispatch order, methods and caveats (every placeholder named), attribution. 6.4: "How much to trust this" panel from live status flags + recorded replay scores
  (`/api/confidence`), storey completeness per priority area. 6.2: the area registry and switcher (Phase 0) cover multiple cities.
- **6.1 vector tiles NOT implemented**: tippecanoe is not installable here, and per-run attributes cannot join onto static tiles without per-feature `feature-state`
  calls at 483k features, which would be slower than the current lossless GeoJSON path. **6.5 not needed**: the Monte Carlo takes 26 s.

### Phase 7: hazard plug-ins
- `hazards/` interface (`intensity`, `damage`, `describe` + small hooks); the earthquake moved into `hazards/earthquake.py` with **identical numbers** (the saved baselines changed only
  by new summary keys: 66 insertions, 0 deletions). `Model.run_hazard(hazard, params)`. Flood (raster water depth, placeholder depth-damage curve, upload endpoint) and
  cyclone (Holland 1980 wind field along a track, placeholder wind fragility; 2.5 s for 483k buildings). Monte Carlo and casualties exist for earthquakes only.

### Phase 8: visual extras
- Seismic-wave animation (P ~6, S ~3.5 km/s from the hypocentre) with a timeline and a warning-time layer (S arrival minus P-detection time at a sensor + alert delay,
  both settings); dark theme (remembered); shake effect (off by default, short, scaled to peak MMI, disabled for reduced-motion users).

### Documentation
- README rewritten to match reality (placeholder / unverified status table); docs/data_sources.md; decision D1 applied (root README is a pointer); D2 applied (set_precision).

### 1.5, 6.1 and Nepal hold-out (follow-up)
- **6.1 vector tiles done** (docs/tiles.md): tippecanoe built from source, static PMTiles per area, per-run attributes as arrays in the style expression (string `slice` was 10x
  slower). Pixel difference vs GeoJSON <= 1.7% after a 1-pixel edge tolerance; first 3D view 5.5 MB -> ~0.1 MB.
- **1.5 ML storeys done** (docs/ml_attributes.md): a model trained on mapped floor labels passed the plan's test (9/9 cities) but over-predicted storeys on ordinary buildings
  (3.0 vs 2.2), so a satellite-supervised model (learns from usable Google heights, footprint + context features) was shipped instead (unbiased, 8/9 cities, +0.064 log-loss).
  `exposure.METHOD_VERSION` 4; baseline change: damage counts < 1%, people at risk +2.7%, baseline-box night population 527k -> 541k (GHS-POP 500k).
- **Held-out Nepal**: the survey stays unobtainable; a leave-one-town-out test on EMS gradings of 6 towns shows no transferable skill (docs/validation.md).
