# Drone controller guide

How to run the simulated drones, fly one with a phone (live camera as the picture), watch it on the laptop, and show SOS calls on a Kindle.
Everything about the drones is **simulated**: there is no real drone. The phone's camera picture is real, the flight is not, and the screens say "Simulated".

## 1. What is where

| Page | Open it on | What it is |
|---|---|---|
| `/` | laptop | The disaster dashboard. Run a simulation here first. |
| `/drone` | laptop | Drone console: fleet, queue, sightings, map and a live view of the selected drone. |
| `/fpv` | Android phone (Chrome) | **The controller.** The phone camera fills the screen, two sticks and one button fly a drone. |
| `/pilot` | Android tablet | A fuller cockpit (3D view, battery, tactical map). Same pairing and controls idea. |
| `/sos` | phone | Send an SOS call. It shows in the dispatch panel and on the Kindle. |

There are two drones:
- **Camera drone:** yours. It is never sent anywhere on its own. Take off, and the phone flies it.
- **Auto drone:** flies surveys by itself. You can watch it, or take it over.

## 2. Start everything (every time)

### The one command

```bash
cd ~/"manthan kriti project"/quake-map/disaster
./start_demo.sh --area baseline
```

It does everything in sections 2 to 6 for you, and prints the links to type on the phone and the Kindle:
1. starts the dashboard (port 8000),
2. runs an earthquake, loads hospitals, fire stations and streets, and starts both drones,
3. links a phone on a USB cable (`adb reverse`),
4. starts the HTTPS proxy for phones on Wi-Fi (port 8443),
5. starts the Kindle server (port 8080) and, **if the Kindle is plugged in**, installs this laptop's address on it, sets the fast refresh and safely unmounts it,
6. gives you a few keys: **p** new pairing code, **s** send 3 test SOS calls, **c** clear them, **r** new earthquake, **d** start the drones, **q** quit.

Good to know:
- **`--area baseline`** is the fast, safe choice. An area that was never prepared before needs its road network downloaded (a few minutes, needs internet). A fresh start opens on whichever area you used last, so name one you know is cached. The script lists the others.
- Anything already running and healthy is **reused**. A proxy that was frozen with Ctrl+Z is **replaced**. `--restart` starts everything fresh.
- Other options: `--mag 7.1 --depth 12 --time-of-day night --speed 10`, `--no-kindle`, `--no-kindle-install`, `--no-proxy`, `--no-browser`, `--no-sim`. `./start_demo.sh --help` lists them.
- **Ctrl+C or q** stops what the script started. It leaves alone anything that was already running. It ignores Ctrl+Z on purpose. It never uses sudo: if the firewall needs a rule it prints the command.
- Logs are in `data/demo-logs/` (one file per program).
- It still cannot do three things for you: turn on the phone's USB debugging and tap Allow, put the Kindle and phone on the same Wi-Fi, and run the one-time `sudo ufw allow …` lines.

### Or by hand (what the script does)


The scenario lives in memory, so **a restart clears it**. Do these in order.

1. **Dashboard.** In a terminal:
   ```bash
   cd ~/"manthan kriti project"/quake-map/disaster
   source ../../.venv/bin/activate
   uvicorn app:app --port 8000
   ```
   Always run commands from `quake-map/disaster`. Running them from the project root gives "No such file".
2. Open `http://localhost:8000`, press **Run simulation**.
3. Under **Responders**, press **Show hospitals, fire stations…** (needs internet the first time). Without it, `/drone` says streets and facilities are not loaded.
4. Open `http://localhost:8000/drone`. Opening it starts the drone service. Both drones appear in the Fleet list.

## 3. Connect the phone controller

You need an Android phone with Chrome. The camera only works on a secure page, so use one of these:

**A. USB cable (most reliable)**
1. Phone: Settings → About phone → tap Build number 7 times. Then Settings → Developer options → turn on **USB debugging**.
2. Plug the phone in. Tap **Allow** on the "Allow USB debugging?" prompt.
3. Laptop: `adb devices` must list the phone as `device`. (Empty list: try another cable, one that carries data. Set USB mode to File transfer.)
4. Laptop: `adb reverse tcp:8000 tcp:8000`
5. Phone Chrome: open **`http://localhost:8000/fpv`**. Type the **whole address, including `:8000`**. Chrome likes to autocomplete to another port.

**B. Wi-Fi (no cable)**
1. Phone and laptop on the **same Wi-Fi network**.
2. Laptop, in `quake-map/disaster`: `python tools/https_proxy.py` and leave it running. **Never press Ctrl+Z in that terminal** (it freezes the proxy; `fg` resumes it). Stop it with Ctrl+C.
3. First time only, allow the port: `sudo ufw allow from 192.168.0.0/24 to any port 8443 proto tcp`
4. Phone: open `https://<laptop IP>:8443/fpv` (the proxy prints the link). Chrome warns about the certificate once: Advanced → Proceed.

**Pair it.** On the laptop's `/drone` page press **Pair a phone** (top right). A 6-digit code and a QR code appear. On the phone type the code (or scan the QR, which opens `/fpv` with the code filled in). Allow the camera when Chrome asks.

## 4. Fly

The phone screen: your camera picture, a small **Camera | Auto** switch at the top, a red **Live** dot, battery and height, a **Simulated** tag, a flip-camera button, two round sticks and one button in the middle.

| Do this | What happens |
|---|---|
| Tap **Take off** | The drone starts up (about 6 seconds), climbs to 40 m and hovers. The phone then takes control by itself. |
| **Left stick** | Move: forward, back, sideways. |
| **Right stick** | Up and down, and turn. |
| Let go of the sticks | It hovers after half a second. |
| **Hold to return** (hold for 1 second) | Sends the drone home to land. Holding stops accidents. |
| Tap **Auto** (top) | Switch to the auto drone. If it is on the ground, **Take off** sends it on a survey. If it is flying, the button says **Take control**: tap it to fly it yourself. |
| Tap **Camera** again | Hands the auto drone back to its autopilot. The camera drone hovers where you left it. |

The picture leans and shifts a little as you move the sticks, so the flight feels real. Turn the phone sideways for the best view (the page tries to lock landscape; rotate by hand if it does not).

## 5. Watch it on the laptop

On `/drone`:
- **Fleet (left):** one card per drone with its state, battery and buttons.

  | Button | Does |
  |---|---|
  | Take off (camera drone) / Start survey (auto drone) | Launches it. |
  | Take control | You fly it (see keys below). |
  | Release / Back to autopilot | Stops flying it. The camera drone hovers; the auto drone resumes its route. |
  | Fly home | Returns to the station and lands. |
- **Live pane (bottom):** buttons **Camera, Chase, Front, Top**. **Camera** shows the phone's picture and switches on by itself when a phone connects. If the phone goes away it falls back to the 3D view.
- **Keys** (click the live view first, after Take control): **W A S D** move, **Q / E** down and up, **arrows** turn, **Space** hold, **R** return home, **Esc** release.
- **Top bar:** **Speed** ×1 to ×60 (how fast simulated time runs), **Pause**, **Autopilot** (on: the auto drone chooses its own surveys; off: nothing flies until you press a button), **Pair a phone**.
- **Right panel:** Queue (what to survey next), Sightings (people drones spotted: confirm or reject), Model, Pair.

One pilot flies a drone at a time. The laptop can always take control back.

## 6. SOS calls and the Kindle

**Send an SOS from a phone:** open `https://<laptop IP>:8443/sos` (needs the HTTPS proxy from section 3B, so the phone can share its location) and tap the SOS button. Or fake some: `curl -X POST localhost:8000/api/live/simulate -H 'content-type: application/json' -d '{"n":3}'`. Clear them with `curl -X POST localhost:8000/api/live/clear`.

**Kindle dashboard** (separate project in `~/kindle-dash`, branch `quake-pages`):
1. Dashboard running on port 8000 (section 2).
2. `cd ~/kindle-dash/server && .venv/bin/python dash.py serve` (port 8080).
3. On the Kindle: tap **Kindle Dash** in the library. Double tap changes page, the power button exits.
4. **Same Wi-Fi as the laptop.** The Kindle shows "Can't reach …" when it is on a different network.

What it shows: the **quake** page (latest run: people at risk, collapses, deaths, worst-hit areas, SOS counts, responders, drones) and the **sos** page (every open call, most urgent first). A new SOS call switches the Kindle to the SOS page with a black **NEW SOS** banner. Running a new simulation switches it to the quake page. Resolving the last open call switches it back.

The Kindle's address is set by `~/kindle-dash/scripts/install-to-kindle.sh` while the Kindle is plugged in over USB. Run it again if the laptop's IP changes. The old cloud setting is saved on the Kindle as `kindledash.conf.render-backup`.

## 7. A 5 minute demo

1. Dashboard: **Run simulation**. Point at the heat map and the numbers. The Kindle jumps to the quake page.
2. `/drone`: show the two drones. **Pair a phone**, enter the code.
3. On the phone: **Take off**. On the laptop, the live view switches to **Camera**: the phone's picture is the drone's eye.
4. Fly with the sticks for a minute. Switch to **Auto**, then **Take control** to take over the survey drone, then back to **Camera**.
5. Send an SOS from the phone. The Kindle shows **NEW SOS**. Dispatch a team from the dashboard, then resolve the call. The Kindle returns to the numbers.
6. Turn **Speed** up to ×30 and let the auto drone find people (**Sightings** tab).

## 8. When something goes wrong

| What you see | Fix |
|---|---|
| `python: can't open file …/tools/…` | You are in the wrong folder. `cd` to `quake-map/disaster` first. |
| `adb: no devices/emulators found` | Cable is charge-only, USB debugging is off, or the "Allow" prompt was not tapped. Check `lsusb` shows the phone. |
| Phone shows "Not Found" | Wrong port. The address must be `localhost:8000/fpv`. Port 8001 is the old spike test server. |
| `/drone` says "Run a simulation first" or "Streets and facilities are not loaded" | The dashboard was restarted. Do section 2 steps 2 and 3 again. |
| Phone: "Camera blocked" | Chrome → lock icon → Permissions → allow Camera. Reload. |
| Laptop says "No phone camera yet" | Keep `/fpv` open **in front** with the screen on (it stops sending in the background). Check the phone shows its camera picture. |
| Take off seems to do nothing | It takes about 6 seconds. The card says "Taking off in N s". At speed ×1 that is real seconds. |
| Phone page will not load over Wi-Fi, laptop says nothing | Same network? Proxy running and not stopped (`fg`)? `sudo ufw allow … port 8443`? Use the exact `https://<IP>:8443/fpv`. |
| Kindle: "Can't reach http://…:8080" | Wrong Wi-Fi, or the laptop's IP changed (re-run the install script), or the Kindle server is not running. |
| Kindle is slow to react | It checks every few seconds. `POLL_SECONDS=3` in `kindledash.conf` on the Kindle makes it about 1 to 3 seconds. |
| Page looks stale | Reload `/drone` (the files are not cached). If it still looks wrong, restart `uvicorn`. |

## 9. How it works (for questions)

- **Simulated drone:** a flight model with a battery, wind, a reserve for the way home and collision assist. Plans use the same energy formulas as the simulation, so a plan and its flight agree.
- **Lidar and SLAM-style map:** each drone ray-casts a simulated 60 m lidar against box models of the buildings. The hits become a 3D point cloud placed at the drone's *believed* position, which drifts as a random walk (0.12 m per root second), so the map smears the way real SLAM does. It is not real SLAM: nothing estimates position from sensors. Export the cloud as `.ply` from the ⋯ menu.
- **What drones feed back:** the scan marks buildings scanned or not, and a noisy classifier turns what a drone saw into an observed damage class. That updates the "drone-updated" damage picture on the Model tab. The simulated disaster is drawn from the same model, so this tests the workflow, not the model's accuracy.
- **Optional physics mode:** with `DRONE_WORKER_PYTHON` set to a Python that has torch, flight uses the Drone-SLAM quadrotor dynamics (a friend's project, two files vendored with permission). Not needed for the demo.
- **Phone camera:** the phone sends a small JPEG about 8 times a second to the server, which passes only the newest frame to the laptop. Nothing is stored.
- **Code map:** `drone/` (simulation and server, `bridge.py` is the FastAPI part), `web/drone/page` (laptop), `web/drone/fpv` (phone), `web/drone/pilot` (tablet), `kindle_feed.py` (feed for the Kindle), `tests/drone/` (about 150 tests).
- **Settings:** environment variables `DRONE_<NAME>` (see `drone/config.py`): `DRONE_MAX_STATIONS`, `DRONE_LAUNCH_DELAY_S`, `DRONE_CAMERA_DRONE`, `DRONE_ENABLED=0` to switch the whole drone module off.

## 10. Honest limits

- No real drone, no real SLAM. Say "simulated drone with a SLAM-style map" if asked.
- Verified in headless Chrome, with a fake camera, and with an Android 10 phone for the controller and its link. The tablet pilot panel, the 3050 laptop and the physics mode were not run on those devices.
- Deaths and injured figures are the model's casualty estimate, with that model's caveats.
