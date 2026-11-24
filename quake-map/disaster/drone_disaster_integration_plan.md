# Drone × Disaster Simulation: Integration Plan

| | |
|---|---|
| **Audience** | Claude (Claude Code) implementing the merge, plus the project owner reviewing it |
| **Host project** | Disaster-Simulation (`quake-map/disaster`), FastAPI + MapLibre dashboard |
| **Guest project** | Drone-SLAM, **DroneForest (terrain)** part only. SplatDrone and asteroid code are out of scope |
| **Status** | Design complete, **nothing built yet** |
| **Written** | 2026-11-24 |

> **Read this first.** This plan was written from the two READMEs and a screenshot, **not from the source code**. Everything marked `[VERIFY]` is an assumption that Phase 0 must confirm or correct before any code is written. Where reality differs from this plan, record the difference in `docs/integration/PLAN_DELTAS.md` instead of silently deviating.

---

## Table of contents

0. [How to use this document](#0-how-to-use-this-document)
1. [Vision, principles and scope](#1-vision-principles-and-scope)
2. [What exists today and where the two projects clash](#2-what-exists-today-and-where-the-two-projects-clash)
3. [Target architecture](#3-target-architecture)
4. [Coordinate frames and georeferencing](#4-coordinate-frames-and-georeferencing)
5. [Shared state and data model](#5-shared-state-and-data-model)
6. [Drone protocol (messages between app, worker and browser)](#6-drone-protocol)
7. [Drone worker design](#7-drone-worker-design)
8. [World-on-demand: the city as the drone sees it](#8-world-on-demand)
9. [Damage truth, survivors and detection](#9-damage-truth-survivors-and-detection)
10. [Energy model, flight time and the clock](#10-energy-model-flight-time-and-the-clock)
11. [Flight control: auto, assisted, manual](#11-flight-control)
12. [Priority engine and sortie planner](#12-priority-engine-and-sortie-planner)
13. [Scan state and coverage](#13-scan-state-and-coverage)
14. [Dispatch integration](#14-dispatch-integration)
15. [Feedback into the damage model](#15-feedback-into-the-damage-model)
16. [Frontend: map layers and the corner window](#16-frontend)
17. [API reference](#17-api-reference)
18. [Configuration](#18-configuration)
19. [State machines and edge cases](#19-state-machines-and-edge-cases)
20. [Testing strategy](#20-testing-strategy)
21. [Performance budgets](#21-performance-budgets)
22. [Risks and mitigations](#22-risks-and-mitigations)
23. [Phased roadmap with tasks and acceptance criteria](#23-phased-roadmap)
24. [Decisions already taken (defaults)](#24-decisions-already-taken)
25. [Definition of done](#25-definition-of-done)
26. [Appendix A: kickoff prompt for Claude Code](#appendix-a-kickoff-prompt)
27. [Appendix B: example messages](#appendix-b-example-messages)
28. [Appendix C: glossary](#appendix-c-glossary)

---

## 0. How to use this document

### 0.1 Reading order

1. Read sections 1 to 3 completely. They define what is being built and why.
2. Run **Phase 0 (discovery)** from section 23 before touching any code. It produces `docs/integration/DISCOVERY.md`, which answers every `[VERIFY]` item.
3. Build **one phase at a time**. Stop at the end of each phase, run its acceptance checks, and report before starting the next.

### 0.2 Working agreements

- **Disaster app is the host.** Its existing behaviour must not regress. The existing test suite (about 170 tests, `pytest -q`) must keep passing after every phase.
- **The drone module is optional.** With `DRONE_ENABLED=0`, or with PyTorch not installed, the disaster app must start and behave exactly as before. No drone UI appears.
- **Small, reviewable changes.** Prefer new files under `drone/` and thin hook points in existing files over rewriting existing files.
- **Report changed files only.** When reporting progress, list just the files that changed. Do not create zip archives or README files unless the owner asks.
- **Never claim something works unless it was run.** State the exact commands run and their outcome.
- **Declare simplifications.** Anything deliberately unrealistic gets a `# SIMPLIFICATION:` comment in code and a line in `docs/integration/SIMPLIFICATIONS.md`. Only **flight time** is meant to be realistic (section 10). Everything else may be simplified, but must be declared.
- **Take defaults, don't interrupt.** Section 24 lists decisions already made. Use them. Ask the owner only if blocked, and log any new decision in `docs/integration/DECISIONS.md`.
- **Match the existing style.** Follow the disaster project's code style, test layout and naming. Inspect it first in Phase 0.

### 0.3 Documents Claude should create and maintain

| File | Purpose |
|---|---|
| `docs/integration/DISCOVERY.md` | Phase 0 findings: real file names, function names, data shapes |
| `docs/integration/PLAN_DELTAS.md` | Every place the build differs from this plan, with reason |
| `docs/integration/DECISIONS.md` | New decisions made during the build |
| `docs/integration/SIMPLIFICATIONS.md` | Every deliberate unrealism, with where it lives in code |
| `docs/integration/DEMO_SCRIPT.md` | Click-by-click demo for each milestone |

---

## 1. Vision, principles and scope

### 1.1 Vision

The disaster dashboard answers *"where is the damage, who is at risk, and how do responders get there?"* from a model. The drone answers *"what is actually there?"* from observation. Merged, the two form a loop:

```
 model says: these neighbourhoods are the worst
      │
      ▼
 drone flies the highest-value areas first (or the operator flies it)
      │
      ▼
 drone observes: collapsed buildings, blocked roads, people
      │
      ▼
 observations become dispatch calls and correct the model
      │
      ▼
 priorities, routes and casualty estimates update; the drone goes to the next best place
```

To the user this must feel like **one product**: one map, one dispatch panel, one clock. The drone is a responder unit with a small camera window, not a second application.

### 1.2 Design principles

| # | Principle | Consequence |
|---|---|---|
| P1 | **The disaster app is the host** | Drone code plugs into it. The drone's own viewer is not shipped as a page |
| P2 | **The map carries the information; the corner window carries the drone's eye** | Coverage, victims, roads and drone position are drawn on the main map. The window shows the drone's camera view and a few controls |
| P3 | **The drone is a responder unit** | It appears in Dispatch like a fire or ambulance team, with status, home station, and ETA |
| P4 | **Only flight time is realistic** | Battery, endurance, reserve, reachability and sorties are modelled carefully. Sensing, damage observation and so on are simplified |
| P5 | **One source of truth, one clock, one frame** | A single incident state, a single simulation clock, a single georeferenced frame |
| P6 | **Modular on both sides** | Backend: the drone module can be off. Frontend: the corner window is a registry of small modules |
| P7 | **Deterministic and reproducible** | Seeds everywhere. Same scenario plus same seed gives the same hidden truth and the same flight |
| P8 | **Protocol first** | The worker and the app talk only through a documented protocol. A real drone could replace the simulated one later |
| P9 | **Honesty about validity** | The "truth" the drone discovers is sampled from the project's own model. In simulation this validates the *workflow*, not the fragility curves. The UI and docs must say so |

### 1.3 In scope (the build) and later

The numbering below (F1 to F40) is the feature list agreed with the owner. Phases in section 23 reference it.

| ID | Feature | Phase |
|---|---|---|
| **Core integration** | | |
| F1 | Drone is a unit in the Dispatch panel, with status, battery, home station | 1 |
| F2 | Drones launch only from police stations; a station in a likely-damaged building cannot launch | 1 |
| F3 | One shared incident state, one shared clock (pause and speed), one georeferenced frame | 1 |
| F4 | Drone sim runs as a separate worker process, streaming over a websocket | 1 |
| F5 | A defined drone message protocol so a real drone could plug in later | 1 |
| F6 | Drone's local world built on demand from footprints and the scenario's damage, deterministic from the seed | 3 |
| **Flight time (the realistic part)** | | |
| F7 | Battery and energy model: roughly 25 to 35 min real flight, return-to-home reserve | 1 (simple), 3 (physics-driven) |
| F8 | Round-trip reachability: each station's drone shows a reachable area; out-of-range targets are greyed | 2 |
| F9 | Sortie planning: launch, survey, return, fitted to the energy budget | 5 |
| F10 | Battery swap turnaround between sorties, limited spare batteries per station | 1 |
| **Control** | | |
| F11 | Auto mode: drone works down the priority queue | 5 |
| F12 | Assisted mode: click a building, road or neighbourhood, or draw an area | 4 |
| F13 | Manual mode: keyboard or gamepad steers; existing flyer handles avoidance | 4 |
| F14 | Take over and release at any time; auto resumes from the best remaining target | 4 |
| F15 | Battery and return-to-home warning in manual mode | 4 |
| **Priority engine** | | |
| F16 | Score targets by expected people trapped, collapse chance, remaining uncertainty, per unit of flight energy | 5 |
| F17 | Visible, editable queue: reorder, pin, skip, with the reason for each ranking | 5 |
| F18 | Pending SOS calls jump the queue | 5 |
| **On the main map** | | |
| F19 | Drone icon, trail, camera cone | 2 |
| F20 | Scan state per building (unscanned, partial, scanned); unscanned areas shaded | 6 |
| F21 | Drone-verified damage drawn differently from modelled damage | 6 |
| F22 | Victim pins; road segments marked blocked from drone observation | 6 |
| F23 | Scan percentage on each priority neighbourhood | 6 |
| F24 | "Send drone" in building popups; "Verify with drone" on phone SOS calls | 4 (send), 6 (verify) |
| **Detections to dispatch** | | |
| F25 | Drone detections become calls (source: drone), ranked with phone SOS calls | 6 |
| F26 | Duplicate detections merge into one call | 6 |
| F27 | Confirm or reject step before a detection is dispatched | 6 |
| F28 | Existing dispatch sends the nearest working team on the debris-aware road graph | 6 |
| **Feedback into the damage model** | | |
| F29 | Observed buildings update strongly, neighbours weakly | 7 |
| F30 | Casualty ranges, priority neighbourhoods and hospital access recompute | 7 |
| F31 | Drone-observed debris edits the road graph and reroutes teams | 7 |
| **Corner window** | | |
| F32 | Small, draggable, snapping to corners, click to enlarge or swap with the map | 2 |
| F33 | Default: live chase view plus one-line status bar with mode toggle and battery | 2 |
| F34 | Optional modules: depth camera, controls pad, telemetry, task queue, mapping scorecard | 2 to 5 |
| F35 | Low resolution and frame rate; pauses when hidden | 2 |
| **Later (not part of the first build)** | | |
| F36 | Multiple drones working across stations | 8+ |
| F37 | Footprint-aided drift correction | 8+ |
| F38 | Evaluation harness: time-to-find-victims with and without drones, on seeded scenarios | 8+ |
| F39 | RL training on city worlds with rubble | 8+ |
| F40 | Exports: PDF report section, victims as GeoJSON, point cloud as .ply | 8 |

### 1.4 Out of scope

- Real drone hardware and flight-controller integration (the protocol must allow it later; nothing more).
- Regulatory modelling (airspace rules, licences). A single optional geofence is enough.
- Cyclone and flood hazards in the first build. The drone module should read generic per-building damage states so these can be added later, but only earthquakes are tested.
- SplatDrone, asteroid modules, RL training, and the drone's own standalone viewer pages.
- A mobile or phone UI for the drone. Phones keep only the existing SOS page.
- Realistic sensor physics, realistic aftershocks, realistic wire and cable hazards, realistic communication links. All simplified or absent (declared in `SIMPLIFICATIONS.md`).

### 1.5 Milestones at a glance

| Milestone | End of phase | What the owner can demo |
|---|---|---|
| **M0** | 0 | Written findings; plan corrected against real code |
| **M1** | 2 | A drone unit sits at each police station in Dispatch, launches, flies a simple route with a real battery curve, shows in a corner window and on the map, with reachability areas |
| **M2** | 4 | Same, but flying through a generated city chunk with rubble; operator can fly manually or click a target; return-to-home works |
| **M3** | 5 | Auto mode: the drone chooses and flies sorties by priority; the queue is visible and editable |
| **M4** | 6 | Scanning marks buildings; the drone finds people; detections appear as dispatchable calls |
| **M5** | 7 | Observations update the heat map, casualty numbers and routes, with a Modelled vs Drone-updated comparison |
| **M6** | 8 | Polish, fast-forward, exports, documentation |

---

## 2. What exists today and where the two projects clash

### 2.1 Disaster-Simulation (host)

From the README (verify against the code):

| Area | What the README says |
|---|---|
| Entry | `quake-map/disaster/app.py` (FastAPI), `index.html` (the whole front end, MapLibre), run with `uvicorn app:app` |
| Model | `simulate.py` (hazard, damage, impact), `hazards/` plug-ins, `uncertainty.py` (200 Monte Carlo runs) |
| Responders | `responder_layer.py` (facilities, roads, access, routes, dispatch order) |
| Live demo | `live.py`, `sos.html`: phone alerts, SOS calls, Dispatch panel; state **in memory**, open SOS calls survive restart |
| Phones | `tools/https_proxy.py` serves `/sos` over https on port 8443 |
| Data | Overture/OSM footprints (Noida: 483,358 buildings), Google Open Buildings heights, GHS-POP, USGS Vs30 |
| Output | 500 m cells heat map, 8 priority neighbourhoods, building popups, PDF/CSV export |
| Tiles | optional static vector tiles (tippecanoe) for large areas |
| Tests | about 170 pytest tests, no network needed |
| Facilities | hospitals, fire stations, **police stations**, shelters; greyed out when their building is likely damaged |

`[VERIFY]` items for Phase 0 (record the real answers in `DISCOVERY.md`):

1. How police stations are stored and exposed (fields, API route, id scheme, link to a building id).
2. How a Dispatch **unit/team** is modelled in `live.py` (class or dict, fields, status values, how ETA progress is advanced: wall clock or something else).
3. How a **call** is modelled (fields, ranking function, how phone SOS calls are created, how the Dispatch panel renders them).
4. Per-building data available after a run: id, footprint, storeys/height, occupants (by time of day), damage probabilities, collapse probability, construction type probabilities. In what arrays or files, and how they are cached.
5. Whether the 200 Monte Carlo realisations are stored per building, or only summaries.
6. How the 500 m cell aggregation, priority neighbourhoods and casualty totals are computed, and whether they can be **recomputed from modified per-building probabilities without re-running the hazard**.
7. The road graph structure in `responder_layer.py`: node and edge ids, blockage probabilities, how routing uses them, and whether edges can be overridden.
8. How `index.html` is organised (one script block? ES modules? globals?), how the map object is exposed, how the right-hand Dispatch panel is mounted, how 3D buildings are drawn (fill-extrusion on a vector tile source?).
9. How static files are served (is there a static mount, or is everything inline?).
10. Whether the Dispatch/clock logic has any notion of simulation time or only wall-clock time.
11. Whether `tools/https_proxy.py` forwards websocket upgrades. (Phones do not need the drone websocket, so this is informational.)
12. Python version, dependency pinning, how optional dependencies are handled (the drone module must be optional).
13. How areas are saved and switched, and what event the front end receives when the area changes.
14. Existing test fixtures that can be reused for a tiny synthetic city.

### 2.2 DroneForest (guest, terrain part)

From the README (verify against the code):

| Module | Role |
|---|---|
| `terrain.py` | analytic heightfield (same maths in torch and the viewer's JS) |
| `dynamics.py` | batched quadrotor, collective-thrust + body-rate actions, inner rate loop |
| `world.py` | per-env forest and disaster zone, batched raycasts, collisions; axis-aligned boxes with slab raycasts |
| `env.py` | 97-d observations, reward, curriculum, scan-gated imitation reward |
| `heuristic.py` | hand-written reference flyer |
| `mission.py` | long procedural mission flown by the heuristic at a **moving look-ahead waypoint**, with wind and INS drift |
| `rollout.py` | exports `rollout.json` for the viewer |
| `viewer/` | three.js viewer: voxel SLAM map, mesh, scorecard, minimap, survivor detection with pins |

Known limitations stated by its README: heuristic success on dense disaster arenas is modest; box obstacles are axis-aligned; the policy observes an 80-ray scan, not depth images; the SLAM map is a voxel occupancy map built from the drifting pose.

`[VERIFY]` items:

1. Real dynamics parameters (mass, thrust, drag, time constants, simulation `dt`, control rate). Can the physics be re-parameterised to a ~3 to 4 kg airframe?
2. The exact interface of `heuristic.py`: inputs (state, scan, waypoint), outputs (action), and where avoidance happens.
3. How `mission.py` represents the look-ahead waypoint and whether an external controller can drive it.
4. How `world.py` stores boxes (tensors), whether per-env worlds are batched, and the cost of raycasts as the box count grows.
5. How survivor detection works in the viewer and in `mission.py` (server side or viewer side).
6. How the voxel SLAM map is built (server side in Python or in the viewer JS). This decides where scan state is computed.
7. Torch version, CUDA use, behaviour on CPU, memory use. The owner's machine is a laptop with a 4 GB RTX 3050.
8. Whether anything in DroneForest depends on `terrain.py` heightfield in a way that blocks a flat ground plane.

### 2.3 Mismatches and how this plan resolves them

| Topic | Disaster app | DroneForest | Resolution |
|---|---|---|---|
| Coordinates | WGS84 lon/lat | local metres, z up | Local ENU frame per area, converted in `drone/georef.py` (section 4) |
| Buildings | polygon footprints, up to ~480k | axis-aligned boxes in procedural arenas | Oriented boxes from footprints, streamed in chunks (section 8) |
| Damage | probabilities per building | binary geometry (a wall exists or is rubble) | Sample **one** hidden outcome per building from the probabilities (section 9) |
| Ground | real city, treated as flat | analytic heightfield | Flat ground at z = 0 (declared simplification) |
| Time | mostly static scenario, live demo runs in wall time | real-time physics | One shared clock with pause and speed (section 10) |
| Runtime | FastAPI + one HTML file | torch + standalone three.js viewer | Drone sim in a separate worker; viewer reduced to an embeddable component |
| Dependencies | no torch | torch required | Drone extras are optional; app runs without them |
| State | in memory in `live.py` | in the sim process | Single incident state in the app, worker is stateless apart from physics |
| Scale | 30 km box | arenas of a few hundred metres | Chunk the world around the drone; never load the whole city into the raycaster |
| Obstacle handling | n/a | avoidance quality is modest in dense scenes | Fly above roof level by default; safety filter in manual mode (section 11) |

---

## 3. Target architecture

### 3.1 Process and component diagram

```
┌─────────────────────────────────── Browser (laptop) ─────────────────────────────────────┐
│  index.html (existing)                                                                    │
│   ├─ MapLibre map  ── layers: heat map (existing), buildings, stations, calls (existing)  │
│   │                    + NEW drone layers: drone, trail, cone, scan state, victims,       │
│   │                          blocked roads, reach circles                                 │
│   ├─ Dispatch panel (existing) ── + drone units, + drone calls, + task queue              │
│   └─ Corner window (NEW)  ── registry of modules: live view, depth cam, controls, ...     │
│                                                                                           │
│  drone-api.js ──── REST (commands, queries) ───────┐                                      │
│                └── WebSocket /ws/drone (events) ───┤                                      │
└────────────────────────────────────────────────────┼──────────────────────────────────────┘
                                                     │
┌──────────────────────── FastAPI app (existing app.py, one process) ──────────────────────┐
│  existing: simulate, responder_layer, live, report, uncertainty                           │
│  NEW drone/ package (no torch import here):                                               │
│    bridge.py   router, websocket hub, worker supervisor                                   │
│    state.py    incident store (drones, tasks, sorties, detections, scan state)            │
│    clock.py    simulation clock                                                           │
│    priority.py / planner.py / energy.py / feedback.py / scan.py / truth.py / georef.py    │
│    stations.py adapters to responder_layer and live.py                                    │
└───────────────────────────────▲───────────────────────────────────────────────────────────┘
                                │ WebSocket /ws/worker (worker is the client)
┌───────────────────────────────┴───── Drone worker (separate process) ────────────────────┐
│  python -m drone.worker                                                                   │
│    fleet loop at fixed dt  ──  DroneBackend (kinematic L0 now, physics+lidar L1 later)    │
│    chunk world builder (footprints → oriented boxes + rubble)                             │
│    flyer (waypoint follower + avoidance + safety filter)                                  │
│    lidar raycasts, scan events, detections                                                │
│  imports torch (optional); owns the GPU                                                   │
└───────────────────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Process responsibilities

| Process | Owns | Must not |
|---|---|---|
| **Browser** | Rendering, operator input, window layout | Hold authoritative state; compute priorities |
| **FastAPI app** | The **incident state** (single source of truth), clock, priority engine, sortie planner, dispatch integration, feedback model, persistence | Import torch; run physics |
| **Drone worker** | Physics, lidar, flyer, chunk world, the instantaneous drone state | Decide tasks; talk to browsers; touch dispatch |

The worker receives **commands** (launch, goto, survey this polygon, manual input, return home, pause) and emits **events** (telemetry, scan events, detections, sortie milestones). It holds no mission logic, so it can be restarted at any time.

### 3.3 Why the worker connects to the app (and not the reverse)

- One origin for the browser, so no CORS or extra ports.
- The worker can later run on another machine (for example, a GPU box) by changing one URL.
- If the worker crashes, the app notices the closed socket, marks drone units `offline`, and shows "drone link lost". When the worker reconnects, the app re-sends the fleet and the world.

### 3.4 Directory layout (new files in bold)

```
quake-map/disaster/
  app.py                       existing; add router include and lifespan hook only
  live.py                      existing; extend units and calls (section 14)
  responder_layer.py           existing; add station accessor + road edge override hook
  simulate.py                  existing; expose per-building arrays + recompute entry point
  index.html                   existing; add hook points only (section 16)
  **web/drone/**               front-end modules (ES modules, vendored three.js)
    drone-module.js            init(map, api), module registry, window manager
    drone-api.js               REST + websocket client
    drone-window.js            corner window (drag, snap, swap, modules)
    drone-view.js              three.js chase view
    drone-layers.js            MapLibre sources and layers
    drone-controls.js          keyboard and gamepad input
    modules/                   live-view, depth-cam, telemetry, controls-pad, task-queue, scorecard
    vendor/three.module.js     vendored three.js
  **drone/**                   Python package
    __init__.py
    config.py                  all tunables (section 18)
    protocol.py                pydantic models + JSON Schema export (section 6)
    georef.py                  WGS84 <-> local ENU, chunk frames
    clock.py                   simulation clock
    state.py                   incident store, event bus, persistence
    energy.py                  pure-Python energy model (shared by planner and sim)
    stations.py                adapters to responder_layer and live.py
    truth.py                   sampled hidden damage truth + survivors
    scan.py                    scan grid, building scan state, observed damage
    priority.py                scoring
    planner.py                 sortie planner, survey path generator
    feedback.py                Bayesian-style update of damage probabilities
    bridge.py                  FastAPI router, websocket hub, worker supervisor
    worker/
      __main__.py              entry point: python -m drone.worker
      loop.py                  fleet loop, command handling, event emission
      backend.py               DroneBackend interface
      kinematic.py             L0 stand-in drone (Phase 1)
      physics.py               L1 physics + lidar drone (Phase 3)
    sim/                       vendored and adapted DroneForest pieces (torch lives here only)
      dynamics.py
      raycast.py               oriented-box and ground raycasts
      world.py                 chunk world
      flyer.py                 waypoint follower + avoidance + safety filter
      lidar.py
  tests/drone/                 all new tests
  tools/
    **drone_replay.py**        replay an event log
    **drone_bench.py**         tick-time and raycast benchmarks
  docs/integration/            documents listed in section 0.3
  requirements-drone.txt       torch, numpy, shapely, pyproj, websockets (optional extras)
```

`[VERIFY]` the real names of existing files and the static-file mechanism before creating anything.

### 3.5 Dependency rules

- `drone/*.py` (outside `worker/` and `sim/`) must import **neither torch nor FastAPI internals** beyond what `bridge.py` needs. They must be unit-testable in plain Python.
- `drone/worker/` and `drone/sim/` may import torch. Nothing else may import them.
- `protocol.py` is the only module shared by app, worker and tests for message shapes.
- The disaster code imports the drone package only through `drone.bridge` (router) and `drone.state` hooks, guarded by `DRONE_ENABLED`.

### 3.6 Data flow for one scenario

1. User runs a simulation (existing flow).
2. The app finishes the hazard and damage computation and calls `drone.state.on_scenario_ready(scenario)`.
3. `truth.py` samples hidden damage and survivors with seed `S` and caches them (section 9).
4. `stations.py` reads police stations and creates one drone unit per available station (section 14).
5. The fleet is described to the worker (`fleet_init`), along with the area's origin and the config.
6. The priority engine scores targets using the modelled probabilities (section 12).
7. Auto mode, or the operator, tasks a drone. The planner builds a sortie. The app sends commands.
8. The worker builds chunk worlds on demand, flies, scans, and emits events.
9. The app updates scan state, creates detections and calls, updates the model (if enabled), and pushes updates to browsers.
10. The browser draws map layers and, if open, the corner window.

---

## 4. Coordinate frames and georeferencing

### 4.1 Frames

| Frame | Definition | Used by |
|---|---|---|
| **WGS84** | lon, lat in degrees (EPSG:4326) | Disaster app, MapLibre, API payloads |
| **Area ENU** | local east-north-up metres, origin at the area's centroid, ground z = 0 | App-side drone logic, planner, truth, scan grid |
| **Chunk frame** | area ENU shifted by a chunk origin so values stay small | Worker, GPU tensors, three.js |

### 4.2 Rules

- **Server-side math is float64.** Convert to float32 only inside chunk frames for GPU and rendering. Reason: city-scale coordinates in float32 jitter in the renderer and lose precision in raycasts.
- Use `pyproj` with a **local transverse mercator or UTM zone chosen from the area centroid** (for Noida this is UTM zone 43N, EPSG:32643, but compute it, don't hardcode). Within a 30 km area, distortion is negligible for this purpose.
- `georef.py` API:

```python
class AreaFrame:
    def __init__(self, lon0: float, lat0: float): ...
    def to_local(self, lon, lat) -> tuple[float, float]       # metres east, north
    def to_lonlat(self, x, y) -> tuple[float, float]
    def polygon_to_local(self, coords_lonlat) -> list[tuple[float, float]]
    def chunk_origin(self, x, y, size) -> tuple[float, float] # snapped to a grid
```

- Ground is **flat at z = 0**. Building height = measured height if available, else `storeys × 3.0 m` using the model's storeys, else a default of 6 m (two storeys). Roof is flat.
- Altitude is stored two ways: `z` (metres above ground, which equals above sea level here) and, where useful, `agl` (above the local roof or ground beneath the drone).
- Yaw: 0 = east, counter-clockwise positive, radians in the worker; compass heading degrees in API payloads for the browser. Convert in one place (`georef.py`).

### 4.3 Tests

- Round-trip `lonlat → local → lonlat` error below 1 cm over the full 30 km area.
- Known pair: two points 1 km apart east-west at Noida's latitude measure 1000 m ± 1 m in the local frame.
- Chunk origins are deterministic and snapped, so the same position always maps to the same chunk id.

---

## 5. Shared state and data model

The app owns one **incident state** per loaded area and scenario. It is rebuilt when a scenario runs or the area changes. All entities carry the simulation time `t` (seconds since T+0) they were last updated.

### 5.1 Entities

```python
class Station:                      # a police station, adapted from the existing facility record
    id: str                         # existing facility id
    name: str
    lonlat: tuple[float, float]
    local_xy: tuple[float, float]
    building_id: str | None
    available: bool                 # False if likely damaged (existing grey-out rule)
    unavailable_reason: str | None
    spare_batteries: int            # charged batteries on the shelf
    charging: list[float]           # sim times when each recharging battery is ready
    drone_id: str

class Drone:
    id: str
    station_id: str
    status: DroneStatus             # see section 19.1
    mode: ControlMode               # auto | assisted | manual | rth | hold
    control_owner: str              # "auto" | "operator"
    pose: Pose                      # x, y, z, yaw in area ENU; plus lonlat and agl
    velocity: tuple[float, float, float]
    battery_wh: float
    battery_pct: float
    energy_to_home_wh: float        # estimate, updated each telemetry frame
    rth_triggered: bool
    sortie_id: str | None
    task_id: str | None
    link_ok: bool
    fidelity: str                   # "L0" or "L1"

class Task:
    id: str
    type: TaskType                  # survey_block | inspect_building | verify_sos | check_road | goto
    created_by: str                 # "auto" | "operator" | "sos"
    geometry: dict                  # GeoJSON polygon, point or line (WGS84)
    local_geometry: list            # same in area ENU
    building_ids: list[str]
    block_id: str | None
    priority_score: float
    score_components: dict          # victims_term, info_term, sos_term, energy_wh (section 12)
    est_energy_wh: float
    est_duration_s: float
    pinned: bool
    skipped: bool
    status: TaskStatus              # queued | planned | active | done | failed | cancelled
    assigned_drone: str | None
    reachable_from: list[str]       # station ids that can reach it with reserve

class Sortie:
    id: str
    drone_id: str
    task_ids: list[str]
    route: list[Waypoint]           # transit, survey passes, return
    plan: dict                      # energy budget, per-leg energy and time, reserve
    timeline: dict                  # launch_t, arrive_t, survey_start_t, rth_t, land_t (planned + actual)
    status: SortieStatus

class Detection:
    id: str
    drone_id: str
    t: float
    lonlat: tuple[float, float]
    building_id: str | None
    kind: str                       # "person" (only kind in v1)
    confidence: float               # 0 to 1
    est_people: int
    status: DetectionStatus         # unverified | confirmed | rejected | merged
    merged_into: str | None
    call_id: str | None
    snapshot: str | None            # optional small image key (not required in v1)

class BuildingScan:
    building_id: str
    scan_confidence: float          # 0 to 1
    state: str                      # unscanned | partial | scanned
    observed_damage: str | None     # intact | partial | collapsed
    observed_t: float | None
    observed_by: str | None

class RoadObservation:
    edge_id: str                    # existing road graph edge id [VERIFY]
    blocked: bool
    t: float
    drone_id: str

class TruthBuilding:                # hidden from the UI until observed
    building_id: str
    state: str                      # intact | partial | collapsed
    trapped: int                    # people inside, not visible from outside
    exposed: int                    # people visible to a drone (outdoors, roof, rubble edge)
```

### 5.2 Event bus

`state.py` exposes an in-process asyncio publish and subscribe bus. Everything that changes state publishes an event; websocket hubs and persistence subscribe. Event types are listed in section 6. Events are also appended to an **ndjson log** (`data/<area>/drone/events-<scenario>.ndjson`), which enables replay and debugging.

### 5.3 Persistence

- Truth, scan grid snapshots and the event log are cached under the area's data directory, keyed by `(area_id, scenario_hash, seed)`.
- A server restart rebuilds the incident state: fleet and stations from the scenario, scan state from the last snapshot plus the log tail, calls via the existing mechanism.
- `[VERIFY]` how `live.py` persists open SOS calls today and reuse that mechanism for drone-created calls rather than inventing another.

### 5.4 Scenario identity

`scenario_hash` covers hazard parameters, area id, data versions and the model version. If any change, a new hash gives new truth. Re-running the same scenario with the same seed reproduces the same truth. A **"Re-roll truth"** operator action changes the seed.


---

## 6. Drone protocol

All messages are JSON text frames, except point-cloud updates (binary). Models live in `drone/protocol.py` (pydantic). A JSON Schema export (`python -m drone.protocol --schema > docs/integration/protocol.schema.json`) is the contract for the worker, the browser and any future real drone.

### 6.1 Envelope

```json
{ "v": 1, "type": "telemetry", "seq": 1042, "t": 913.4, "drone_id": "drn-police-17", "payload": { } }
```

| Field | Meaning |
|---|---|
| `v` | protocol version (integer). Breaking changes bump it. Peers reject unknown major versions |
| `type` | message type |
| `seq` | per-sender, per-connection counter. Detects drops and ordering problems |
| `t` | simulation time in seconds (not wall-clock) |
| `drone_id` | present for drone-scoped messages |
| `payload` | type-specific body |

### 6.2 Channels

| Channel | Direction | Purpose |
|---|---|---|
| `/ws/worker` | worker ⇄ app | commands down, events up |
| `/ws/drone` | app → browser (plus browser → app for manual input) | state events down; `manual_input` up |
| REST `/api/drone/*` | browser → app | commands, queries, queue edits (section 17) |

The browser never talks to the worker.

### 6.3 App → worker commands

| Type | Payload | Notes |
|---|---|---|
| `hello_ack` | `{protocol, config, area: {lon0, lat0, epsg}}` | sent after the worker's `hello` |
| `fleet_init` | `{drones: [{id, station_id, home_xy, home_yaw, battery_wh}], seed}` | replaces the fleet |
| `world_hint` | `{chunk_ids: [...]}` | optional prefetch for the next leg |
| `world_chunk` | `{chunk_id, origin_xy, size, buildings: [...], roads: [...], rubble: [...], survivors: [...]}` | geometry in chunk frame (section 8). App-built, so the worker never reads the database |
| `launch` | `{sortie_id, route: [...waypoints], mode}` | starts a sortie |
| `goto` | `{x, y, z, speed}` | single waypoint (assisted) |
| `follow_route` | `{route: [...waypoints], speed}` | sequence of waypoints |
| `set_mode` | `{mode: "auto"|"assisted"|"manual"|"hold"}` | |
| `manual_input` | `{vx, vy, vz, yaw_rate, seq, collision_assist}` | velocity commands in the drone's heading frame, forwarded from the browser at 20 Hz |
| `return_home` | `{reason}` | |
| `abort` | `{}` | stop and hold position |
| `set_clock` | `{sim_t, speed, paused}` | authoritative clock from the app |
| `set_wind` | `{speed, dir}` | optional, for the energy model |
| `battery_swap` | `{drone_id, battery_wh}` | after landing and turnaround |
| `shutdown` | `{}` | |

### 6.4 Worker → app events

| Type | Rate | Payload |
|---|---|---|
| `hello` | once | `{protocol, worker_version, fidelity_levels, device}` |
| `telemetry` | 10 Hz (configurable) | `{pose, velocity, battery_wh, energy_to_home_wh, mode, status, wind, assist_active, nearest_range_m}` |
| `status` | on change | `{status, reason}` (see state machine, section 19.1) |
| `sortie_event` | on change | `{sortie_id, phase: "launched"|"arrived"|"survey_started"|"survey_done"|"rth"|"landed", ...}` |
| `scan_events` | up to 2 Hz | `{buildings_seen: [{building_id, fraction_seen_delta}], cells_covered: [...], road_edges_seen: [...]}` |
| `detections` | on event | `{candidates: [{x, y, building_id, confidence, est_people}]}` |
| `cone` | 5 Hz | `{polygon_local: [[x, y], ...]}` camera footprint on the ground |
| `pointcloud` | up to 2 Hz, binary | quantised xyz (int16 in chunk frame, 5 cm) plus height byte, decimated, only new points |
| `alarm` | on event | `{code: "LOW_BATTERY"|"RTH_FORCED"|"COLLISION_AVOIDED"|"GEOFENCE"|"LINK_LOST", message}` |
| `error` | on event | `{code, message}` |

### 6.5 App → browser events (`/ws/drone`)

The app relays most worker events after converting local coordinates to lon/lat, and adds app-level events:

`clock`, `fleet`, `drone_update`, `task_queue` (full or diff), `sortie_plan`, `scan_update` (diff), `observation` (building damage observed), `detection`, `call_update`, `road_update`, `model_update` (feedback applied), `reachability`, `chunk_geometry` (for the corner window's 3D view), `alarm`, `snapshot` (full state for a newly connected browser).

On connect the browser receives `snapshot`, then diffs. If a browser sees a `seq` gap it requests a new `snapshot`.

### 6.6 Browser → app

REST for discrete actions; one websocket message for continuous input:

```json
{ "type": "manual_input", "vx": 4.0, "vy": 0.0, "vz": 0.0, "yaw_rate": 0.3, "seq": 881, "collision_assist": true }
```

**Dead-man rule:** if the app (or worker) has received no `manual_input` for 500 ms while the drone is in manual mode, it commands zero velocity (hover). The browser sends at 20 Hz and also sends an explicit zero on key release.

### 6.7 Backpressure and sizes

- Telemetry is droppable: only the latest matters. The hub keeps one pending telemetry message per drone per client.
- `scan_update` and `observation` are not droppable. They are queued, and coalesced per building.
- Binary point cloud frames are capped at about 50 KB; the worker decimates (voxel grid 0.5 m) when it would exceed the cap.
- If a client's send queue grows beyond a limit, the hub drops it and the client reconnects and requests a snapshot.

### 6.8 Versioning and the real-drone path

`telemetry`, `scan_events`, `detections`, `cone`, `pointcloud`, `alarm` and `sortie_event` are the **drone-to-app contract**. A real drone adapter (MAVLink or ROS) would implement these from real data. `launch`, `goto`, `follow_route`, `manual_input`, `return_home`, `abort`, `battery_swap` are the app-to-drone contract. Keep both free of simulator-only concepts.

---

## 7. Drone worker design

### 7.1 Process lifecycle

1. `python -m drone.worker --app ws://localhost:8000/ws/worker` (or launched by the app's supervisor, section 7.6).
2. Connect, send `hello`, wait for `hello_ack` and `fleet_init`.
3. Run the fleet loop until `shutdown` or disconnect. On disconnect, retry with backoff (1, 2, 4, ... up to 10 s), then re-sync from `hello_ack`.

### 7.2 Backend interface

All drone behaviour sits behind one interface so that the simple kinematic drone and the physics drone are interchangeable, and a real-drone adapter could be a third implementation.

```python
class DroneBackend(Protocol):
    fidelity: str                                    # "L0" or "L1"
    def reset(self, drone_id: str, home_xy, home_yaw, battery_wh: float, seed: int) -> None: ...
    def set_world(self, chunks: list[WorldChunk]) -> None: ...
    def command(self, cmd: Command) -> None: ...     # goto, follow_route, manual_input, return_home, abort, set_mode
    def step(self, dt: float, sim_t: float) -> Telemetry: ...
    def drain_events(self) -> list[Event]: ...       # scan_events, detections, cone, pointcloud, alarms, sortie events
```

### 7.3 Fidelity levels

| Level | What it simulates | Scan and detection | Max speed multiplier | Phase |
|---|---|---|---|---|
| **L0 kinematic** | Point-mass with speed and acceleration limits, analytic energy model, straight or waypoint paths, altitude hold | Analytic: a building counts as seen if inside the camera footprint and range; detection by probability | Large (it is cheap) | 1 |
| **L1 physics** | DroneForest quadrotor dynamics, wind gusts, drift, lidar raycasts against the chunk world, flyer with avoidance | From lidar hits and ray-to-building ids; detection by probability gated by line of sight | About 4× | 3 |

Both emit the same event types. The app does not care which one is running. **Switching fidelity mid-flight is not required.** L0 is selected for fast-forward and for machines without torch.

### 7.4 The fleet loop

```
fixed dt (default 0.05 s, [VERIFY] against DroneForest control rate)
 loop:
   advance sim time toward target given by set_clock (speed × wall dt), in substeps of dt
   for each drone: apply latest command, step backend, collect telemetry and events
   every 0.1 s: send telemetry
   every 0.5 s: send scan_events, cone, pointcloud batches
   handle incoming commands between substeps
```

The loop never blocks on the websocket; sending uses an async queue with the drop rules from section 6.7. If a tick takes longer than `dt`, the loop slows the sim clock rather than skipping physics, and reports `worker_lag` so the app can show "simulation running slower than requested".

### 7.5 Determinism

- Seeds: `seed_world = hash(scenario_hash, chunk_id)`, `seed_drone = hash(seed, drone_id)`. Wind and noise use drone seeds.
- Physics must use fixed `dt`. No wall-clock dependence in simulated behaviour.
- Test: same inputs and seed produce an identical event stream (compare hashes of the first 30 simulated seconds).

### 7.6 Supervision

`bridge.py` optionally launches the worker as a subprocess (`DRONE_WORKER_AUTOSTART=1`), restarts it on crash with backoff, and exposes `/api/drone/health`. When the worker is absent, drone units show `offline` and the corner window shows "Drone service not running" with the command to start it.

### 7.7 Device handling

- `DRONE_DEVICE=auto|cuda|cpu`. Auto picks CUDA if available and has enough free memory (a 4 GB card must also hold the browser's compositing, so cap worker GPU memory at about 1.5 GB).
- CPU fallback must work at 1× speed for one drone (acceptance test in Phase 3).
- Log the chosen device and measured tick time at startup.

---

## 8. World-on-demand

The drone's world is built **only around where it is flying**, from the same building data the disaster app already holds, plus the sampled damage truth.

### 8.1 Chunking

- Chunk size: 250 m × 250 m (config), aligned to a grid in area ENU.
- The worker holds the **active chunk set**: the chunk under the drone plus neighbours within `lidar_range + margin`. Typically 1 to 4 chunks.
- The app builds chunks (it has the data, the truth and the road graph) and sends `world_chunk`. Building chunks is done in a thread pool and cached on disk and in memory (LRU, about 64 chunks).
- Prefetch: when a sortie route is known, send `world_hint` for chunks along the route and the survey block.
- If a drone is about to enter a chunk that has not arrived, it holds (hover) and the UI shows "loading world". This must be rare; test with a slow builder.

### 8.2 Building colliders: oriented boxes

DroneForest's boxes are axis-aligned. Real footprints are rotated polygons.

- For each footprint, compute `shapely.minimum_rotated_rectangle` → centre, half-extents, yaw. Height from section 4.2. This is the **oriented bounding box (OBB)**.
- `SIMPLIFICATION:` L-shaped and courtyard buildings become a filled rectangle. For footprints where the rectangle's area exceeds the polygon's by more than 40 percent, split into up to 3 OBBs by a simple greedy decomposition. Keep this optional (Phase 3 stretch).
- Raycast: transform the ray into the box frame (translate, rotate by −yaw), then apply the existing slab method. Implement in `sim/raycast.py` in batched torch, same signature as the existing axis-aligned version so the lidar code is unchanged.
- Every collider carries `building_id` (index into a per-chunk id table) so a lidar hit can be attributed to a building. Rubble and road debris carry the id of their source building or edge.
- Ground: flat plane at z = 0. Hit id `-1` means ground.

### 8.3 Cost control

Dense Noida chunks may hold several hundred to a couple of thousand buildings. Each lidar scan (default 80 rays) against every box is wasteful.

- Per tick, **pre-cull** boxes to those within `lidar_range` of the drone (CPU grid hash or a coarse torch distance mask) before raycasting.
- Use a uniform grid index per chunk with cell size equal to lidar range.
- Benchmark in `tools/drone_bench.py` (target: 1 drone, 80 rays, dense chunk, under 2 ms per scan on the owner's GPU, under 10 ms on CPU).

### 8.4 Damage to geometry

The truth state (section 9) of each building controls what is placed:

| State | Geometry |
|---|---|
| intact | one OBB at full height |
| partial | OBB reduced to 40 to 70 percent height (random, seeded); one or two thin wall segments removed along an edge; 2 to 4 rubble boxes spread near the base |
| collapsed | OBB replaced by a rubble field: 5 to 12 slab and block boxes, pancake height 10 to 25 percent of the original, footprint spilling out by up to 5 m onto the surrounding ground; random yaw and sizes (seeded) |

Reuse DroneForest's disaster-zone rubble generator (broken walls, fallen slabs) where possible. Adapt it from "arena" placement to "inside this footprint" placement.

### 8.5 Roads and debris

- Roads are drawn as flat ground colour (no collision). Only debris on roads collides.
- For each road edge in the chunk, the truth (section 9) has a blocked flag sampled from the simulation's per-edge blockage probability. Blocked edges get 2 to 4 debris boxes across the carriageway.
- `[VERIFY]` the edge id scheme and where per-edge blockage probability is stored.

### 8.6 Chunk payload

```json
{
  "chunk_id": "c_12_-7", "origin_xy": [3000.0, -1750.0], "size": 250.0,
  "buildings": [
    {"id": "b123", "c": [10.2, 44.1], "h": [8.0, 6.5, 9.0], "yaw": 0.31, "state": "collapsed", "kind": "obb"}
  ],
  "rubble":    [{"src": "b123", "c": [12.0, 46.0, 1.0], "h": [3.0, 2.0, 1.0], "yaw": 1.2}],
  "road_debris": [{"edge": "e881", "c": [60.0, 12.0, 0.8], "h": [2.0, 1.5, 0.8], "yaw": 0.0}],
  "roads": [{"edge": "e881", "pts": [[0,0],[60,12],[120,30]], "width": 7.0}],
  "survivors": [{"id": "s1", "p": [11.0, 45.0, 0.3], "exposed": true}]
}
```

Coordinates are in the chunk frame (float32-safe). Survivors are included so the worker can run detection geometry; their existence is **not** revealed to the browser (section 9.5).

### 8.7 Browser copy of geometry

The corner window's 3D view needs geometry too. The app sends `chunk_geometry` to browsers containing **only buildings, rubble, road debris and roads** (no survivors), so the browser can render the same world without access to hidden truth. Hidden survivors become visible only through detections.

### 8.8 Tests

- OBB raycast agrees with shapely ray-polygon intersection within 1 cm on random rays and boxes.
- Chunk building is deterministic for the same `(scenario_hash, chunk_id)`.
- Every building in a chunk appears exactly once; ids are unique.
- Collapsed buildings are lower than intact; blocked edges contain debris.

---

## 9. Damage truth, survivors and detection

### 9.1 Purpose and honesty

The simulation produces **probabilities**. A drone can only observe **outcomes**. So the system samples one hidden outcome per building and per road edge, and the drone discovers it. Because the outcome is drawn from the model's own probabilities, this tests the workflow (finding, routing, updating), not the accuracy of the fragility curves. The UI shows a small note in the drone-updated view: "Observations come from a simulated disaster drawn from this model."

### 9.2 Sampling truth

`truth.py`, run once when a scenario finishes:

1. Inputs: per-building probabilities of damage states, occupants for the scenario's time of day, per-edge blockage probability.
2. If per-building Monte Carlo realisations are available (`[VERIFY]`), **pick one realisation** with the seed. This keeps spatial correlation consistent with the model. Otherwise, draw independently per building, and record `SIMPLIFICATION: independent draws lose spatial correlation`.
3. Map the model's damage classes to `intact | partial | collapsed`:
   - collapsed ← complete damage or collapse
   - partial ← extensive damage
   - intact ← none, slight or moderate
   Record the mapping in config so it can be changed.
4. Sample edge blockage independently per edge from its probability.
5. Persist the truth (section 5.3).

### 9.3 Survivors

For each building:

- `occupants` from the model for the scenario's time of day.
- If collapsed: draw `trapped` from a binomial with `p_trapped_collapsed` (default 0.35); of those, `exposed` (visible to a drone: at rubble edges or in voids) from a binomial with `p_exposed_given_trapped` (default 0.25).
- If partial: `trapped ~ Binomial(occupants, p_trapped_partial)` (default 0.05), `exposed` with `p_exposed_given_trapped` (0.5).
- If intact: no survivors needing rescue.
- Outdoors: at night few people; by day more. v1 skips outdoor people except those who escaped a collapsed building (draw `escaped = Binomial(occupants − trapped, p_escape)` and place them on the nearest street or open ground, `exposed = true`).

Place survivor positions inside the building's rubble footprint (trapped, exposed) or on the nearest road node (escaped). All numbers are config values and listed in `SIMPLIFICATIONS.md`.

### 9.4 Detection model (simplified)

For each scan tick, for each survivor within the drone's camera footprint and range:

```
visible = exposed AND line_of_sight_clear AND within_range
p = p_detect(altitude_agl)          # linear from 0.9 at <=30 m to 0.3 at 120 m (config)
detected ~ Bernoulli(p * visibility_factor)
```

- `line_of_sight_clear` uses the same raycast code as the lidar (L1) or a simple geometric test against nearby boxes (L0).
- A survivor can be re-detected on later passes. Repeated detections within `merge_radius` (15 m) and `merge_window` (10 min of sim time) merge in the app (section 14.4).
- **False alarms:** at a rate of `false_alarms_per_km2` (default 0.3) across the surveyed area, emit a detection with random low confidence (0.2 to 0.5) at a random street or rubble point.
- Detection confidence: `0.5 + 0.5 * p` for true detections (plus a small noise), as above for false alarms. This gives the operator something realistic to confirm or reject.

### 9.5 Information hygiene

- The browser never receives truth, survivors or unobserved damage states.
- `observation` events reveal only the observed state of a scanned building.
- Debug view (`?debug_truth=1`, dev only) may reveal truth for development. It must be impossible to enable through normal UI.

### 9.6 Observed damage

When a building's `scan_confidence` crosses the `scanned` threshold (section 13), its `observed_damage` is produced:

```
observed = confusion_matrix_sample(truth_state)     # default accuracy 0.9, errors to adjacent state
```

`SIMPLIFICATION:` a real system would classify from the point cloud by comparing observed volume with the expected volume. The interface is in `scan.py:classify_building(...)`, so a real classifier can replace the noisy-truth stub later.

### 9.7 Tests

- Truth is deterministic for a given seed and changes with a different seed.
- Summed expected collapses over many seeds approaches the model's expected collapses (tolerance, a statistical test with a fixed seed set).
- No survivor is placed in an intact building.
- Detection probability decreases with altitude; false alarm count scales with area.

---

## 10. Energy model, flight time and the clock

This is the one part that must be realistic.

### 10.1 Reference airframe (defaults, all configurable)

Treat as an enterprise quadrotor with a thermal and lidar payload. Fix these numbers once and use them everywhere (physics, planner, reachability, UI). The values are chosen so endurance lands in the 25 to 35 minute range; **tune them against the acceptance test in 10.6, not against a spec sheet.**

| Parameter | Default | Meaning |
|---|---|---|
| `mass_kg` | 4.0 | all-up mass (used by climb power and, in L1, by the dynamics) |
| `battery_wh` | 200 | usable energy of a full battery |
| `p_hover_w` | 330 | hover power |
| `p_payload_w` | 25 | sensors and compute |
| `k_forward` | 0.002 | forward-flight factor: `1 + k_forward * v²` |
| `k_wind` | 0.004 | wind factor: `1 + k_wind * w²` (w = headwind component or wind speed, in m/s) |
| `eta_climb` | 0.7 | efficiency for climb power |
| `cruise_speed_ms` | 15 | transit |
| `survey_speed_ms` | 8 | surveying |
| `max_speed_ms` | 18 | manual limit |
| `climb_rate_ms` | 3 | |
| `reserve_fraction` | 0.25 | of full battery kept for return and landing, plus the return estimate (below) |
| `swap_time_s` | 300 | landing to ready, with a charged spare |
| `recharge_time_s` | 3600 | time for a spent battery to become a spare again |
| `spare_batteries_per_station` | 3 | |
| `launch_delay_s` | 180 | pre-flight checks and authorisation |

### 10.2 Power model

```
P(v, vz, w) = (p_hover_w * (1 + k_forward*v^2) * (1 + k_wind*w^2)) + p_payload_w + max(0, mass_kg * 9.81 * vz / eta_climb)
E(Δt)       = P * Δt / 3600     # Wh
```

Descent gives no energy back. The model is implemented once in `drone/energy.py` (pure Python, no torch), and **used identically** by the worker (to drain the battery from actual commanded motion), by the planner (to estimate legs), and by reachability (to compute ranges).

### 10.3 Return-to-home logic

- `energy_to_home(pos, wind)`: straight-line distance back at `cruise_speed`, plus climb or descent to landing, plus a fixed landing allowance (10 Wh), times a safety factor 1.1.
- **Forced RTH** triggers when `battery_wh <= energy_to_home + reserve_fraction * battery_wh_full`. It overrides auto, assisted, and manual control (the operator sees an alarm and the mode becomes `rth`). A config flag `rth_override_allowed` (default `false`) can allow an informed operator to cancel it; leave it off.
- **Warning** in manual mode when `battery_wh <= energy_to_home + 1.5 * reserve`: banner plus a "Return now" button. A "point of no return" ring on the main map shows how far the drone can still fly away and return.
- If battery reaches zero before landing (only possible when forced RTH is disabled in tests), the drone descends and is marked `lost` (a tested failure path).

### 10.4 Reachability (F8)

For each station, with a full battery and the planned wind:

```
E_usable      = battery_wh * (1 - reserve_fraction)
E_leg(d)      = energy to fly d metres at cruise_speed (one way)
r_max         = max d such that  2*E_leg(d) + E_survey_min  <= E_usable      # E_survey_min = energy for survey_min_s (default 120 s)
survey_time(d)= (E_usable - 2*E_leg(d)) / P(survey_speed)                    # minutes of survey available at distance d
```

- Publish three rings: `r_max`, and distances where `survey_time` = 5 and 10 minutes. Draw as a polygon layer (circles, since flight is straight-line over flat ground; if a geofence exists, intersect with it).
- Targets outside `r_max` of **every** available station are greyed in the queue and map with the reason "out of range from police stations".
- Recompute when wind, battery spec or station availability changes.

### 10.5 The clock

`clock.py` is authoritative and lives in the app:

```python
class SimClock:
    sim_t: float          # seconds since T+0 (scenario start for response purposes)
    speed: float          # 1, 2, 4 (L1 cap), 10, 30, 60 (L0 only; later phases)
    paused: bool
    def advance(wall_dt) -> float
```

- Broadcast `clock` at 2 Hz and on change; worker follows with `set_clock`.
- The existing Dispatch behaviour (team ETAs, SOS progress) must read the same clock. `[VERIFY]` whether it uses wall time; if so, add an adapter so team movement scales with `speed` and respects pause. This is a required change for the "one project" feel, and the only intrusive one to existing logic. Keep it behind the drone feature flag if possible.
- UI: a small clock control in the main header: `T+00:42:10  [pause] [1×] [2×] [4×]`.
- In Phase 1 to 7 the available multipliers are 1, 2, 4. Faster values arrive with the L0 fast-forward (Phase 8).
- Changing speed during manual flight is allowed up to 2×; above that manual control is disabled with a message.

### 10.6 Acceptance tests for flight time

| Test | Expected |
|---|---|
| Hover on a full battery, no wind | 32 to 36 min |
| Constant 10 m/s cruise, no wind | 26 to 31 min |
| Constant 15 m/s cruise | 20 to 25 min |
| A planned sortie with 15 min survey at 8 m/s at 2 km range | lands with at least the reserve remaining |
| Planner estimate vs worker-simulated energy for the same route | within 5 percent |
| Forced RTH at the point of no return | lands with at least 5 percent of full battery left |
| Wind 8 m/s | endurance falls by 15 to 30 percent relative to no wind |

---

## 11. Flight control

### 11.1 Modes

| Mode | Who decides | How it flies |
|---|---|---|
| `auto` | Priority engine and sortie planner | Follows the planned route, executes survey passes, returns |
| `assisted` | Operator picks a target on the map | App creates a pinned task, plans a mini-sortie, flies it, then returns to auto (or hold) |
| `manual` | Operator, with keyboard or gamepad | Velocity commands become a moving look-ahead waypoint for the flyer |
| `hold` | Operator pressed pause or the drone is waiting | Hover in place; still drains battery |
| `rth` | Failsafe or operator pressed Return | Climbs to transit altitude, flies home, lands |

### 11.2 Control ownership and handover

- Exactly one owner at a time: `auto` or `operator`.
- **Take control:** operator presses "Take control" (or any manual key with focus). The current task is paused, not cancelled. The drone enters `manual` with collision assist on.
- **Release:** the operator can **Resume auto**, **Hold**, or **Return home**. Resume re-plans from the current position and battery; the paused task re-enters the queue with its original priority.
- A second browser tab is read-only (shows "controlled from another window").
- All handovers emit events for the log and the UI.

### 11.3 Manual control through the existing flyer

The existing `mission.py` flies to a moving look-ahead waypoint using the heuristic flyer. Manual mode reuses this:

```
operator input (vx, vy, vz, yaw_rate)  in heading frame
   │   (smoothed with a first-order filter, tau = 0.25 s)
   ▼
desired world velocity v_cmd
   │
   ▼
look-ahead waypoint = position + v_cmd * T_look      # T_look = 2.0 s, clipped to max speed
   │
   ▼
flyer (heuristic) tracks the waypoint, handling avoidance and attitude
```

- Altitude: `vz` input adjusts the altitude target; the drone holds altitude when `vz = 0`.
- Yaw: yaw rate input rotates the camera heading. When flying forward with no yaw input, the heading follows the velocity direction (config `yaw_follows_velocity`).
- **Collision assist (default on):** a safety filter limits the commanded velocity towards obstacles using the lidar nearest-range or SDF-like distance: `v_toward_obstacle <= k * (range - r_safe)`, never negative closing speed below `r_stop` (default 3 m). When it intervenes, emit `alarm COLLISION_AVOIDED` (rate limited) and set `assist_active` in telemetry so the UI can flash an indicator.
- **Assist off:** allowed via a toggle that requires a confirmation click. Collisions end the flight (`crashed` status) in L1. In L0 there are no collisions, so assist-off does nothing; show a notice.
- The window shows "ASSIST" or "NO ASSIST" permanently.

### 11.4 Inputs

| Input | Mapping |
|---|---|
| Keyboard (when the window or its controls pad has focus) | W/S forward and back, A/D strafe, Q/E down and up, arrow left/right yaw, Space hold, R return home, Shift boost (up to `max_speed`), Esc release control |
| Gamepad (Gamepad API, polled at 30 Hz) | Left stick translate, right stick yaw and altitude; face buttons for hold and return |
| On-screen pad (controls module) | Two virtual sticks for mouse and touch; same message as keyboard |

Keyboard focus rule: keys only steer when the drone window is focused or "Take control" is active, so typing in other fields never moves the drone. Show a "Controls active" border.

### 11.5 Assisted mode

- **Click-to-fly on the map** (decided): with the drone selected and the "Send drone" tool active, clicking a building, a road segment, or drawing a polygon creates a task:
  - building → `inspect_building` (orbit-style scan pattern: a ring at roof height + 20 m, radius equal to the building's half-diagonal + 20 m, 8 to 12 waypoints)
  - road segment → `check_road` (fly along the edge at roof height + 20 m)
  - polygon → `survey_block` tasks generated by the survey path generator (section 12.5)
- The planner checks reachability and energy. If infeasible, the UI says why (out of range, not enough battery, no spare battery, station unavailable) and suggests alternatives (nearest feasible station, wait for battery swap).
- Assisted tasks are `pinned` and jump the queue. After completion the drone returns to its previous mode (auto if it was auto; otherwise hold).

### 11.6 Failsafes

| Event | Response |
|---|---|
| Forced RTH threshold | Switch to `rth` regardless of mode |
| Link lost (worker disconnect or no manual input for 5 s in manual) | Hold for 5 s, then `rth` |
| Geofence breach (optional box around the area) | Stop at the boundary and hold; alarm |
| Crash (L1 collision) | Status `crashed`; station's spare count unaffected; drone unavailable for the rest of the scenario (config) |
| Worker crash | Units `offline`; on reconnect, drones restart from the station at the last known battery if landed, otherwise marked `lost` |


---

## 12. Priority engine and sortie planner

Both live in the app (`priority.py`, `planner.py`), are pure Python, and use `energy.py` so that their predictions match the simulated flight.

### 12.1 What "priority" means

Priority is **where scanning is most valuable per unit of flight energy**, not simply where predicted damage is highest:

- A building already confirmed collapsed does not need a damage check, but still deserves a victim search.
- A building already scanned clean drops to the bottom.
- A building whose damage is genuinely uncertain (around 50 percent) is worth scanning to learn the truth.
- Open SOS calls matter more than model estimates.

### 12.2 Task granularity

Tasks are **blocks**, not whole 500 m cells, so that a sortie can fit several.

- Divide the area into survey blocks of 250 m × 250 m (config `block_size_m`), aligned with the chunk grid.
- A block holds the buildings whose centroid is inside it. Blocks with fewer than `min_buildings_per_block` (default 5) are merged into neighbours or skipped.
- A typical block at 60 m swath needs about 5 survey passes of 250 m, around 1.25 km, about 2.6 minutes at 8 m/s. A usable 15 to 20 minute survey budget therefore covers roughly 6 to 7 blocks (about 0.4 km²). That is why prioritisation matters.
- Other task types (`inspect_building`, `verify_sos`, `check_road`, `goto`) are small and created on demand.

### 12.3 Value of a block

For each building `i` in a block (using the **current**, possibly drone-updated probabilities):

```
O_i       = expected occupants (scenario time of day)
Pc_i      = collapse probability (current)
U_i       = 1 - scan_confidence_i                      # 0 if already well scanned
victims_i = w_v * O_i * Pc_i * U_i                      # people we might find
info_i    = w_d * 4 * Pc_i * (1 - Pc_i) * U_i           # uncertainty we would resolve (peaks at Pc = 0.5)
sos_i     = w_sos * (sum over open SOS or unverified drone calls in building i of call weight)

value(block) = sum_i (victims_i + info_i + sos_i)
```

Defaults: `w_v = 1.0`, `w_d = 2.0` (people-equivalents per building), `w_sos = 15.0`. All in config. Store the three sums as `score_components` for the UI.

**Score for queue ordering:** `score = value / max(est_energy_wh, E_floor)`, where `est_energy_wh` is the energy to survey the block plus the marginal transit from the nearest feasible station (section 12.4). Show both `value` and `score`.

### 12.4 Reachability and cost per task

For each task and each **available** station with a ready drone (or one that will be ready soon):

- `transit_wh` = one-way transit energy from the station to the block entry at `cruise_speed` (plus altitude change).
- `survey_wh` = energy of the survey path at `survey_speed`.
- A task is **reachable** from a station if a sortie containing only that task satisfies the energy constraint with reserve.
- `reachable_from` lists stations. If empty, the task is greyed with reason `out_of_range`.

### 12.5 Survey path generator

`planner.py:survey_path(polygon_local, building_heights)` returns waypoints.

1. Altitude `H = min(max_alt, tallest_building_in_polygon + clearance)`, with `clearance = 20 m` and `max_alt = 120 m`. If the tallest building plus clearance exceeds `max_alt`, flag the task `too_tall` and survey at `max_alt` with a warning.
2. Swath `W = min(2 * H * tan(fov/2), 2 * lidar_range * 0.8)`, with a floor of 40 m. Default fixed `W = 60 m` for v1 (`SIMPLIFICATION:` constant swath).
3. Pass spacing `s = W * (1 - overlap)`, `overlap = 0.2` by default.
4. Boustrophedon (lawnmower) lines parallel to the polygon's longest edge, clipped to the polygon (shapely), connected with short turns.
5. Return waypoints at altitude `H`, each with a speed (`survey_speed`) and a `scan_on` flag.

### 12.6 Sortie planner

Goal: choose an ordered set of tasks for one drone that maximises total value subject to energy.

```
inputs : drone (station, battery), candidate tasks (top K=30 by value / direct-energy), wind, time
output : Sortie (ordered tasks, route, energy plan, timeline) or None

1. route = [station]
2. repeat:
     for each candidate task t and each insertion position p in route:
        route' = insert(route, t, p)
        E = energy(route') ; feasible if  E * 1.1 <= battery_wh_usable  (usable = battery - reserve)
        gain = value(t) ; cost = E(route') - E(route)
     choose the feasible (t, p) with the best gain / cost; stop if none
3. improve order with 2-opt (energy as the metric) until no improvement or 50 iterations
4. build waypoint route: transit legs at cruise altitude, survey paths for each task, return
5. compute plan: per-leg energy and time, timeline (launch_t = now + launch_delay), expected landing, remaining battery
```

- Transit altitude: `max(building heights along the corridor) + 20 m`, capped at `max_alt`; straight lines over flat ground. This keeps avoidance simple (the drone flies above everything).
- Minimum sortie value threshold: if the best sortie has value below `min_sortie_value`, don't launch (to avoid pointless flights); show "no worthwhile targets in range".
- Determinism: break ties by task id.
- Time budget: planning must complete within 200 ms for K = 30.

### 12.7 Auto mode loop

```
every planning tick (5 s sim time, and on events: task done, SOS created, drone landed, observation applied):
   for each drone that is ready at its station (status 'ready'), not manual, auto enabled:
        sortie = planner.plan(drone)
        if sortie: assign tasks, send launch (after launch_delay)
   for each active drone in auto: if a much better task appeared (SOS) and energy allows, insert it
        (never abandon a task that is more than 50% complete unless an SOS has weight > threshold)
```

The planner never plans a sortie for a drone whose station is unavailable, whose batteries are spent (no spare), or which is `lost` or `crashed`.

### 12.8 Queue UI semantics (F17)

- The queue shows blocks and small tasks sorted by `score`, with `value`, `est_energy_wh`, `est_duration`, and a **Why** popover (components and the top three contributing buildings).
- Operator actions: **pin** (keep at top; planner must include if feasible), **skip** (exclude until un-skipped), **reorder** (drag; equivalent to setting a manual priority offset), **ban zone** (draw polygon; excluded), **add** (create a task from the map).
- A manual reorder sets `manual_rank`; sorting uses `manual_rank` first, then `score`.
- Each task shows its state and, if assigned, the drone and sortie.
- When an SOS arrives (F18) a banner "SOS bumped the queue" flashes and the new task is highlighted.

### 12.9 Tests

- Score is monotone: raising occupants or collapse probability raises value; raising scan confidence lowers it; `info` peaks at `Pc = 0.5`.
- Planner never produces a sortie that violates the energy constraint (property test over random inputs).
- Planner estimate vs simulated energy within 5 percent (integration test with the kinematic drone).
- Queue operations (pin, skip, reorder, ban) change planner output as expected.
- Planning completes within the time budget.

---

## 13. Scan state and coverage

### 13.1 Per-building scan state

For each building the app tracks `scan_confidence ∈ [0, 1]`:

- At scenario start, every building has `scan_confidence = 0` and state `unscanned`.
- Each `scan_events` message carries `fraction_seen_delta` per building, in the worker's own terms:
  - **L0:** if the building's centroid is inside the camera footprint and `slant_range <= lidar_range`, add `delta = scan_rate * dt * quality(range, speed)` where `quality` falls with range and speed (config). Cap at 1.
  - **L1:** each building has `N_samples = 40` surface sample points (roof grid plus facade ring). A lidar ray that terminates within 0.5 m of a sample, or a line-of-sight test from the drone to the sample within range, marks that sample seen. `fraction_seen = seen / N_samples`.
- The app accumulates with `scan_confidence = max(old, new_fraction)` for L1 (cumulative union), or sums (capped) for L0.
- Thresholds: `partial` when ≥ 0.10, `scanned` when ≥ 0.60 (config). Crossing `scanned` triggers `observed_damage` (section 9.6) and an `observation` event.

### 13.2 Coverage grid for fast map shading

- Server-side `uint8` grid at 10 m resolution over the area (30 km → 3000 × 3000 = 9 MB), or tile-sparse (only tiles touched). Value = 0 unscanned, 1 partial, 2 scanned in that cell (cell = any scan footprint covering it).
- The browser doesn't get the grid. It gets **aggregated 50 m cells** with a scanned fraction, as diffs, only for cells that changed, at 1 Hz.
- Persist snapshots every 60 s of sim time and on pause.

### 13.3 Neighbourhood scan percentage (F23)

Each priority neighbourhood's **scan percentage** is the **building-count-weighted** mean of `scan_confidence` (also shown as "N of M buildings scanned"). Computed in the app and sent with `observation` batches. The priority list in the left panel gets a small progress bar per neighbourhood.

### 13.4 Tests

- Scan confidence never decreases.
- Seen building states change monotonically (`unscanned → partial → scanned`).
- Coverage grid and building states agree for a synthetic straight flight over a synthetic city.
- Neighbourhood percentages equal manual computation on a small fixture.

---

## 14. Dispatch integration

The disaster app already has units, calls, ranking, team suggestions and routes. The drone must **use those**, not duplicate them. Phase 0 maps the real names; the sections below use generic terms.

### 14.1 Drone as a unit (F1)

- Extend the unit model with `kind = "drone"` (alongside fire, ambulance, police teams). A drone unit shows in the Dispatch panel's unit list with: name (`Drone @ <station>`), status chip, battery bar, mode chip, and an "Open" button that opens or focuses the corner window for that drone.
- Status mapping to the existing statuses (adapt to what exists): `ready` ↔ available, `launching`/`transit`/`surveying` ↔ en route / busy, `returning`, `swapping` ↔ unavailable, `offline`/`lost`/`crashed` ↔ unavailable.
- Existing dispatch logic that suggests "nearest working team" for a call must **exclude drones** from ground-team suggestions (drones can't rescue) but include them in a new **"Recon" suggestion** for calls whose confidence is low or which need verification.

### 14.2 Stations (F2)

- `stations.py` reads police stations from the existing facility data. One drone per **available** station (`available = not likely damaged`, using the existing grey-out rule; `SIMPLIFICATION:` the grey-out rule is used for availability instead of the hidden truth).
- Unavailable stations show a drone unit with status `offline` and reason "station in likely-damaged building".
- Station launch rules:
  - A launch needs `status = ready`, a charged battery (or a spare), and the launch delay.
  - After landing: `swapping` for `swap_time_s`; consumes one spare; the spent battery goes to `charging` and returns to the spare pool after `recharge_time_s`.
  - If no spare is available, the drone waits at `waiting_battery` until one is ready, and the UI shows the time remaining.

### 14.3 Calls from drones (F25)

When the app receives a worker detection candidate:

1. Deduplicate against existing detections and calls (section 14.4).
2. Create a `Detection` (`unverified`) and a **call** in the existing call structure with:
   - `source = "drone"`, location, `building_id`, estimated people, `confidence`.
   - Status `unverified` (new status; show as a distinct chip, "Unverified drone sighting").
3. Ranking (existing function): for drone calls, include a `confidence` multiplier in the rank, so a 0.3 confidence sighting ranks below a 0.9 one. Verified drone calls rank like trapped SOS calls with the same building collapse chance.

### 14.4 Duplicate merging (F26)

- Two detections merge if they are within `merge_radius` (15 m, config) and within `merge_window` (10 min of sim time), or both fall inside the same building and one is within 25 m.
- Merging keeps the highest confidence, the largest `est_people`, and the earliest `t`; the others become `merged` with `merged_into`.
- A drone detection also merges into an existing **phone SOS** call within 20 m: the SOS call is marked "drone-corroborated", and no new call is created.

### 14.5 Confirm or reject (F27)

- The Dispatch panel shows unverified drone calls with a snapshot-less card (v1) containing the location, confidence, building, the drone, and two buttons: **Confirm** and **Reject**.
- **Confirm:** the call becomes `open` and enters normal dispatch (suggested teams, routes, send).
- **Reject:** the call closes as `false alarm`. Rejections feed the log; they do not change truth.
- Auto-confirm (config, default off): confidence above 0.85 becomes `open` automatically with a "auto-confirmed" tag. Default off keeps a human in the loop.

### 14.6 "Verify with drone" (F24)

- On a phone SOS call, a **Verify with drone** button creates a `verify_sos` task at the call location with high priority and `created_by = "sos"`. The planner picks the best reachable drone, or reports why none can.
- On arrival the drone performs a short hover-and-scan (`verify_dwell_s`, default 30 s). Outcome: **corroborated** (a detection within 20 m, which then merges into the call), or **not found** (the call gets a "drone could not see anyone" tag; this does not close it, since people may be inside).
- The phone page is **not** changed in v1.

### 14.7 Sending ground teams (F28)

Once a drone-originated call is `open`, the existing flow applies unchanged: nearest working team by driving time, routes on the debris-aware road graph, one-click send, phone ETA (for phone calls only). The road graph includes drone-observed blockages (section 15.4).

### 14.8 "Send drone" on building popups (F24)

Add a **Send drone** button to the building popup. It creates an `inspect_building` task (pinned), shows feasibility (reachable, estimated minutes and battery) before confirming, and offers the nearest feasible station.

### 14.9 Tests

- Drone units appear for available stations only; unavailable stations show `offline`.
- Ground-team suggestion never suggests a drone.
- Merging rules (radius, window, same building, SOS corroboration).
- Confirm and reject transitions.
- Spare battery bookkeeping across several sorties.

---

## 15. Feedback into the damage model

### 15.1 Principles

- **Never overwrite the baseline.** Keep the modelled run intact. Maintain a second set of **drone-updated** probabilities and aggregates. A toggle on the map and summary panel switches between "Modelled" and "Drone-updated". Showing both builds trust.
- **Deterministic and explainable.** Use a simple update rule with documented parameters, not a black box.
- **Strong where observed, weak elsewhere.**

### 15.2 Per-building update (F29, strong)

Let `d ∈ {intact, partial, collapsed}` and the prior `P(d)` come from the model (collapsed ← complete or collapse classes, partial ← extensive, intact ← the rest). With observation `o` and a confusion matrix `C[o|d]` (default accuracy 0.9):

```
P'(d) ∝ C[o|d] * P(d)           (normalised)
```

Convert back to the model's finer damage-state probabilities by scaling classes in proportion to the new class probabilities (document the mapping in `feedback.py`). Mark the building `observed = true`; subsequent aggregation uses a probability of exactly the posterior (never exactly 0 or 1 unless `C` is perfect).

### 15.3 Neighbourhood update (F29, weak)

For unscanned buildings, adjust odds using the **observed vs expected** collapses among scanned buildings in the same 500 m cell and its neighbours:

```
exp   = sum of Pc_i over scanned buildings in the cell neighbourhood (prior)
obs   = number observed collapsed
var   = sum Pc_i (1 - Pc_i)
z     = (obs - exp) / sqrt(max(var, eps))
factor = clip( exp(lambda * z / sqrt(n_scanned)), 0.5, 2.0 )       # lambda = 0.3 by default
odds'_j = odds_j * factor   for unscanned j in the cell
```

- Only apply when `n_scanned >= min_scanned` (default 8).
- Apply to collapse odds; scale other classes to keep probabilities summing to 1.
- Document: this approximates a shared latent "fragility or ground motion residual". It is intentionally conservative.

### 15.4 Roads (F31)

- When the drone observes a road edge, emit a `RoadObservation`. The app calls the road graph's override hook (`responder_layer.set_edge_override(edge_id, blocked)`, to be added; `[VERIFY]` the real structure).
- Blocked: edge cost becomes infinite (or the model's "blocked" treatment). Unblocked: the edge's blockage probability becomes 0.
- Recompute: hospital access estimate, routes in progress (teams en route are re-routed if the blocked edge is on their path; the Dispatch panel shows "re-routed, +N min"), and suggested dispatch order.
- The map draws drone-verified blocked roads in a distinct style.

### 15.5 Aggregates (F30)

- `simulate.py` needs an entry point `recompute_aggregates(prob_overrides, road_overrides) -> Aggregates` that reuses the same code that produced the baseline: expected collapses, deaths, injuries, people at risk, 500 m cell heat map, priority neighbourhoods. `[VERIFY]` whether existing functions can be called with modified probabilities. If not, refactor minimally (extract the aggregation step into a function) and cover it with a test showing identical output to the baseline when no overrides are given.
- Run incrementally: only cells containing changed buildings need recomputing. Debounce to once per 2 s of sim time or 20 observations, whichever comes first.
- **Uncertainty ranges (200 runs)** are not recomputed live (31 s on Noida). Show "ranges reflect the model before drone observations" and offer a "Refresh ranges" button that triggers the existing background run with the overrides applied. `[VERIFY]` feasibility.
- Summary panel in drone-updated view shows both: `Expected collapses  5,424 (modelled)  →  5,310 (drone-updated)`.

### 15.6 Map semantics (F21)

- Modelled damage colours (existing: blue, green, yellow, red) stay.
- Drone-verified buildings get an **outline** in a distinct style plus a small check mark in the popup: "Verified by drone at T+00:42" with the observed state. Verified collapsed buildings use a hatched or darker fill so they stand out from modelled red.
- The legend gains one line for each.

### 15.7 Tests

- With no observations, the updated aggregates equal the baseline.
- Observing a collapsed building raises its collapse probability close to 1 and raises neighbours slightly; observing intact lowers them slightly.
- Factor clipping and the `n_scanned` threshold behave as specified.
- Blocked edge changes route choice and hospital access in a small fixture graph.
- Toggle between modelled and updated views never alters the baseline arrays (checksum test).

---

## 16. Frontend

### 16.1 Integration approach for a single-file front end

`index.html` holds the whole UI. Do **not** refactor it wholesale.

- Add `<script type="module" src="/drone-static/drone-module.js"></script>` guarded by a feature probe (`GET /api/drone/config` returns `enabled`).
- Expose minimal hooks from the existing page: `window.__map` (the MapLibre instance), `window.__dispatch` (a small object to add unit rows, call rows and popup buttons), `window.__clock` (get clock). `[VERIFY]` what already exists, and add only what is missing.
- `drone-module.js` exports `init({map, dispatch, api})`. All drone UI mounts from there. If the module fails to load, the page works as before.

### 16.2 Map layers (`drone-layers.js`)

All sources are GeoJSON, updated by throttled `setData` (max 10 Hz for the drone, 1 Hz for the rest). Evaluate `updateData` where the MapLibre version supports it.

| Layer | Geometry | Style | Update |
|---|---|---|---|
| `drone-trail` | line, last 120 s | thin, drone colour, fading | with telemetry |
| `drone-cone` | polygon, camera footprint | translucent fill | 5 Hz |
| `drone-icon` | point, rotated with heading, altitude label (m AGL) | symbol (custom canvas icon) | 10 Hz |
| `drone-route` | line, planned route of the active sortie | dashed | on plan |
| `reach-rings` | polygons, per available station (3 rings) | thin dashed rings with labels (minutes) | on config or station change |
| `no-return-ring` | polygon, around the active drone | warning colour, only when in manual mode | with telemetry |
| `scan-shade` | 50 m cell polygons | translucent dark for unscanned, clear for scanned | diff 1 Hz |
| `building-scan` | feature-state on the buildings source | outline colour by scan state; hatched for verified collapsed | on observation |
| `victims` | points | pin icons coloured by status (unverified, confirmed) | on detection |
| `blocked-roads` | lines | red hatched, "drone verified" label | on road update |
| `unreachable-targets` | tasks | grey outline | on queue change |

Notes:

- Building-level styling needs a stable feature id. If the buildings source is vector tiles, set `promoteId` to the building id. If it is a large GeoJSON, use a separate small GeoJSON for **only scanned buildings** (outline layer on top) to avoid per-feature state on 483k features. `[VERIFY]` and choose the cheaper option. **Never** push 483k features through `setData`.
- Layer order: heat map below, scan shade above heat, buildings, scan outlines, routes, rings, drone icon, victims on top. Respect existing 3D (fill-extrusion) behaviour.
- Layers have toggles in a "Drone" section of the existing layers menu: Drone, Coverage, Reach rings, Victims, Blocked roads, Route.
- Hidden when drone module is off or no drone launched, except reach rings (always available when drones are enabled).

### 16.3 The corner window (F32 to F35)

**Placement and behaviour**

- A container inside the **map area** (not the viewport), so it never overlaps the left summary panel or the right Dispatch panel; it repositions when the Dispatch panel opens or closes (ResizeObserver).
- Default: bottom-right corner of the map area, with a 12 px margin, raised enough to clear the map attribution (about 40 px from the bottom).
- Default size 320 × 200 px. Minimum 240 × 150, maximum 60% of the map area.
- **Draggable** by its header; **snaps** to the four corners with a short animation when released near one; position and module layout persist in `localStorage` (key `drone.window.v1`).
- **Header** (28 px): drone name, mode chip (AUTO / ASSISTED / MANUAL / RTH / HOLD), battery bar with minutes remaining, and buttons: modules menu, swap with map, expand, minimise.
- **Minimise:** collapses to a 36 px pill showing the mode and battery, which still shows alarms.
- **Expand:** overlays 70% of the map area with the same content scaled up; the map remains visible underneath, dimmed, and clickable outside.
- **Swap with map:** exchanges the sizes of the drone view and the map (the map goes small into the corner). Implemented by resizing containers and calling `map.resize()`. If this proves janky, fall back to Expand and drop Swap (log in `PLAN_DELTAS.md`).
- **Auto-minimise** when no drone is launched for 60 s (config), and auto-restore when one launches or an alarm fires.
- Multi-drone later: a drone selector dropdown in the header (the list exists from day one; with one active drone it is hidden).

**Default content**

1. **Live view** (chase camera, three.js): the drone, nearby buildings (flat-shaded boxes), rubble, road debris, the scanned point cloud (coloured by height), detections as pins.
2. **Status bar** (one line): mode chip, battery %, ETA to home, altitude, speed, an assist indicator, alarm flash.
3. A **Take control / Resume auto** button and a **Return home** button.

**Optional modules** (registry pattern; each module can be toggled, and later popped out into a floating panel)

```js
registerModule({
  id: 'depth-cam',
  title: 'Depth camera',
  defaultOn: false,
  mount(el, ctx) { /* ctx: api, events, drone selector, three scene accessors */ },
  unmount() {},
  onFrame(frame) {}                  // called at most 20 Hz with telemetry/state
});
```

| Module | Content | Phase |
|---|---|---|
| `live-view` | The chase view (default) | 2 |
| `status-bar` | Default one-line status | 2 |
| `controls-pad` | Two virtual sticks, hold, return, assist toggle, speed limit slider | 4 |
| `telemetry` | Small plots: speed and nearest return, altitude, battery over time, drift (if shown) | 2 (basic), later |
| `depth-cam` | Rendered depth image from the view (client-side depth material; no streaming) | 3 |
| `task-queue` | The queue (section 12.8), compact | 5 |
| `scorecard` | Coverage, scanned buildings, victims found, distance flown, flight time left | 6 |

**What the window deliberately does not contain:** minimap (the main map replaces it), mission strip, episode controls, cinematic cameras, sun shafts, shadows, post-processing, record-video (the main project can add screen capture later).

### 16.4 The live view (`drone-view.js`)

- three.js vendored locally (no CDN). Use the version already vendored by DroneForest's viewer if recent enough, else a pinned release; record the version in `DECISIONS.md`.
- Scene built from `chunk_geometry` messages (boxes via `InstancedMesh` for speed: one instanced mesh for building boxes, one for rubble, flat road meshes).
- Camera: chase, 12 m behind and 6 m above the drone, smoothed; **free-look** while holding a mouse button; **fixed forward** camera option for manual flying.
- Point cloud: `THREE.Points` with a preallocated buffer (cap at 200k points, drop oldest beyond that or decimate).
- Rendering budget: DPR 1, antialiasing off, no shadows, cap at 20 fps (`requestAnimationFrame` with a time gate), render only when visible (IntersectionObserver plus `document.visibilityState`), pause the loop when minimised.
- Dispose geometries and textures when chunks drop out of range, to avoid memory growth over long sessions.
- Fog of war is **not** drawn in the window; unscanned shading lives on the main map.

### 16.5 Controls (`drone-controls.js`)

- Keyboard and gamepad as in section 11.4. Inputs are sampled at 20 Hz and sent over the websocket as `manual_input` with an increasing `seq`.
- A visible focus ring and "CONTROLS ACTIVE" label. Releasing focus (click on map, Esc) sends a zero input and a `release_control` request.
- Speed limit slider for manual mode (default 8 m/s, max `max_speed`).
- Show assist state and battery warnings prominently when manual.

### 16.6 Dispatch panel additions

- Drone units in the unit list (section 14.1).
- Unverified drone calls with Confirm and Reject (section 14.5).
- A **Recon** section: "Send drone" suggestions for calls that need verification.
- The task queue is **not** in the Dispatch panel by default. It is a window module (and can be shown as a compact section in the Dispatch panel via a toggle).

### 16.7 Main header additions

- Clock control (section 10.5).
- A "Drone" menu: Show/hide layers, Open window, Auto mode on/off (global), Re-roll truth (with confirmation), Drone service status.

### 16.8 Accessibility and polish

- Colour is never the only carrier of meaning (icons and outlines accompany colours).
- Keyboard focus order includes the window; Esc releases control; all buttons have labels.
- Dark theme: the window and all layers follow the existing dark mode.
- Narrow screens (below 900 px): window defaults to the pill; Expand covers the map.

### 16.9 Frontend tests

`[VERIFY]` the existing front-end test approach (none is mentioned in the README). If there is none, add a small Playwright smoke test (optional) that: loads the page with a mocked websocket, checks that the window appears, drags it to a corner, and that the Dispatch panel shows a drone unit. Otherwise rely on manual demo scripts and unit tests for pure JS helpers (snapping, throttling, coordinate conversion).


---

## 17. API reference

Prefix `/api/drone`. All bodies JSON. Errors use the app's existing error shape (`[VERIFY]`); otherwise `{ "error": {"code": "...", "message": "..."} }`. Times are simulation seconds unless named `wall_`.

### 17.1 Service and configuration

| Method and path | Purpose | Notes |
|---|---|---|
| `GET /config` | `{enabled, worker_connected, protocol, fidelity_available, config (public subset), reference_airframe}` | Front end probes this at load |
| `GET /health` | worker status, tick time, device, lag | |
| `POST /worker/restart` | restart the supervised worker | dev only |

### 17.2 Fleet, stations, reachability

| Method and path | Purpose |
|---|---|
| `GET /stations` | stations with availability, reasons, spare batteries, charging timers, drone id |
| `GET /fleet` | all drones, current state |
| `GET /reachability?station=<id>` | rings (GeoJSON), `r_max`, survey-minutes at distances |
| `POST /battery/swap` | operator-triggered early swap (optional) |

### 17.3 Commands

| Method and path | Body | Purpose |
|---|---|---|
| `POST /drones/{id}/launch` | `{sortie_id}` or `{task_id}` | launch a planned sortie |
| `POST /drones/{id}/mode` | `{mode}` | auto, assisted, manual, hold |
| `POST /drones/{id}/take_control` | `{}` | operator takes control, pauses the current task |
| `POST /drones/{id}/release_control` | `{then: "auto"|"hold"|"rth"}` | |
| `POST /drones/{id}/return_home` | `{}` | |
| `POST /drones/{id}/goto` | `{lon, lat, agl}` | assisted single waypoint (feasibility checked) |
| `POST /auto` | `{enabled}` | global auto-mode switch |

Continuous manual input travels over `/ws/drone` as `manual_input` (section 6.6).

### 17.4 Tasks and queue

| Method and path | Purpose |
|---|---|
| `GET /queue` | ordered tasks with scores, components, reachability |
| `POST /tasks` | create a task: `{type, geometry, building_ids?, pinned?}`; returns feasibility |
| `POST /tasks/preview` | feasibility and estimates without creating (used by "Send drone") |
| `PATCH /tasks/{id}` | `{pinned, skipped, manual_rank}` |
| `DELETE /tasks/{id}` | cancel |
| `POST /zones/ban` | `{polygon}` exclude area; `DELETE /zones/ban/{id}` |
| `GET /sorties/{id}` | planned and actual timeline |

### 17.5 Scan, observations, detections

| Method and path | Purpose |
|---|---|
| `GET /scan/summary` | scanned counts, coverage percent, per-neighbourhood percentages |
| `GET /scan/cells?bbox=...` | 50 m cells for the viewport (initial load; later diffs arrive on the websocket) |
| `GET /buildings/{id}/scan` | scan confidence, observed damage, who and when |
| `GET /detections?status=...` | list |
| `POST /detections/{id}/confirm` | confirm, creating or promoting a call |
| `POST /detections/{id}/reject` | reject as false alarm |
| `POST /calls/{call_id}/verify_with_drone` | create a `verify_sos` task |

### 17.6 Model feedback

| Method and path | Purpose |
|---|---|
| `GET /model/status` | `{updated_enabled, observed_buildings, last_update_t, params}` |
| `POST /model/enable` | `{enabled}` apply or stop applying drone observations |
| `GET /model/summary` | modelled vs drone-updated totals |
| `POST /model/refresh_ranges` | trigger the background uncertainty refresh |
| `POST /truth/reroll` | new seed (destroys scan state; confirmation required in UI) |

### 17.7 Clock

| Method and path | Purpose |
|---|---|
| `GET /clock` | `{sim_t, speed, paused}` |
| `POST /clock` | `{paused?, speed?}` |

### 17.8 Exports (Phase 8)

| Method and path | Purpose |
|---|---|
| `GET /export/pointcloud.ply?drone=` | accumulated point cloud |
| `GET /export/victims.geojson` | detections and calls from drones |
| `GET /export/coverage.geojson` | scanned cells and buildings |
| `GET /export/events.ndjson` | event log |
| report | add a "Drone operations" section to the existing PDF: sorties, coverage, victims found, model changes |

### 17.9 WebSockets

| Path | Purpose |
|---|---|
| `/ws/worker` | worker connection (section 6) |
| `/ws/drone` | browser events and `manual_input` |

---

## 18. Configuration

All tunables live in `drone/config.py` with environment overrides (`DRONE_<NAME>`). Group them in a dataclass and expose a public subset through `/api/drone/config`. Defaults below match earlier sections.

| Group | Name | Default | Notes |
|---|---|---|---|
| Feature | `DRONE_ENABLED` | `1` if torch and websockets import, else `0` | master switch |
| | `DRONE_WORKER_AUTOSTART` | `1` | supervised subprocess |
| | `DRONE_DEVICE` | `auto` | `auto`, `cuda`, `cpu` |
| | `DRONE_MAX_ACTIVE` | `1` | simultaneously flying drones (raise later) |
| | `DRONE_SEED` | `1` | truth and flight seed |
| Clock | `DRONE_SPEEDS` | `1,2,4` | allowed multipliers |
| Airframe | `battery_wh`, `p_hover_w`, `p_payload_w`, `k_forward`, `k_wind`, `eta_climb`, `mass_kg` | section 10.1 | |
| | `cruise_speed_ms`, `survey_speed_ms`, `max_speed_ms`, `climb_rate_ms` | 15, 8, 18, 3 | |
| | `reserve_fraction` | 0.25 | |
| | `launch_delay_s`, `swap_time_s`, `recharge_time_s`, `spare_batteries_per_station` | 180, 300, 3600, 3 | |
| Sensing | `lidar_range_m` | 60 | city-scale, larger than forest default |
| | `lidar_rays` | 80 | same as DroneForest by default |
| | `camera_fov_deg` | 70 | |
| | `swath_m` | 60 | constant in v1 |
| | `scan_samples_per_building` | 40 | L1 |
| | `scanned_threshold`, `partial_threshold` | 0.60, 0.10 | |
| Detection | `p_detect_low_alt`, `p_detect_high_alt` | 0.9, 0.3 | linear with altitude 30 to 120 m |
| | `false_alarms_per_km2` | 0.3 | |
| | `merge_radius_m`, `merge_window_s` | 15, 600 | |
| | `auto_confirm_threshold` | off | |
| Truth | `p_trapped_collapsed`, `p_exposed_given_trapped`, `p_trapped_partial`, `p_escape` | 0.35, 0.25 (0.5 for partial), 0.05, 0.6 | |
| | `observation_accuracy` | 0.9 | confusion matrix |
| World | `chunk_size_m` | 250 | |
| | `storey_height_m`, `default_height_m` | 3.0, 6.0 | |
| | `rubble_collapsed_height_frac` | 0.10 to 0.25 | |
| Planner | `block_size_m`, `min_buildings_per_block` | 250, 5 | |
| | `w_v`, `w_d`, `w_sos` | 1.0, 2.0, 15.0 | |
| | `survey_overlap`, `clearance_m`, `max_alt_m` | 0.2, 20, 120 | |
| | `min_sortie_value` | tune | avoid pointless flights |
| Feedback | `feedback_enabled` | `1` | applying observations |
| | `feedback_lambda`, `feedback_min_scanned`, `feedback_clip` | 0.3, 8, 0.5 to 2.0 | |
| UI | `window_default_corner` | `bottom-right` | |
| | `view_fps`, `view_dpr` | 20, 1 | |
| | `telemetry_hz` | 10 | |

---

## 19. State machines and edge cases

### 19.1 Drone status

```
offline ──(worker + station ok)──▶ ready ──(launch authorised)──▶ launching ──(delay elapsed)──▶ takeoff
   ▲                                 ▲                                                            │
   │                                 │                                                            ▼
waiting_battery ◀──(no spare)── swapping ◀──(landed)── landing ◀── returning ◀── surveying ◀── transit
                                                                       ▲              │            │
                                                          (RTH or sortie done)────────┘            │
                                                                       ▲                           │
                                                                       └───────(RTH forced)────────┘

 hold  : entered from any airborne status; resumes the previous status when released
 manual: an overlay on airborne statuses (transit, surveying, hold); not a separate status
 lost / crashed : terminal for the scenario (config), station keeps its spare batteries
```

Status transitions are emitted by the worker (`status`) for flight phases and by the app for ground phases (`ready`, `launching`, `swapping`, `waiting_battery`).

### 19.2 Task and sortie states

- Task: `queued → planned → active → done`; side exits: `skipped`, `cancelled`, `failed` (for example: forced RTH before completion, drone lost, unreachable after conditions changed). A partially complete survey task returns to `queued` with its **remaining** area, not from scratch (block coverage state is kept).
- Sortie: `planned → launched → surveying → returning → landed → closed`; `aborted` on forced RTH; `failed` on loss.

### 19.3 Detection and call states

- Detection: `unverified → confirmed | rejected | merged`.
- Drone call: `unverified → open → en route → done`, or `unverified → closed (false alarm)`.

### 19.4 Edge cases to handle and test

| Situation | Expected behaviour |
|---|---|
| User re-runs the scenario while a drone is flying | Recall all drones (immediate RTH, then reset), clear scan state, regenerate truth, rebuild chunks, show a banner "Scenario changed, drones recalled" |
| User switches area | Same as above, plus new stations and frame; drone UI resets |
| New scenario with the same parameters and seed | Truth identical (cached); optionally keep scan state (config, default reset) |
| Server restart mid-flight | App restores incident state from snapshot and log; drones in the air are marked `lost` unless the worker also survived and re-registers; worker reconnect re-syncs fleet and chunks |
| Worker crash | Units `offline`, UI "Drone service lost"; supervisor restarts; drones that were airborne are `lost`; landed drones restore |
| Browser reload | Receives `snapshot`; window restores layout; control ownership returns to auto |
| Two tabs | Both see state; only one holds manual control |
| Operator disconnects in manual mode | Dead-man hover after 500 ms, then link-lost logic after 5 s |
| No station reachable for a task | Task greyed with reason; sortie planner skips it |
| Station becomes unavailable mid-scenario (e.g., rerun with other parameters) | Drone returns to the nearest available station if it has energy, else `lost` |
| Battery below reserve at launch | Launch refused with explanation |
| No spare battery | `waiting_battery` with a timer |
| Drone returns but station is now unavailable | Land at nearest available station; if none, land in place and mark `stranded` |
| Wind change during flight | Energy estimates update; RTH may trigger earlier |
| Detection at the exact boundary between buildings | Assign to the nearest building by footprint distance |
| Task polygon contains no buildings | Rejected with message |
| Building taller than `max_alt - clearance` | Task flagged `too_tall`; surveyed at `max_alt` with a warning |
| Clock paused during manual control | Input is ignored with a notice; the drone is frozen |
| Speed multiplier above 2× in manual | Manual disabled with notice |
| Worker lags | Sim clock slows; UI shows lag indicator |
| Memory growth over long sessions | Chunk LRU, point cloud caps, scan grid sparse tiles, event log rotation |

---

## 20. Testing strategy

### 20.1 Principles

- **Plain-Python units first.** Most logic (georef, energy, priority, planner, truth, feedback, scan, protocol) is pure Python and testable without torch or a browser.
- **Fast by default.** The drone test suite runs in under 60 s without GPU. Slower tests are marked `@pytest.mark.slow`.
- **Fixtures.** A tiny synthetic city (about 50 buildings, 3 stations, a small road graph) in `tests/drone/fixtures/`, plus a loader that builds the same `scenario` object the app produces. Reuse existing fixtures where possible `[VERIFY]`.
- **Deterministic.** Fixed seeds in every test.
- **Do not weaken existing tests.** Run the existing suite in CI alongside the new one.

### 20.2 Test list (new, under `tests/drone/`)

| Area | Tests |
|---|---|
| `georef` | round trip, known distances, chunk origin determinism, yaw conversion |
| `protocol` | pydantic models round-trip; schema export stable (snapshot test); unknown major version rejected; sequence gap detection |
| `energy` | endurance acceptance table (10.6), monotonicity, climb cost, wind effect, return estimate vs simulated return |
| `clock` | pause and resume, speed changes, monotonic time, adapter for dispatch ETAs `[VERIFY]` |
| `stations` | one drone per available station; unavailable ones offline; spare battery bookkeeping across sorties; recharge timing |
| `truth` | determinism, seed sensitivity, statistical agreement with model, survivors only in damaged buildings, edge blockage sampling |
| `world/raycast` | OBB raycast vs shapely; chunk determinism; unique ids; damaged geometry shapes; benchmark guards (marked slow) |
| `flyer` | follows a waypoint list; stops before obstacles with collision assist; no motion when held; dead-man behaviour |
| `scan` | monotonic scan confidence; thresholds; coverage grid vs building states; neighbourhood percentages |
| `detection` | probability vs altitude; false alarm rate; merging rules; SOS corroboration |
| `priority` | monotonicity, info term peak at 0.5, SOS bump, manual rank ordering |
| `planner` | energy-feasible property test; estimate vs simulation within 5%; insertion and 2-opt improve or keep value; time budget |
| `feedback` | baseline unchanged; posterior behaviour; neighbour factor clipping; aggregates equal baseline with no overrides; toggle checksum |
| `bridge/api` | endpoints with a fake worker; manual input to dead-man; snapshot on connect; backpressure drop of telemetry |
| `integration` | fake worker + app + simulated browser client: full sortie, from task creation to landing, with event log assertions; replay test (log replay reproduces final state) |
| `existing-regression` | run existing suite with `DRONE_ENABLED=0` and `=1` |

### 20.3 Manual demo scripts (kept in `DEMO_SCRIPT.md`)

One per milestone (M1 to M6), each a numbered click-by-click sequence with expected observations, so the owner can verify quickly.

### 20.4 Quality gates per phase

A phase is done only when: new tests pass, existing tests pass, the demo script for its milestone works end to end on the owner's machine class, `DISCOVERY.md`/`PLAN_DELTAS.md`/`SIMPLIFICATIONS.md` are up to date, and no new warnings appear in the server log during the demo.

---

## 21. Performance budgets

Target machine: laptop with a 4 GB RTX 3050 (mobile), also a CPU fallback.

| Item | Budget |
|---|---|
| Worker tick (1 drone, L1, 80 rays, dense chunk) | ≤ 10 ms GPU, ≤ 25 ms CPU (at `dt` = 50 ms the loop must keep up at 1×) |
| Worker GPU memory | ≤ 1.5 GB |
| Planner (K = 30) | ≤ 200 ms |
| Priority recomputation after an observation batch | ≤ 100 ms |
| Aggregates recompute (incremental) | ≤ 500 ms for 20 observations on Noida; full recompute as in baseline |
| Websocket traffic per browser | ≤ 100 KB/s average (telemetry 10 Hz ≈ 5 KB/s, point cloud ≤ 50 KB at 2 Hz at worst, scan diffs small) |
| Chunk build (250 m, dense) | ≤ 300 ms on first build; cached afterwards |
| Browser: corner window | ≥ 20 fps on integrated graphics while the 3D map runs at ≥ 30 fps; window CPU under 10% of a core when idle |
| Map layer updates | drone layers ≤ 10 Hz; others ≤ 1 Hz; no layer pushes more than 5k features per update |
| Start-up with drone enabled | adds ≤ 2 s to app start (worker start is async) |
| Memory growth | flat over a 2 hour session (check with a soak test) |

`tools/drone_bench.py` reports tick time, raycast time vs boxes, chunk build time, and message rates; run it at the end of Phase 3 and Phase 6 and store results in `docs/integration/PERF.md`.

---

## 22. Risks and mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | The heuristic flyer avoids obstacles poorly in dense urban boxes (README: modest success on dense disaster arenas) | High | Medium | Fly above roof level by default; use the flyer only for tracking waypoints; add the safety filter for manual flight; keep L0 as a fallback; later train or improve avoidance (F39) |
| R2 | `index.html` is monolithic; hooks are hard to add | Medium | Medium | Add only a tiny hook surface; keep all drone UI in separate modules; document hook points in `DISCOVERY.md` |
| R3 | Two WebGL contexts (MapLibre 3D + three.js) overload a 4 GB GPU | Medium | High | Low-res, 20 fps cap, no shadows, pause when hidden, instanced meshes, dispose chunks; offer "view off" |
| R4 | Torch is a heavy optional dependency | Medium | Medium | Optional extra, L0 kinematic works without torch (kinematic backend uses numpy only) |
| R5 | Scale: 483k buildings | High if mishandled | High | Chunking, grid culling, no full-city GeoJSON updates, sparse scan state; benchmarks as gates |
| R6 | Aggregation code can't be re-run with overrides | Medium | High for F29 to F31 | Phase 0 finds out; minimal refactor with identical-output test; if too hard, defer F30 and show overrides only at building level |
| R7 | Dispatch timing uses wall-clock, not a sim clock | Medium | Medium | Adapter behind the feature flag; if too intrusive, drone runs on a clock that the dispatch demo also reads |
| R8 | Truth sampled from the model gives misleading "validation" | Certain | Reputational | UI note, docs, P9; never present results as validation of fragility curves |
| R9 | The physics drone's tuning drifts from the energy model | Medium | Medium | One shared `energy.py`; the worker drains battery from actual motion using the same function; acceptance tests in 10.6 |
| R10 | Websocket backpressure and stale telemetry | Medium | Low | Latest-only telemetry, queue limits, snapshot on reconnect |
| R11 | Float32 precision at city scale | Medium | Medium | Float64 on server; chunk frames; rendering relative to a chunk origin |
| R12 | Determinism broken by GPU nondeterminism | Medium | Low | Fixed dt; CPU determinism tests; document that GPU runs may differ at the last bits |
| R13 | Scenario change mid-flight corrupts state | Medium | High | Explicit recall-and-reset flow (19.4), tests |
| R14 | Phone SOS flow regresses | Low | High | Do not modify `sos.html`; existing tests; integration test that creates a phone SOS with the drone module on |
| R15 | Scope creep (multi-drone, real detection, drift) | High | Medium | Features F36 to F40 are explicitly later; any addition is logged in `DECISIONS.md` and costed |
| R16 | MapLibre `setData` stutter with many layers | Medium | Medium | Throttle, small sources, evaluate `updateData`, avoid large feature counts |
| R17 | Overpass or data availability issues (Overture used instead, per project history) | Low | Medium | Drone module reads from the already-loaded area; no new downloads |
| R18 | OBB approximation produces odd world shapes for large footprints | Medium | Low | Optional multi-OBB split; declared simplification |

---

## 23. Phased roadmap

Each phase lists **goal, tasks, files, deliverables, acceptance, and out-of-scope**. Do one phase at a time, run the quality gates (20.4), report changed files only, then wait for the owner's go-ahead.

### Phase 0: Discovery and spikes (no feature code)

**Goal:** replace every `[VERIFY]` with a fact, de-risk the three riskiest assumptions, and correct this plan.

**Tasks**

1. Read both repos end to end at the level needed. Fill `docs/integration/DISCOVERY.md` with: file and function names, data shapes, how things connect, for every `[VERIFY]` in sections 2, 8, 14, 15 and 16.
2. **Spike A (data):** load a saved area (Noida) and extract per-building arrays (id, footprint, height, occupants, damage probabilities). Time it. Report memory size and the fastest access path for the app.
3. **Spike B (geometry):** build one 250 m chunk of OBBs from real Noida footprints; run DroneForest's raycast adapted to OBBs; benchmark with 80 rays at the box counts found. Report ms per scan on GPU and CPU.
4. **Spike C (flyer):** run the heuristic flyer at fixed altitude above roof level over that chunk for 60 s along a straight route and along a survey path; report collisions and path tracking error. Also test avoidance by descending to 8 m above ground in a street.
5. **Spike D (front end):** add a throw-away floating `<div>` and a minimal three.js canvas to `index.html` with a dummy drone moving over the map; check frame rates with the 3D map active.
6. Identify the integration points: where to hook unit rows, call rows, popup buttons, header controls, and layer menu items.
7. Confirm requirements: Python version, torch version, GPU memory on the owner's machine (ask the owner to run a short script only if needed).
8. Write `PLAN_DELTAS.md` with the corrections to this document.

**Deliverables:** `DISCOVERY.md`, `PLAN_DELTAS.md`, spike scripts under `tools/spikes/` (kept, small), benchmark numbers.

**Acceptance:** every `[VERIFY]` is answered or explicitly marked "unknown, owner decision needed"; spikes produce numbers; the owner approves the corrected plan.

**Out of scope:** any change to production code.

### Phase 1: Foundation (M1 part 1)

**Goal:** a drone unit exists per available police station in Dispatch; it can launch, fly a simple route with a real battery model, and land, entirely through the protocol; no UI beyond Dispatch rows and a minimal debug view.

**Tasks**

1. `drone/config.py`, `drone/protocol.py` (models and schema export), `drone/georef.py`, `drone/energy.py`, `drone/clock.py` and tests for each.
2. `drone/state.py`: incident store, event bus, ndjson log, snapshots.
3. `drone/stations.py`: adapters over the existing facility and unit code; one drone per available police station; spare batteries and swap lifecycle (F10).
4. `drone/bridge.py`: router, `/ws/worker`, `/ws/drone`, `/api/drone/config|fleet|stations|clock`, worker supervisor.
5. `drone/worker/` with the L0 **kinematic** backend (numpy only): point-mass dynamics, waypoint following, altitude hold, energy drain, forced RTH, status changes.
6. Extend `live.py` with `kind = "drone"` units and a Dispatch panel row (name, status chip, battery bar). Minimal hook in `index.html`.
7. Clock adapter so team ETAs follow the sim clock (flag-guarded), plus the header clock control.
8. A temporary debug page or panel listing telemetry (removed in Phase 2).

**Acceptance**

- `DRONE_ENABLED=0` leaves existing behaviour and tests unchanged.
- With the module on and no torch installed, the app starts and drones work (L0).
- Launching a hard-coded 3 km out-and-back route lands with at least the reserve; endurance acceptance table (10.6) passes for the kinematic backend.
- Killing and restarting the worker marks units offline and restores them, with no server restart.
- Unavailable stations show an offline drone with a reason.
- Event log can be replayed to reproduce final drone states (replay test).

**Out of scope:** physics, lidar, scan state, windows.

### Phase 2: Corner window and map presence (M1)

**Goal:** the drone is visible and tangible in the UI: map layers, reachability rings, corner window with a live view.

**Tasks**

1. `web/drone/`: module loader, `drone-api.js`, `drone-layers.js`, `drone-window.js` with drag, snap, minimise, expand (swap optional), persistence, module registry, status bar (F32 to F35).
2. `drone-view.js`: three.js chase view with simple boxes for buildings from `chunk_geometry` (app builds a simple chunk from footprints, no rubble yet), drone model, trail.
3. Map layers: drone icon, trail, cone (from the kinematic backend's analytic footprint), planned route, reachability rings (F8, F19), layer toggles.
4. `GET /reachability`, rings computed by `energy.py`.
5. Header "Drone" menu; auto-minimise rule; window modules: live-view, status-bar, telemetry (basic).
6. Remove the Phase 1 debug panel.
7. Performance checks against section 21 (browser items).

**Acceptance**

- Launch a drone from the Dispatch row; watch it fly in the window and on the map; battery bar updates; rings show correct ranges for each station.
- The window never overlaps the left or right panels; it re-snaps when the Dispatch panel toggles; its layout persists across reloads.
- 3D map plus window sustain the section 21 frame rates on the owner's machine; the window pauses when minimised or hidden.
- Disabling the module (`DRONE_ENABLED=0`) removes all drone UI.

**Out of scope:** user control, rubble, scan, detections.

### Phase 3: The real world and real physics (M2 part 1)

**Goal:** the drone flies through a generated city chunk with rubble, using DroneForest dynamics and lidar.

**Tasks**

1. `drone/truth.py`: sampling (section 9), caching, tests.
2. Chunk builder in the app (section 8): OBB generation, damage geometry, road debris, survivors, chunk cache, `world_chunk` and `chunk_geometry` messages.
3. `drone/sim/raycast.py` (OBB slab raycast, ground), `world.py` (chunk world, culling, grid), `lidar.py`; benchmarks (`tools/drone_bench.py`).
4. `drone/sim/dynamics.py` adapted from DroneForest and re-parameterised to the reference airframe; `flyer.py` (waypoint follower, avoidance); L1 backend in `worker/physics.py`, including the drain of battery from actual motion.
5. Re-run acceptance tests of 10.6 against L1 (tune airframe parameters until they pass; planner/estimate tolerance 5%).
6. `scan_events` from lidar (building ids), the `cone` event, point cloud events; the window shows the point cloud and the rubble world; `depth-cam` module.
7. CPU fallback test (1×, one drone).

**Acceptance**

- A drone flies a 2 km route and a survey pattern over a dense Noida chunk at 1× on GPU without a collision; on CPU at 1×.
- Collapsed buildings look collapsed in both views; blocked roads have debris.
- The same scenario and seed produce the same world and, for the same commands, the same flight (determinism test).
- Tick and memory budgets in section 21 are met (record in `PERF.md`).

**Out of scope:** manual control, planner, detections.

### Phase 4: Control (M2)

**Goal:** the operator can click targets and fly manually; failsafes work.

**Tasks**

1. Control ownership and handover (section 11.2); `take_control`, `release_control`.
2. Manual pipeline: input smoothing, look-ahead waypoint generation, dead-man, collision assist (safety filter), assist indicator (F13 to F15).
3. `drone-controls.js` (keyboard, gamepad, on-screen pad), `controls-pad` module, manual battery warning and point-of-no-return ring.
4. Assisted tasks: building inspect, road check, polygon survey with the survey path generator (F12), feasibility preview (`POST /tasks/preview`), **Send drone** in the building popup (F24 send).
5. Failsafes (11.6): forced RTH, link lost, geofence; related alarms and UI banners.
6. Tests for all of the above, including the failsafe tests with the integration harness.

**Acceptance**

- Fly manually through a street at 10 m above ground with assist on: no collision when pushing toward walls; with assist off the drone can crash.
- Take control mid-sortie, fly for a while, resume auto: the drone continues sensibly.
- Click a building: preview shows feasibility and energy; confirm: the drone flies an inspection ring and returns.
- Battery warning appears; forced RTH triggers and lands with the reserve intact.
- Releasing keyboard focus hovers the drone; killing the browser tab triggers the dead-man and then link-lost RTH.

**Out of scope:** priority queue and auto planning.

### Phase 5: Priority and auto mode (M3)

**Goal:** the drone chooses and flies sorties by priority and the operator can steer the queue.

**Tasks**

1. `priority.py`: block creation, value and score (section 12.3), components, tests.
2. `planner.py`: reachability per task and station, survey path generator refinements, sortie planner (insertion + 2-opt), time budget, tests (F9, F16).
3. Auto loop (12.7) with SOS bump (F18) and the rule for not abandoning near-complete tasks (F11).
4. Queue API and UI: `task-queue` module, Why popover, pin, skip, reorder, ban zones, unreachable greying (F17, section 12.8).
5. Turnaround and spare battery logic fully exercised across multiple sorties (F10).
6. Global Auto on/off switch; header menu.
7. Integration test: with auto on and a seeded scenario, a drone flies three sorties without violating energy limits, each starting where the planner says.

**Acceptance**

- Auto mode launches the highest-value feasible sortie; the queue order changes sensibly when a phone SOS is created.
- Pinning, skipping and banning visibly change what the drone does next.
- After a landing the drone swaps battery, waits if none is available, then relaunches.
- Planner estimates match simulated energy within 5 percent on three different sorties.
- Unreachable tasks are greyed with the correct reason.

**Out of scope:** scan state shading, detections, model feedback.

### Phase 6: Scan state, observations, detections and dispatch (M4)

**Goal:** scanning changes the map; people found become dispatchable calls.

**Tasks**

1. `drone/scan.py`: per-building scan confidence, coverage grid, thresholds, observed damage stub (F20, F21), neighbourhood scan percentages (F23).
2. Map layers: scan shade, building outlines, verified styling, legend entries (F20, F21).
3. Detection model in the worker and the app: probabilities, false alarms, merging, SOS corroboration (F25, F26).
4. Dispatch: unverified drone calls with Confirm and Reject (F27), Recon suggestion, **Verify with drone** (F24 verify), ranking with confidence, existing team sending (F28).
5. Victim pins layer and the `scorecard` module.
6. Road observation display only (blocked-roads layer; no model update yet) (F22).
7. Tests for scan monotonicity, merging, state transitions, and the integration test for "drone finds a person, call appears, team is sent".

**Acceptance**

- Fly over a block: buildings change from unscanned to scanned; shading clears; neighbourhood percentages rise.
- A drone finds an exposed survivor; an unverified call appears; Confirm sends the nearest working team on the debris-aware road graph.
- A phone SOS near a drone detection merges and shows "drone-corroborated".
- Reject closes the call as a false alarm; false alarms occur at roughly the configured rate over many sorties.

**Out of scope:** model updates.

### Phase 7: Feedback into the model (M5)

**Goal:** drone observations improve the displayed damage picture, with an honest side-by-side view.

**Tasks**

1. Refactor `simulate.py` minimally to expose `recompute_aggregates(prob_overrides, road_overrides)`; add the identical-output test with no overrides.
2. `drone/feedback.py`: building posterior, neighbourhood factor, parameters, tests (F29).
3. Aggregate recomputation (incremental, debounced); the Modelled / Drone-updated toggle; summary panel with both numbers (F30).
4. Road graph override hook and re-routing of teams in progress; hospital access and dispatch order update (F31).
5. Priority engine reads drone-updated probabilities, so the next sortie reflects what was learned.
6. "Refresh ranges" button wired to the existing background run (if feasible; otherwise document the limitation).
7. UI note about simulated truth (P9).

**Acceptance**

- Observing a collapsed building changes the heat map locally and the totals slightly; toggling views shows both states; baseline checksum is unchanged after any number of updates.
- A blocked road observed by the drone changes a route and a hospital access time visibly, with a "re-routed, +N min" note.
- The next planned sortie differs from the one planned before observations when the observations matter.

**Out of scope:** statistical validation of the model.

### Phase 8: Polish, fast-forward, exports, docs (M6)

**Goal:** make it demo-ready and robust; add the cheap parts of the "later" list.

**Tasks**

1. L0 fast-forward at 10×, 30×, 60× using the kinematic backend with analytic scanning (a speed multiplier above 4× switches to L0 when no manual control is active); consistency test between L0 and L1 results at the same speed.
2. Exports (F40): point cloud `.ply`, victims `.geojson`, coverage `.geojson`, event log, PDF report section.
3. Replay tool and timeline scrubber (basic): re-run an event log in the UI.
4. Swap-with-map polish, pop-out modules, accessibility pass, dark theme pass.
5. Soak test (2 hours at 4× with random operator actions); memory and performance reports.
6. Documentation: user guide (how to run the drone, modes, queue, controls), developer guide (architecture, protocol, adding a module), update the main README sections (only if the owner asks for README changes), `DEMO_SCRIPT.md` final.
7. Collect all `SIMPLIFICATIONS.md` entries into a user-visible "How much to trust the drone simulation" panel, matching the existing "How much to trust this" panel.

**Acceptance:** all earlier acceptance checks still pass; soak test is flat; the owner signs off a final demo.

### Later (separate plans)

- **F36 multiple drones:** raise `DRONE_MAX_ACTIVE`; assignment by auction; deconfliction (altitude layers per drone); the drone selector in the window header.
- **F37 footprint-aided drift correction:** DroneForest's INS drift random walk, bounded by matching lidar returns to known building footprints; show true, believed and corrected paths.
- **F38 evaluation harness:** seeded scenarios, metrics (time to first victim found, victims per hour, damage-map error versus truth), with and without drones, with different station layouts.
- **F39 RL on city worlds:** train policies on chunk worlds with rubble and oriented obstacles; depth-image CNN; replace the heuristic flyer.
- **Other hazards:** cyclone and flood by plugging their damage states into the same truth mapping.
- **Real drone adapter:** implement the drone-to-app contract from MAVLink or ROS.

---

## 24. Decisions already taken

These are defaults. Use them without asking. Log any change in `DECISIONS.md`.

| # | Decision | Default |
|---|---|---|
| D1 | Repo layout | Vendor only the needed DroneForest pieces into `quake-map/disaster/drone/sim/`; don't import the DroneForest repo as a dependency |
| D2 | Worker transport | Worker is a websocket client of the app (`/ws/worker`) |
| D3 | State persistence | In-memory incident state plus ndjson event log plus periodic scan snapshots |
| D4 | Terrain | Flat ground at z = 0 |
| D5 | Collider shape | Minimum rotated rectangle (OBB) per footprint; multi-OBB split optional |
| D6 | Heights | Measured height, else `storeys × 3.0 m`, else 6 m |
| D7 | Click-to-fly | Yes, in assisted mode (building, road, polygon). Manual mode is keyboard, gamepad, and on-screen pad only |
| D8 | Station availability | Same rule as the existing grey-out (likely-damaged building ⇒ unavailable); truth is not used |
| D9 | Observed damage | Noisy truth through a confusion matrix (accuracy 0.9), behind a replaceable classifier interface |
| D10 | Feedback display | Baseline preserved; Modelled vs Drone-updated toggle; both numbers in the summary |
| D11 | Window default | Bottom-right corner of the map area, 320 × 200, auto-minimise when idle |
| D12 | Hazards | Earthquake only in v1 |
| D13 | Active drones | One at a time (`DRONE_MAX_ACTIVE = 1`); all stations list a drone unit |
| D14 | 3D view | three.js, vendored, no post-processing, no shadows, 20 fps cap |
| D15 | Fidelity | L0 (kinematic) from Phase 1, L1 (physics and lidar) from Phase 3; fast-forward via L0 in Phase 8 |
| D16 | Altitude strategy | Transit and survey above the tallest building in the area of operation plus 20 m (max 120 m); low flying only in assisted inspection and manual |
| D17 | Auto-confirm detections | Off |
| D18 | Re-running a scenario | Recalls drones, resets scan state, regenerates truth (cached by seed) |
| D19 | Phone SOS page | Unchanged |
| D20 | Docs | Don't create README or zip files unless the owner asks; report changed files only |

---

## 25. Definition of done

The integration is done when all of these hold:

1. With the drone module on, the disaster dashboard shows a drone unit for each available police station in Dispatch, with correct status and battery.
2. A small, draggable corner window shows the drone's live view; the main map shows the drone, its route, its camera footprint, reachability rings, scan state, victims and blocked roads.
3. The operator can fly manually (keyboard or gamepad), click a target for assisted flight, take control and release it at any time, and Return Home at any time.
4. In auto mode the drone flies sorties to the highest-value feasible areas, within realistic flight time, and the operator can see and edit the queue and see why each target ranks where it does.
5. Flight time behaves realistically: roughly 25 to 35 minutes of endurance, a mandatory return reserve, battery swaps between sorties, a limited number of spare batteries, and visible reachability.
6. The drone detects people probabilistically; detections become unverified calls; confirmed calls use the existing dispatch flow and debris-aware routing; duplicates merge; phone SOS calls can be verified by drone.
7. Drone observations update a drone-updated view of the damage picture (heat map, totals, priorities, hospital access, routes) without changing the baseline, and the UI says the observations come from a simulated disaster.
8. The disaster app runs unchanged when the drone module is off, with the existing tests passing in both configurations.
9. One shared clock with pause and speed control drives both Dispatch and the drone.
10. All simplifications are documented and visible to users; `DISCOVERY.md`, `PLAN_DELTAS.md`, `DECISIONS.md`, `SIMPLIFICATIONS.md`, `DEMO_SCRIPT.md` and `PERF.md` are complete.
11. All acceptance sections for Phases 0 to 8 pass, and the soak test shows flat memory.

---

## Appendix A: Kickoff prompt

Paste this as the first message to Claude Code in the disaster repo, with this plan saved at `docs/integration/PLAN.md`.

```text
You are implementing the "Drone × Disaster" integration described in docs/integration/PLAN.md.

Rules:
- Read PLAN.md sections 0 to 3 and 23 (Phase 0) first.
- Do Phase 0 only: no production code changes. Produce docs/integration/DISCOVERY.md,
  PLAN_DELTAS.md and the spike scripts under tools/spikes/. Answer every [VERIFY] item.
- The Drone-SLAM repo (DroneForest terrain part only) is at <path or URL>. Do not use SplatDrone
  or the asteroid code.
- Do not create README files or zip archives. When you report, list only the files you changed.
- Do not claim anything works unless you ran it. Show the commands and outputs.
- Take the defaults in PLAN.md section 24. Ask me only if you are blocked.
- Stop at the end of Phase 0 and wait for my approval before starting Phase 1.
```

For each later phase, repeat with: *"Do Phase N only, following its tasks and acceptance criteria; stop and report afterwards."*

---

## Appendix B: Example messages

### B.1 `fleet_init` (app → worker)

```json
{
  "v": 1, "type": "fleet_init", "seq": 3, "t": 0.0,
  "payload": {
    "seed": 1,
    "drones": [
      {"id": "drn-ps-0412", "station_id": "ps-0412", "home_xy": [1520.4, -230.8], "home_yaw": 0.0, "battery_wh": 200.0},
      {"id": "drn-ps-0977", "station_id": "ps-0977", "home_xy": [-3105.2, 4420.1], "home_yaw": 1.57, "battery_wh": 200.0}
    ]
  }
}
```

### B.2 `launch` (app → worker)

```json
{
  "v": 1, "type": "launch", "seq": 17, "t": 182.0, "drone_id": "drn-ps-0412",
  "payload": {
    "sortie_id": "s-0007", "mode": "auto",
    "route": [
      {"x": 1520.4, "y": -230.8, "z": 0.0,  "speed": 0.0,  "scan": false, "kind": "takeoff"},
      {"x": 1520.4, "y": -230.8, "z": 62.0, "speed": 3.0,  "scan": false, "kind": "climb"},
      {"x": 2890.0, "y": 410.0,  "z": 62.0, "speed": 15.0, "scan": false, "kind": "transit"},
      {"x": 2900.0, "y": 420.0,  "z": 62.0, "speed": 8.0,  "scan": true,  "kind": "survey", "task_id": "t-5531"},
      {"x": 3140.0, "y": 420.0,  "z": 62.0, "speed": 8.0,  "scan": true,  "kind": "survey", "task_id": "t-5531"},
      {"x": 1520.4, "y": -230.8, "z": 62.0, "speed": 15.0, "scan": false, "kind": "return"},
      {"x": 1520.4, "y": -230.8, "z": 0.0,  "speed": 2.0,  "scan": false, "kind": "land"}
    ]
  }
}
```

### B.3 `telemetry` (worker → app)

```json
{
  "v": 1, "type": "telemetry", "seq": 90412, "t": 611.3, "drone_id": "drn-ps-0412",
  "payload": {
    "pose": {"x": 2933.1, "y": 421.7, "z": 62.1, "yaw": 0.02},
    "velocity": [7.9, 0.1, 0.0],
    "battery_wh": 131.6, "energy_to_home_wh": 41.8,
    "mode": "auto", "status": "surveying", "assist_active": false,
    "wind": {"speed": 3.1, "dir": 2.4}, "nearest_range_m": 48.2
  }
}
```

### B.4 `scan_events` and `detections` (worker → app)

```json
{
  "v": 1, "type": "scan_events", "seq": 90420, "t": 611.5, "drone_id": "drn-ps-0412",
  "payload": {
    "buildings_seen": [{"building_id": "b88123", "fraction_seen_delta": 0.08}, {"building_id": "b88130", "fraction_seen_delta": 0.15}],
    "road_edges_seen": [{"edge": "e4412", "blocked": true}]
  }
}
```

```json
{
  "v": 1, "type": "detections", "seq": 90433, "t": 614.0, "drone_id": "drn-ps-0412",
  "payload": {"candidates": [{"x": 2944.2, "y": 433.0, "building_id": "b88130", "confidence": 0.74, "est_people": 1}]}
}
```

### B.5 `manual_input` (browser → app, forwarded to worker)

```json
{"type": "manual_input", "vx": 4.0, "vy": 0.0, "vz": 0.0, "yaw_rate": 0.3, "seq": 881, "collision_assist": true}
```

### B.6 `observation` (app → browser)

```json
{
  "v": 1, "type": "observation", "seq": 5521, "t": 612.0,
  "payload": {
    "building_id": "b88123", "observed": "collapsed", "scan_confidence": 0.63,
    "by": "drn-ps-0412", "modelled": {"p_collapse": 0.41},
    "neighbourhood": {"id": 3, "scanned_pct": 0.18}
  }
}
```

### B.7 `task_queue` item (app → browser)

```json
{
  "id": "t-5531", "type": "survey_block", "status": "active", "pinned": false,
  "value": 41.7, "score": 0.62, "est_energy_wh": 67.2, "est_duration_s": 560,
  "components": {"victims_term": 28.1, "info_term": 11.9, "sos_term": 0.0},
  "reachable_from": ["ps-0412"], "assigned_drone": "drn-ps-0412"
}
```

---

## Appendix C: Glossary

| Term | Meaning |
|---|---|
| **Area ENU** | Local east-north-up coordinate system in metres, origin at the area centroid |
| **Backend (drone)** | An implementation of the `DroneBackend` interface: kinematic (L0), physics (L1), or a real drone |
| **Block** | A 250 m × 250 m survey task unit |
| **Chunk** | The same 250 m grid cell used for world building and streaming |
| **Collision assist** | Safety filter in manual mode that limits velocity toward obstacles |
| **Drone-updated** | The set of probabilities and aggregates after applying drone observations (the baseline stays untouched) |
| **L0 / L1** | Kinematic fidelity level / physics and lidar fidelity level |
| **OBB** | Oriented bounding box (rotated rectangle prism) used as a building collider |
| **Point of no return** | The distance beyond which the drone can no longer fly home with reserve |
| **RTH** | Return to home |
| **Sortie** | One flight from launch to landing, made of transit, one or more surveys, and return |
| **Truth** | The hidden sampled outcome (damage states, survivors, blocked edges) the drone discovers |
| **Verified** | Observed by a drone, as opposed to modelled |
