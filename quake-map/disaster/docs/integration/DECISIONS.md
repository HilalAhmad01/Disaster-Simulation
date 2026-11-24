# Decisions

Taken during the build, in order of importance. Earlier decisions D1 to D20 are the defaults of the integration plan; D21 onward were made in conversation or by the evidence in DISCOVERY.md.

| # | Decision | Why |
|---|---|---|
| D21 | There is no real drone. Everything is simulated and labelled so (a "Simulated" tag on the drone page and the pilot panel, replays marked as replays). No real-drone adapter, no versioned wire schema | The goal is a showcase of how a drone would look inside the dashboard |
| D22 | Level 0 (the simple drone) runs inside the app. Only Drone-SLAM physics (level 1) runs in a separate process | Fewer parts to break on demo day; torch is optional |
| D23 | The tablet pilot panel is its own page, `/pilot`, for Android Chrome. The commander's console is its own page, `/drone`; the dashboard keeps only a button, a sightings badge and two contextual actions | Requested; keeps the dashboard uncluttered |
| D24 | Tablet connection: USB with `adb reverse` first, laptop hotspot plus the existing https proxy second | `localhost` is a secure context in Chrome: no certificate warning and no Wi-Fi jitter |
| D25 | Drone-SLAM mode (L1) uses two small DroneForest files vendored into `drone/droneforest/` with attribution | The owner agreed by message; the repo has no LICENSE file yet |
| D26 | No curated demo seed. `tools/pick_demo_seed.py` showed every seed has findable people in the first blocks | Honest: a hand-picked seed would be a hidden thumb on the scale |
| D27 | The drone follows the existing demo clock (`hub.speed`); there is no second clock. Manual flight slows it to 2x or less, L1 flight to 4x or less, restored afterwards | One clock, one product |
| D28 | Drones are created for at most 5 police stations (best placed for the risky neighbourhoods, spread at least 800 m apart) plus one example of an unavailable station | 109 stations in Noida; the rest add noise |
| D29 | Hidden outcome: one draw from the model's probabilities with a shared normal draw per 500 m cell for spatial correlation; people visible from the air are rare (0.2% of trapped people) | Most trapped people are inside rubble; keeps a sortie to a handful of sightings a person can confirm |
| D30 | Battery 200 Wh, cruise 10 m/s, reserve 25% of a full battery, launch check 6 s (it was 60 s, which felt like the button did nothing), battery swap 300 s, 3 spare batteries per station | Endurance of 25 to 35 minutes, as the plan required; the Drone-SLAM flyer averages about 11 m/s, so cruise is 10 |
| D31 | Queue ordering is by value per watt-hour; operator edits (pin, skip, reorder, banned zones) bypass the one-second queue throttle | Edits must feel immediate |
| D32 | The drone-updated damage picture applies observations with a confusion matrix (accuracy 0.9) to scanned buildings and a capped odds factor to unscanned neighbours; the modelled picture is never modified | Both views shown side by side builds trust |
| D33 | Pilot pairing uses a 6-digit single-use code valid for 2 minutes; pairing and the dashboard API are unauthenticated beyond that, like the rest of the app | Local network showcase; see SIMPLIFICATIONS |
| D34 | The floating corner window of the plan is replaced by a fixed live-view pane on the drone page, and the tablet shows the same view full screen | A dedicated page has the room; one less thing to drag around |
| D35 | Design of the new pages follows the dashboard's own language (graphite neutrals, ink primary buttons, hairline borders, 4 px grid) and the guidance of the `unslop` and `impeccable` repositories: sentence case, no uppercase tracked labels, one SVG icon set, no nested cards, one easing curve, only colour, background, border and transform animate | Requested: in sync with the rest of the app |

## Configuration

Every setting is an environment variable `DRONE_<NAME>` (see `drone/config.py`). The ones you are likely to touch:

| Variable | Default | Meaning |
|---|---|---|
| `DRONE_ENABLED` | `1` | `0` removes every drone route and button; the app behaves exactly as before |
| `DRONE_AUTO_MODE` | `1` | Drones choose their own sorties from the queue when the console is open |
| `DRONE_MAX_STATIONS` | `5` | Drones created |
| `DRONE_BATTERY_WH` | `200` | Full battery |
| `DRONE_LAUNCH_DELAY_S` | `60` | Pre-flight checks, in simulated seconds |
| `DRONE_WORKER_PYTHON` | empty | A Python that has torch: turns on Drone-SLAM physics (L1) |
| `DRONE_SEED` | `1` | Seed of the hidden outcome |
| `DRONE_FALSE_ALARMS_PER_KM2` | `1.5` | False sightings per square kilometre surveyed |
