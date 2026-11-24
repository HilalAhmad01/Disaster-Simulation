# Performance

Measured on this development machine (CPU only, Python 3.14), 2026-11-24. Scripts: `tools/drone_bench.py`, `tools/spikes/`.

## Server

| Item | Result | Budget |
|---|---|---|
| Tick, 5 drones on auto at 60x on the baseline area (32,400 buildings), 30 simulated minutes | median 4.3 ms, p95 20 ms, p99 38 ms, max 74 ms | 100 ms |
| Messages to a browser, same run | about 60 KB/s, 180 messages/s | 100 KB/s |
| Memory growth over that run | 28 MB | flat |
| Start the drone module (bind) on baseline | 0.25 s | 2 s |
| Start on Noida (483,358 buildings) without streets | 0.5 s | 2 s |
| Queue rebuild | 8 to 9 ms | 200 ms |
| Drone-updated aggregates, Noida | 180 ms | 500 ms |
| Probabilities after new observations, Noida | 35 ms | 100 ms |
| One chunk of world geometry | 2 ms | 300 ms |
| 80-ray lidar scan against a 1,790-box active set | 0.3 to 0.5 ms with a distance cull | 10 ms |

What fixed the first run's spikes (max 128 ms): per-task reachability is cached, queue rebuilds are limited to about once a second, the SOS-bump check reads the cached queue, and chunk geometry uses a cheap seeded generator instead of a numpy generator per building.

## Drone-SLAM physics (level 1)

| Item | Result |
|---|---|
| Control step of the vendored quadrotor and flyer, one CPU thread | 2.5 ms (8x real time); with 25 steps per call and the pipe, 19.5 ms per call (25x real time for one drone) |
| Planned vs simulated energy, one block survey | planned 57.7 Wh, level 0 57.7 Wh, level 1 54.9 Wh (5% under) |
| Demo clock while a physics drone flies | capped at 4x (`DRONE_L1_CLOCK_CAP`) so five drones keep up |
| GPU | not needed for one drone; the worker uses the CPU. Run `tools/spikes/spike_f_laptop.py` on the demo laptop to confirm |

## Browser
Not measurable here (headless Chrome uses a software renderer). `tools/spikes/spike_e_server.py` and `spike_d_overlay.js` measure frame rates on the tablet and on the dashboard's 3D map; the plan's target is 20 fps in the live view while the 3D map stays at 30 fps.
The live view draws at pixel ratio 1, no shadows, instanced meshes, 24 fps cap, and stops when hidden. The tactical map is a 2D canvas redrawn only when something changed.

## Tests
`python -m pytest -q`: the existing 199 plus the drone suite (about 130 tests, of which 8 need torch or a browser). The randomised stress test runs 4 seeds by default; 30 seeds of 4,000 ticks each also pass.
