# Demo script

Everything shown is simulated; the drone page and the pilot panel say so. About eight minutes at the default demo speed.

## Before you start (once)
1. `cd quake-map/disaster && uvicorn app:app --port 8000`. Optional: `DRONE_WORKER_PYTHON=/path/to/python-with-torch` for Drone-SLAM physics.
2. Open `http://localhost:8000`. Use the saved area `baseline` (streets and facilities are cached, so it works without extra downloads).
3. Run an earthquake (the quick scenario or your own), then open Responders and press Show hospitals, fire stations and roads.
4. The maps, fonts and street tiles come from the internet (MapLibre, Google Fonts, OpenStreetMap). Check the venue has it, or open the page once beforehand so the browser has them cached.
5. Tablet, USB route (most reliable): enable USB debugging, plug it in, run `adb reverse tcp:8000 tcp:8000`, open `http://localhost:8000/pilot` in Chrome. Hotspot route: start `tools/https_proxy.py`, join the laptop's hotspot, open the https address shown in the Tablet tab, accept the certificate warning once.
6. Run the three spikes that need hardware if you have not yet: `tools/spikes/spike_e_server.py` on the tablet, `spike_f_laptop.py` on the demo laptop, `spike_d_overlay.js` in the dashboard.

## The story

**1. The picture (dashboard).** The damage heat map, the numbered priority neighbourhoods, responders and routes. "The model says these blocks are worst. A drone can check."

**2. Open the drone console.** Press Drone in the top bar (or take the Guided tour, which has a Send in a drone step). Fleet on the left: one drone per well-placed police station, battery, spare batteries; one station is greyed out because its building is likely damaged. Dashed rings: how far each can fly and still return.

**3. Launch.** Press Launch on a drone. Pre-flight checks, take-off, transit at about 40 m. The live view shows chase, front, top and map views; the map shows the heading arrow, trail and camera footprint. The battery bar has a mark at the return reserve.

**4. Why there.** Queue tab: ranked blocks, value per watt-hour; Why? shows what drives a rank. Pin, skip or reorder and watch the next sortie change. Toggle Auto.

**5. Look at a building.** Zoom in, press the target tool, click a building: the preview shows minutes, watt-hours and battery left. Send drone.

**6. Take the controls (keyboard).** Take control, click the live view: W A S D, Q and E, arrows, Space holds, R returns, Esc releases. The demo clock drops to 2x and returns afterwards.

**7. Tablet.** Tablet tab, Pair a tablet: a 6-digit code. Enter it on the tablet. Take control on the tablet; the left stick moves, the right stick climbs and turns. Release control and choose Resume, Hold or Return home. Stop touching the sticks: it hovers after half a second. A second tablet cannot fly the same drone; the commander can take it back.

**7b. Phone camera view (optional, for show).** On an Android phone open `http://localhost:8000/fpv` (after `adb reverse tcp:8000 tcp:8000`; `localhost` is a secure page, which the camera needs). Type the pairing code, or open `/fpv?code=123456` from a link or QR code. The phone's own camera fills the screen with a minimal HUD (battery, height). Tap Take off; when the simulated drone is up the phone takes control by itself. Left stick moves, right stick climbs and turns, hold the button to send it home. The camera picture is real; the flight is simulated and the screen says so. The same flight shows on the laptop's Drone page.

**7c. Kindle (optional, for show).** A jailbroken Kindle runs the separate `kindle-dash` project (`~/kindle-dash`, branch `quake-pages`). Start this dashboard on port 8000 and `python dash.py serve` in `~/kindle-dash/server`; the Kindle then shows the latest simulation run (people at risk, collapses, worst-hit areas, responders, drones) and a list of SOS calls. A new SOS call puts a black NEW SOS banner on the Kindle within about 10 seconds, and running a new simulation switches it back to the numbers page. The Kindle reads `GET /api/kindle/feed` (`kindle_feed.py`), which leaves out phone ids and exact positions. The Kindle's `kindledash.conf` holds this computer's IP; run `scripts/install-to-kindle.sh` again if it changed.

**8. Sightings.** Sightings tab: unverified sightings with a confidence. Confirm and dispatch creates an ordinary SOS call; open Dispatch on the dashboard and send the nearest team. A phone SOS near a sighting is marked drone-corroborated; on a phone call, Verify with a drone flies there and looks.

**9. Update the picture.** Model tab: switch the map between Modelled and Drone-updated, read the totals side by side. The note says the findings come from a simulated outcome.

**10. Drone-SLAM physics (optional).** With `DRONE_WORKER_PYTHON` set, drones fly with the vendored Drone-SLAM dynamics (banking, gusts); the Map view shows the believed track drifting from the true one.

## If something fails
- More menu, Replay the last run, or a saved run: re-plays recorded simulated messages, labelled as a replay. Save a run with Save this run after a good take.
- `curl localhost:8000/api/drone/health` shows whether the service is bound and how long a tick takes.
- Re-running the earthquake recalls the drones and rebuilds everything; open the drone page again if it shows "no simulation".
