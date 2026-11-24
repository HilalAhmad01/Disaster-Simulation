# Drone integration: Phase 0 discovery

Written 2026-11-24. Read-only phase: no production file was changed. Everything below was read from the code or measured by
a spike script in [tools/spikes/](../../tools/spikes/). Items I could not test are marked **NOT RUN** with what is needed.

**Baseline before any change:** `python -m pytest -q` gives **199 passed, 1 skipped in 22 s** (the plan said about 170).

## 0. Setup facts

| Fact | Value |
|---|---|
| Python | 3.14.8, project venv `.venv`; `requirements.txt` is unpinned; optional deps are imported lazily (e.g. `anthropic`) |
| torch | Not installed in the project. `torch 2.15.0+cpu` installs and runs on 3.14.8 (tested in an isolated venv). The default PyPI torch is the large CUDA build, so use the CPU index for dev here |
| GPU on this machine | `nvidia-smi` cannot reach a driver. The 3050 numbers must come from the friend's laptop (spike F) |
| adb / Chrome / openssl | all installed here (adb 1.0.41, Chrome 155, OpenSSL 3.6) |
| Data on disk | areas `baseline` (32,400 buildings), `noida` (483,358), `delhi-core`; facilities cached for all three; **roads cached only for `baseline`** |
| Drone-SLAM licence | no LICENSE file in the repo. The owner (a friend of the project owner) said OK to use the code, over WhatsApp. Keep that message |
| Plan file | `drone_disaster_integration_plan.md` is untracked in git |

## 1. Host `[VERIFY]` answers (plan section 2.1)

| # | Question | Answer (file and line refer to `quake-map/disaster/`) |
|---|---|---|
| 1 | Police stations | `facilities.py` table `layer.fac`: `id, kind, name, lon, lat, source, beds, src_id, building_id`. Kind `police` is a working kind. `layer.facility_state()` ([responder_layer.py:33](../../responder_layer.py#L33)) adds `p_damaged, functionality, may_not_work` (true below 0.5). API: `GET /api/facilities` ([app.py:369](../../app.py#L369)). Counts: baseline **21** police, Noida **109**. Some have empty names. |
| 2 | Dispatch unit or team | **There is no unit object.** `UNITS` ([live.py:36](../../live.py#L36)) is a capacity per facility kind (police 2). Free units are computed from assigned SOS calls (`team_fid`) in `avail()`. ETA is the route's `expected_time_min`; the team's progress is animated **in the browser** from `assigned_at`, the server clock offset and `hub.speed` ([ui/command.js:224](../../ui/command.js#L224)). |
| 3 | Calls | A call is an SOS dict in `hub.sos` (`id, device, created, status new/assigned/resolved, lat, lon, trapped, injured, people, building, team, eta_min, route, sim, ...`). Ranking: `priority()` ([live.py:55](../../live.py#L55)) returning a score and a level (low, medium, high, critical). Created by `POST /api/live/sos` ([live.py:524](../../live.py#L524)); made-up demo calls use `sim: true`. The front end keeps them in `LIVE.sos` ([index.html:1075](../../index.html#L1075)). **Nothing is persisted:** the `live.py` docstring says everything is in memory and lost on restart. |
| 4 | Per-building data after a run | `model.last` (DataFrame, one row per building): `geometry, btype, levels, levels_src, p_URM, p_RC_LOW, p_RC_MID, use, occupants, pga, mmi, vs30, cat, risk, p_slight, p_moderate, p_extensive, p_collapse`. Also `model.x/y/lat/lon`, `model.W` (3 construction-type weights), `model.occ_by[night/day/commute]`. **Building id = the integer row index** (no string id). Heights are `levels` (storeys); 2.8 m per storey in `heights.py`. 483k buildings: every array is about 4 MB, extraction takes 1 ms. |
| 5 | Monte Carlo realisations | **Not stored.** `uncertainty.run_mc` redraws them from the hazard field (`mc_snapshot`, [simulate.py:289](../../simulate.py#L289)) with spatial correlation. The hidden truth can call the same per-realisation draw once with a seed. |
| 6 | Aggregation recompute | The cell groupby, priority ranking, heat grid and summary are written **inline inside `run_hazard`** ([simulate.py:170](../../simulate.py#L170)); there is no function to call with changed probabilities. They depend only on `p` (n by 4), `occ` and positions, so extraction is easy but is a required refactor (Phase 7). |
| 7 | Road graph | `roads.RoadNet`: arrays `u, v, t, len, geom, width, road_id`; edges are integer positions. `blockage_now(layer)` ([live.py:121](../../live.py#L121)) returns P(blocked) per edge, cached per run id. Override plan: patch that array and bump the cache. The routing code's exact use of it is still to be read in Phase 7. |
| 8 | `index.html` structure | One classic `<script>` block (about lines 299 to 1296), no ES modules. Globals: `map`, `LIVE`, `lastRun`. `ui/command.js` is a second IIFE loaded last that reads those globals. MapLibre 4.7.1 comes from a CDN (jsdelivr, unpkg fallback). Buildings are a viewport GeoJSON source `bld` plus `far`, and optional vector tiles `bldv` (no `promoteId`). |
| 9 | Static serving | Only `/`, `/sos`, tiles and **`/ui/{name}` with a hard whitelist** (`command.css`, `command.js`) ([app.py:247](../../app.py#L247)). No `StaticFiles` mount. The drone front end needs its own route. |
| 10 | Simulation time | **Wall clock** plus `hub.speed`. Speeds are 1, 5, 10, 30, 60; the default is **10** ([live.py:397](../../live.py#L397), `SPEEDS` at line 40). There is no sim clock. |
| 11 | Proxy and websockets | `tools/https_proxy.py` is a raw TCP relay with TLS termination, so websocket upgrades pass through. **Measured** (spike E): over https through the proxy, websocket round trip p95 1.3 ms, 0% loss, on loopback. |
| 12 | Python and optional deps | See section 0. |
| 13 | Areas | `areas.Registry`; `POST /api/areas/{aid}/activate` ([app.py:326](../../app.py#L326)) swaps the model and sets `layer=None`. No event bus; the page reloads through `areaSel.onchange` ([index.html:870](../../index.html#L870)). The drone module needs a hook in `activate`. |
| 14 | Test fixtures | `tests/conftest.py`, `tests/synthetic.py::synthetic_exposure(n, seed, centre, span)`, `test_live.py`, `test_responder_layer.py`. A tiny synthetic city is easy to build from these. |

## 2. Drone-SLAM `[VERIFY]` answers (plan section 2.2)

Clone read: commit 8f6de7a (2 commits). Code is small: 1,502 lines of Python in `droneforest/`.

| # | Question | Answer |
|---|---|---|
| 1 | Dynamics | `dynamics.py`: acceleration-based quadrotor with **no mass and no battery**. Thrust acceleration `g(1+a0)` clamped to [0, 2g], body rates up to (6, 6, 3) rad/s, drag 0.05 to 0.30, thrust lag 0.03 to 0.07 s, rate lag 0.02 to 0.05 s. `dt_phys` 0.01 s, two physics steps per control step, so control runs at **50 Hz**. Re-parameterising to 4 kg is unnecessary; our `energy.py` owns the battery. |
| 2 | Flyer interface | `heuristic_action(env, vmax, steer_gain, slow)` returns an (E, 4) action. It reads privileged env state: `env.goal`, `env.q`, `env.tree_t` (ray distances to trees) and `env.forest.ground`. Avoidance is a potential field on `tree_t`. To reuse it, we feed an env-like object whose `tree_t` is filled from our own raycast. |
| 3 | Look-ahead waypoint | `mission.py` sets `env.goal = path[idx+9]` each step (path points 1 m apart). An external controller can drive it by setting `env.goal`. **Measured** (spike C): straight 400 m legs track within **1.15 m** cross-track at all speeds, in wind. |
| 4 | World and boxes | `world.py` holds tensors per env. **The committed `world.py` has trees only: no boxes, no slab raycast.** `mission.py` and `rollout.py` use `Forest(..., max_boxes=...)`, `f.bc`, `f.bh`, `f.bkind` and `f.B`, which do not exist, so `mission.py` cannot run as pushed. The friend must push the missing `world.py` (and check `env.py`). It does not block us, because we write our own oriented-box raycast (spike B). |
| 5 | Survivor detection | In the viewer, after the fact: `computeDetections(ep)` (`droneforest/viewer/main.js:576`). POIs (`survivor`, `hiker`) are generated by `mission.py:build_world`. Not server-side. |
| 6 | Voxel SLAM map | Built in the **viewer's JavaScript** (`addVoxel`, 0.4 m voxels, a hit-count threshold, chunked surface-net mesh, scorecard). Scan state for buildings must be computed on our server anyway. |
| 7 | Torch, GPU, CPU | CPU build runs on 3.14.8. One drone: control step **2.5 ms** on one thread, about **8x real time** (spike F script, this machine). 32 envs: 8 ms. GPU and GPU memory: **NOT RUN** (needs the 3050 laptop). |
| 8 | Terrain | `terrain.py` is an analytic heightfield. Zero coefficients give flat ground: **measured** ground z = 0.00. |

Other things found:

- **Scale mismatch.** The sim is forest-sized: arena +-22 m by +-14 m, a **12 m forward fan** of 80 rays (az +-70 deg, el +-35 deg), speeds of 4 to 5 m/s by default. The plan assumed a 60 m, 360 degree lidar and 15 m/s cruise.
- **Cruise speed.** With `vmax=15` the flyer averages **11 m/s** (mean 4.6, 6.5, 9.5, 11.0 for vmax 5, 8, 12, 15). It never reaches 15.
- **Heuristic in its own forest.** At density 0.5, success is **17 to 20%** (1 and 32 envs), matching the README's "modest". City avoidance must not rely on it.
- **INS drift** is a random walk of 0.12 per root second per axis: about 1.6 m (3-D) after 60 s and 5 m after 10 minutes.

## 3. Spike results

| Spike | Result | Script |
|---|---|---|
| **A: data** | Noida: load 0.6 s, Model 1.8 s, one run **1.1 s** (baseline 0.18 s). Arrays are tiny. 250 m chunks: median 35 buildings (baseline 50), p95 about 165, **max 556**. Rotated-rectangle build for 556 buildings: 1.4 ms. Rectangle area matches the footprint (median ratio 1.00); only 2.3% are over 1.4 (L shapes). | [spike_a_data.py](../../tools/spikes/spike_a_data.py) |
| **B: raycast** | Numpy oriented-box slab raycast on the densest real chunk (1,790 boxes in the active set): agrees with shapely to **0.00 cm** on 817 random rays. One 80-ray scan: **2.3 ms** without a cull, **0.3 to 0.4 ms** with a distance cull (budget was 10 ms). Box heights: median 3.0 m, p95 9 m, max 18 m. | [spike_b_raycast.py](../../tools/spikes/spike_b_raycast.py) |
| **C: flyer** | See section 2 items 1 to 3, 7, 8 and the notes. | [spike_c_flyer.py](../../tools/spikes/spike_c_flyer.py) |
| **D: overlay on the 3D map** | **NOT RUN.** Script is ready; paste it into the real dashboard (3D on) on the demo laptop. Headless Chrome cannot measure a real GPU. | [spike_d_overlay.js](../../tools/spikes/spike_d_overlay.js) |
| **E: tablet** | The page and server work in headless Chrome over `http://localhost` (the USB route) and over https through the project proxy (the hotspot route): secure context true, WebGL2, wake lock, fullscreen, websocket round trip about 1 ms. **Not measured on the Android tablet:** multi-touch, fullscreen and wake lock after a certificate bypass, vibrate, frame rate, real Wi-Fi round trip. | [spike_e_server.py](../../tools/spikes/spike_e_server.py), [spike_tablet/index.html](../../tools/spikes/spike_tablet/index.html) |
| **F: laptop** | Script runs (tested here on CPU). **NOT RUN on the 3050 laptop.** | [spike_f_laptop.py](../../tools/spikes/spike_f_laptop.py) |

## 4. Still unknown (needs a person or hardware)

1. Spike E on the actual Android tablet, with real Chrome, both connection routes (`adb reverse`, and hotspot plus proxy).
2. Spike F and the `--device cuda` run of spike C on the friend's 3050 laptop, including free GPU memory with the dashboard open.
3. Spike D with the real 3D map in a real browser.
4. Whether the dashboard works with no internet (MapLibre comes from a CDN; whether the basemap is also online was not checked).
5. The friend pushing the missing box-obstacle code in `world.py`, and the licence message saved.
6. Which area the demo uses (see PLAN_DELTAS item 6).
