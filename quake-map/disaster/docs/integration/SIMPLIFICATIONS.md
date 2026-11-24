# Deliberate simplifications

Only flight time is meant to be realistic. Everything below is simplified on purpose; the dashboard says so where a viewer could be misled.

## What the "discoveries" are
- **The hidden outcome is drawn from the model's own probabilities** (`drone/truth.py`). In simulation this shows the workflow (find, confirm, dispatch, update), not that the damage model is right. The drone page's Model tab says so.
- Spatial correlation is a shared normal draw per 500 m cell (`truth_cell_corr`), not the Monte Carlo correlation field.
- Survivors are grouped per building and kind; only a small share is visible from the air (`p_exposed_given_trapped`, `p_escape`). Outdoor people are only those who left a collapsed building.
- Detection is a Bernoulli draw per second inside the camera footprint with no line-of-sight test (`drone/scan.py`); the chance falls with altitude. False alarms are a Poisson process over the area covered.
- Observed damage is the hidden state through a confusion matrix (`classify_building`), the one place a real point-cloud classifier would replace.

## The world
- Flat ground at z = 0; flat roofs; one rotated rectangle per building (97.7% of Noida footprints are within 40% of their rectangle).
- Rubble is random slabs and blocks inside and around the footprint, seeded per building. Collapse is not simulated.
- Street debris is a few boxes across the carriageway of streets the hidden outcome blocked.
- Building height is storeys times 2.8 m (default 6 m); the lidar is analytic ray-box intersection in numpy, 60 m and 360 degrees (the vendored Drone-SLAM sensor is a 12 m forward fan, unused here).

## Flight
- Level 0 flies straight legs at constant speed with the planner's own energy formulas, so plan and simulation agree exactly. Wind is a constant in the energy model, not a flow field.
- Level 1 (Drone-SLAM physics) tracks a look-ahead waypoint with the vendored heuristic flyer, with gusts. It does not avoid obstacles: the city is flown above roof level.
- Collision assist in manual flight looks one second ahead against nearby boxes; with assist off a contact crashes the drone.
- The believed position (drift) is a random walk (0.12 m per root second) used for the map look; it is not fed back into navigation.
- Battery: the reserve and the return estimate are realistic in form; the numbers are an assumed airframe, not a product.
- Drones are created from police stations only; a station in a likely-damaged building shows an offline drone with the reason.

## Dispatch and the model
- A confirmed sighting becomes an ordinary SOS call (source "drone"); the existing team suggestions and routes handle it. Teams already driving are re-routed around a street a drone found blocked, only when the new route is slower.
- Drone-updated casualties scale the modelled deaths by the change in people expected inside collapsed buildings; they are not a full re-run of the casualty model.
- The 200-run uncertainty ranges are not recomputed live.
- Observed streets override the blockage probability of the run they were seen in (0.999 blocked, 0 clear).

## Access and network
- The dashboard API has no authentication, like the rest of the app. Pairing a tablet gates only the pilot panel, on a local network. Behind the https proxy every client looks like 127.0.0.1, so the wrong-code limit is shared.
- The tablet page needs a secure context for full screen and wake lock; `localhost` through `adb reverse` provides it, the self-signed https proxy usually does.
- Input from a tablet is velocity at 20 Hz; a frame older than the last one is dropped; no input for 0.5 s hovers; link lost for 5 s returns home.

## Showcase
- Replays re-send recorded simulated messages and are labelled as such.
- Nothing is persisted across a server restart except saved replays (the dispatch hub keeps everything in memory too).
