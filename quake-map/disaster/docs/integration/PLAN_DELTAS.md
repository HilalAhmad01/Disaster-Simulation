# Plan deltas: where reality differs from the integration plan

Written 2026-11-24 after Phase 0. Each row says what the plan assumed, what is true, and what changes. Evidence is in
[DISCOVERY.md](DISCOVERY.md). The plan has since also been revised in conversation (simulated showcase, no real drone,
Android tablet pilot panel); those decisions are in the last section.

## A. Corrections from the code

| # | Plan said | Reality | Change |
|---|---|---|---|
| 1 | Add `kind="drone"` to the unit model | No unit objects exist; `UNITS` is a capacity table | Drones get their own list and row type in Dispatch; ground-team suggestions already never see them |
| 2 | New sim clock at 1, 2, 4x, Dispatch adapted to it | Dispatch is wall-clock plus `hub.speed` (1, 5, 10, 30, 60; default 10) and teams animate in the browser | **No second clock.** The drone follows `hub.speed`. L0 serves 10x and above; L1 runs at 1 to 8x. Taking manual control asks the dashboard to drop to 2x or less |
| 3 | Serve `web/drone/` as static files | `/ui/{name}` is a two-file whitelist | Add a separate safe route (`/drone-static/...`) and a `/pilot` page route, as `/sos` does |
| 4 | Pick one of the 200 stored Monte Carlo realisations | None are stored | Draw one correlated realisation with the same code as `run_mc`, seeded and cached |
| 5 | Re-run aggregation with changed probabilities | Aggregation is inline in `run_hazard` | Extract it into a function (Phase 7), with an identical-output test |
| 6 | Demo on Noida | Noida roads are not cached (needs network); `baseline` has everything and runs in 0.18 s | **Demo area: `baseline`** (32,400 buildings, 21 police stations, roads cached, offline). Cache Noida roads before any Noida demo. The plan's reachability story needs thought, see B1 |
| 7 | One drone per police station | 21 (baseline) or 109 (Noida) stations | Cap the fleet: the 4 to 6 working stations best placed for the highest-risk neighbourhoods. Others show as "no drone assigned". Config `DRONE_MAX_STATIONS` |
| 8 | Building id is a string | Id is the integer row index | Contract: `building_id` = row index, valid for one (area, exposure version). Saved scan state stores the version |
| 9 | Chunks hold hundreds to thousands of buildings; torch raycast and a spatial index needed | Median 35 to 50 per 250 m chunk, p95 about 165, max 556; numpy scan with a cull takes 0.3 to 0.5 ms | Numpy only for the lidar look. No torch raycast, no grid index. The 10 ms budget is met 20 times over |
| 10 | Split L-shaped footprints into several boxes (stretch) | 97.7% of footprints are within 40% of their rectangle | Drop the multi-box split. One rotated box per building |
| 11 | Survey height up to 120 m | Heights: median 3 m, p95 9 m, max 18 m | Survey at tallest building plus 20 m, roughly 25 to 40 m; max altitude 120 m rarely matters |
| 12 | Open SOS calls survive a restart | `live.py` says everything is in memory and lost on restart | Drone state follows the same rule: in memory, plus the event log for replay. No persistence promised |
| 13 | A tiny synthetic city fixture can be reused | `tests/synthetic.py` exists | Reuse it; add stations and a small road net on top |

## B. Corrections from Drone-SLAM

| # | Plan said | Reality | Change |
|---|---|---|---|
| 1 | Reach rings will shape the story | Battery 200 Wh at 15 m/s gives about 8 km out-and-back range; the baseline area is 5.5 km across, so every station reaches everything | Keep reach rings for the look. For a range-limited story, use a smaller showcase battery (about 100 Wh) or a larger area. **Owner decision** |
| 2 | Lidar 60 m, 360 degrees, 80 rays | Drone-SLAM is forest-scale: 12 m, a forward fan of 80 rays | Our numpy lidar (L0+) uses our own 60 m, 360 degree scan for the map look. L1 keeps the 12 m fan for avoidance only |
| 3 | Cruise 15 m/s | The flyer averages 11 m/s with a 15 m/s limit | L1 cruise speed 10 m/s. L0 may use whatever the energy table says; the planner reads the airframe table, not the flyer |
| 4 | Heuristic flyer handles obstacle avoidance | 17 to 20% success in its own forest; it only reads tree distances | Fly above roofs. Our own simple safety filter handles manual flight. The flyer only tracks waypoints |
| 5 | "Disaster zone with rubble" boxes can be reused | The pushed `world.py` has no boxes; `mission.py` and `rollout.py` cannot run as committed | Do not depend on it. Ask the friend to push the missing code. Our rubble generator is written from scratch |
| 6 | L1 needs the GPU, 4x cap | One drone runs about 8x real time on one CPU thread (2.5 ms per step) | **The 3050 is not required for the physics.** Phase 8 no longer depends on GPU hardware; the GPU matters only for rendering. Spike F on the 3050 laptop is still needed for its CPU speed, and to rule out torch install problems |
| 7 | Voxel SLAM map could be computed server-side | It is built in the viewer's JavaScript | Reuse its logic client-side for the tablet's SLAM view; scan state for buildings stays server-side |
| 8 | Licence unknown | Still no LICENSE file; the owner agreed over WhatsApp | Keep the message. Ask the friend to add a LICENSE file. Only Phase 8 copies code |

## C. Decisions now taken (log of conversation)

| # | Decision |
|---|---|
| D21 | **No real drone.** Everything is simulated and labelled SIMULATED DRONE in the UI. No real-drone adapter, no versioned JSON Schema export |
| D22 | L0 runs in-process inside the app. Only L1 (torch) runs as a separate worker process |
| D23 | Pilot panel is a separate page `/pilot`, for a mid-range Android tablet in Chrome. The dashboard corner window is a read-only mirror |
| D24 | Tablet connection: USB with `adb reverse` first; laptop hotspot plus the existing https proxy second. The demo tablet may be tethered |
| D25 | Drone-SLAM mode (L1) is in the demo scope, built after Phases 1 to 7 and gated by a benchmark. The friend's WhatsApp OK covers using their code |
| D26 | Showcase additions: demo seed, drone chapter in the guided tour, replay of a recorded run as a fallback |

## D. Where the plan ended up (2026-11-24)

| Phase | Result |
|---|---|
| 0 Discovery | Done; spikes E and F and the dashboard overlay spike D need your tablet, the 3050 laptop and the real 3D map |
| 1 Foundation | Done: config, georeferencing, energy, clock, state, stations, pairing and lease, in-process simulator, drones in the console |
| 2 Presence | Done, redesigned: a separate drone page (`/drone`) and the tablet pilot page (`/pilot`) replace the plan's corner window |
| 3 Truth and world | Done: hidden outcome, rubble and debris geometry, numpy lidar and the live 3D view |
| 4 Control | Done: keyboard and touch control, lease, dead-man, link lost, forced return, collision assist, click-to-fly, send a drone to a building |
| 5 Priority and auto | Done: value per watt-hour, energy-feasible sorties, editable queue, SOS bump, battery swaps |
| 6 Scan and dispatch | Done: scan state, sightings, merging, corroboration, confirm or reject, verify with a drone |
| 7 Feedback | Done: drone-updated picture next to the modelled one, blocked streets and re-routing |
| 8 Drone-SLAM mode | Done and tested with torch (physics worker, fallback to the simple model) |
| 9 Showcase | Done: tour chapter, replay, exports, documents. Not done: the 2-hour soak test (a 4,000-tick randomised stress test ran instead) |

Differences from the plan not listed above: the corner window became a fixed pane; the protocol schema export and the versioned wire format were dropped (no real drone); aggregates are recomputed from one extracted method and verified equal to the baseline.
