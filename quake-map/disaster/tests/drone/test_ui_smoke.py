"""Browser smoke tests (headless Chrome via Playwright): the drone page, the tablet pilot page and the dashboard's drone button load, pair and
react without a single JavaScript error. Skipped when Playwright or Chrome is not installed."""
import socket, threading, time, json, urllib.request
import pytest
pw = pytest.importorskip("playwright.sync_api")
import shutil
if not (shutil.which("google-chrome") or shutil.which("chrome") or shutil.which("chromium")):
    pytest.skip("no Chrome/Chromium on this machine", allow_module_level=True)
import uvicorn
import dronefix

IGNORE = ("Failed to load resource", "net::ERR", "tile.openstreetmap", "fonts.g", "favicon")


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("DRONE_AUTO_MODE", "0"); mp.setenv("DRONE_LAUNCH_DELAY_S", "5"); mp.setenv("DRONE_MAX_STATIONS", "3")
    d = tmp_path_factory.mktemp("ui")
    city = dronefix.make_city(mp, d)
    import app as appmod
    app = appmod.create_app(d)
    st = app.state.st
    st.model, st.layer = city["model"], city["layer"]
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=srv.run, daemon=True); th.start()
    t0 = time.time()
    while not srv.started and time.time() - t0 < 20:
        time.sleep(0.1)
    base = f"http://127.0.0.1:{port}"
    yield base, app
    srv.should_exit = True; th.join(5); mp.undo()


def post(base, path, body=None):
    r = urllib.request.Request(base + path, data=json.dumps(body or {}).encode(), headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(r).read())


@pytest.fixture()
def page(server):
    with pw.sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True, args=["--ignore-gpu-blocklist", "--enable-unsafe-swiftshader", "--use-gl=angle", "--use-angle=swiftshader"])
        ctx = b.new_context(viewport={"width": 1440, "height": 900}, has_touch=True)
        pg = ctx.new_page()
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append("PAGEERROR " + str(e)))
        pg.on("console", lambda m: pg.errors.append("console: " + m.text[:200]) if m.type == "error" and not any(k in m.text for k in IGNORE) else None)
        yield pg
        b.close()


def test_drone_page_loads_lists_the_fleet_and_switches_tabs(server, page):
    base, app = server
    page.goto(base + "/drone")
    page.wait_for_selector(".drone", timeout=30000)
    assert page.locator(".drone").count() >= 1
    assert page.inner_text(".tag") == "Simulated"
    for tab in ("finds", "model", "tablet", "queue"):
        page.click(f"[data-tab={tab}]"); page.wait_for_timeout(300)
        assert page.get_attribute(f"[data-tab={tab}]", "aria-selected") == "true"
    page.click("#themeBtn"); page.wait_for_timeout(200)
    assert page.evaluate("document.documentElement.dataset.theme") in ("dark", "light")
    assert page.locator("#speed button").count() == 5
    assert page.errors == []


def test_drone_page_launch_shows_the_drone_in_flight(server, page):
    base, app = server
    post(base, "/api/live/speed", {"speed": 60})
    page.goto(base + "/drone"); page.wait_for_selector("[data-a=launch]", timeout=30000)
    page.click("[data-a=launch]")
    page.wait_for_function("window.__drone.store.airborne().length > 0", timeout=60000)
    page.wait_for_timeout(1500)
    assert page.locator(".dr-mk.drone").count() == 1                              # the heading marker is on the map
    assert page.inner_text("#glance").startswith("1 flying")
    assert page.errors == []
    for d in app.state.drone.units.values():                                         # tidy: bring it home so later tests start clean
        if d.kd.airborne:
            app.state.drone.return_home(d.id)


def test_pilot_page_pairs_with_a_code_and_shows_the_cockpit(server, page):
    base, app = server
    code = post(base, "/api/drone/pair/code", {"role": "pilot"})["code"]
    page.goto(base + "/pilot"); page.wait_for_selector("#keypad button")
    page.click("#keypad button:text-is('9')"); page.click("#keypad button:text-is('Back')")   # editing works
    for ch in code:
        page.click(f"#keypad button:text-is('{ch}')")
    page.click("#pairGo")
    page.wait_for_selector("#cockpit:not([hidden])", timeout=15000)
    page.wait_for_function("window.__pilot.store.ready && window.__pilot.store.fleet.size > 0", timeout=15000)
    assert "SIMULATED" in page.inner_text(".sim").upper() and page.locator("#stickL").count() == 1
    assert page.errors == []
    assert page.evaluate("localStorage.getItem('drone.pilot.token')")


def test_pilot_page_with_a_wrong_code_says_so(server, page):
    base, _ = server
    page.goto(base + "/pilot"); page.wait_for_selector("#keypad button")
    for ch in "000000":
        page.click(f"#keypad button:text-is('{ch}')")
    page.click("#pairGo"); page.wait_for_selector("#pairErr:not([hidden])", timeout=5000)
    assert "wrong or expired" in page.inner_text("#pairErr")


def test_dashboard_has_a_drone_button_that_opens_the_drone_page(server, page):
    base, _ = server
    page.goto(base + "/")
    try:
        page.wait_for_selector("#drBtn", timeout=20000)
    except Exception:
        pytest.skip("the dashboard could not load its map library (offline?)")
    page.click("#drBtn"); page.wait_for_url("**/drone", timeout=10000)
    assert page.errors == []


def test_fpv_phone_camera_page_takes_off_takes_control_and_flies(server):
    base, app = server
    post(base, "/api/live/speed", {"speed": 60})
    code = post(base, "/api/drone/pair/code", {"role": "pilot"})["code"]
    with pw.sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True, args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
                                                                        "--ignore-gpu-blocklist", "--enable-unsafe-swiftshader"])
        ctx = b.new_context(viewport={"width": 800, "height": 380}, has_touch=True, permissions=["camera"])
        pg = ctx.new_page(); errors = []
        pg.on("pageerror", lambda e: errors.append("PAGEERROR " + str(e)))
        pg.on("console", lambda m: errors.append("console: " + m.text[:200]) if m.type == "error" and not any(k in m.text for k in IGNORE) else None)
        pg.goto(base + "/fpv?code=" + code)
        pg.wait_for_selector("#code"); assert pg.input_value("#code") == code        # the link carries the code
        pg.click("#pairGo"); pg.wait_for_selector("#fly:not([hidden])", timeout=15000)
        pg.wait_for_function("window.__fpv.store.fleet.size > 0", timeout=15000)
        pg.wait_for_function("document.getElementById('cam').videoWidth > 0", timeout=15000)       # the camera feed is live
        assert "simulated" in pg.inner_text(".sim").lower()
        pg.wait_for_function("!document.getElementById('go').disabled && document.getElementById('go').textContent === 'Take off'", timeout=15000)
        pg.click("#go")
        pg.wait_for_function("document.getElementById('go').textContent === 'Hold to return'", timeout=60000)   # airborne, and the phone took control
        did = pg.evaluate("window.__fpv.store.active().id")
        x0 = app.state.drone.units[did].kd.mode
        assert x0 == "manual"
        bx = pg.locator("#stickL").bounding_box()                                               # push the move stick forward with a finger
        cx, cy = bx["x"] + bx["width"] / 2, bx["y"] + bx["height"] / 2
        pg.mouse.move(cx, cy); pg.mouse.down(); pg.mouse.move(cx, cy - bx["height"] * 0.4, steps=4)
        pg.wait_for_timeout(1500)
        spd = pg.evaluate("window.__fpv.store.fleet.get(window.__fpv.store.active().id).speed")
        pg.mouse.up()
        assert spd > 0.5, spd
        assert errors == [], errors
        for d in app.state.drone.units.values():
            if d.kd.airborne:
                app.state.drone.return_home(d.id)
        b.close()


def test_phone_camera_frames_reach_the_commander_page(server):
    base, app = server
    code = post(base, "/api/drone/pair/code", {"role": "pilot"})["code"]
    with pw.sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True, args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
                                                                        "--ignore-gpu-blocklist", "--enable-unsafe-swiftshader"])
        errors = []
        def watch(pg):
            pg.on("pageerror", lambda e: errors.append("PAGEERROR " + str(e)))
            pg.on("console", lambda m: errors.append("console: " + m.text[:200]) if m.type == "error" and not any(k in m.text for k in IGNORE) else None)
        lap = b.new_context(viewport={"width": 1440, "height": 900}).new_page(); watch(lap)
        lap.goto(base + "/drone"); lap.wait_for_selector(".drone", timeout=30000)
        ph = b.new_context(viewport={"width": 800, "height": 380}, has_touch=True, permissions=["camera"]).new_page(); watch(ph)
        ph.goto(base + "/fpv?code=" + code); ph.click("#pairGo")
        ph.wait_for_function("document.getElementById('cam').videoWidth > 0 && window.__fpv.store.fleet.size > 0", timeout=20000)
        lap.wait_for_function("document.querySelector('[data-r=cam]') && !document.querySelector('[data-r=cam]').hidden", timeout=20000)      # the feed shows up by itself
        assert lap.evaluate("document.querySelector('[data-mode=phone]').getAttribute('aria-pressed')") == "true"
        assert lap.evaluate("document.querySelector('[data-r=cam]').src.startsWith('data:image/jpeg;base64,/9j/')")
        ph.close()                                                                         # phone gone: the page says the feed stopped
        lap.wait_for_function("document.querySelector('[data-r=cam]').hidden", timeout=10000)
        lap.wait_for_function("__drone.live.mode === 'chase'", timeout=10000)                  # back to the 3D view, not a dead screen
        assert errors == [], errors
        b.close()


def test_commander_page_follows_the_drone_the_phone_camera_belongs_to(server):
    base, app = server
    code = post(base, "/api/drone/pair/code", {"role": "pilot"})["code"]
    with pw.sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True, args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
                                                                        "--ignore-gpu-blocklist", "--enable-unsafe-swiftshader"])
        lap = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
        lap.goto(base + "/drone"); lap.wait_for_selector(".drone", timeout=30000)
        other = next(d for d in lap.evaluate("__drone.store.drones()") if d["role"] != "camera")["id"]
        lap.evaluate(f"__drone.live.select('{other}')")                                # the laptop is looking at the other drone
        ph = b.new_context(viewport={"width": 800, "height": 380}, has_touch=True, permissions=["camera"]).new_page()
        ph.goto(base + "/fpv?code=" + code); ph.click("#pairGo")
        lap.wait_for_function("document.querySelector('[data-r=cam]') && !document.querySelector('[data-r=cam]').hidden", timeout=20000)
        assert lap.evaluate("__drone.store.fleet.get(__drone.live.sel).role") == "camera"
        b.close()


def test_fpv_phone_can_switch_to_the_auto_drone_and_fly_it_then_hand_it_back(server):
    base, app = server
    post(base, "/api/live/speed", {"speed": 60})
    code = post(base, "/api/drone/pair/code", {"role": "pilot"})["code"]
    with pw.sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True, args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
                                                                        "--ignore-gpu-blocklist", "--enable-unsafe-swiftshader"])
        pg = b.new_context(viewport={"width": 800, "height": 380}, has_touch=True, permissions=["camera"]).new_page(); errors = []
        pg.on("pageerror", lambda e: errors.append("PAGEERROR " + str(e)))
        pg.goto(base + "/fpv?code=" + code); pg.click("#pairGo")
        pg.wait_for_selector("#pick button:text-is('Auto')", timeout=20000)
        assert pg.get_attribute("#pick button:text-is('Camera')", "aria-pressed") == "true"      # the camera drone is the default
        pg.click("#pick button:text-is('Auto')")
        assert pg.get_attribute("#pick button:text-is('Auto')", "aria-pressed") == "true"
        pg.wait_for_function("document.getElementById('go').textContent === 'Take off' && !document.getElementById('go').disabled", timeout=15000)
        pg.click("#go")                                                                         # the auto drone takes off on a survey sortie of its own
        pg.wait_for_function("document.getElementById('go').textContent === 'Take control'", timeout=60000)
        auto_id = pg.evaluate("__fpv.st.sel")
        assert app.state.drone.units[auto_id].kd.mode != "manual"                               # it keeps flying itself until asked
        pg.click("#go")
        pg.wait_for_function("document.getElementById('go').textContent === 'Hold to return'", timeout=15000)
        assert app.state.drone.units[auto_id].kd.mode == "manual"
        pg.click("#pick button:text-is('Camera')")                                              # switching away hands it back
        pg.wait_for_function(f"__fpv.store.fleet.get('{auto_id}').mode !== 'manual'", timeout=15000)
        assert app.state.drone.units[auto_id].kd.mode != "manual"
        assert errors == [], errors
        for d in app.state.drone.units.values():
            if d.kd.airborne:
                app.state.drone.return_home(d.id)
        b.close()


def test_fleet_buttons_survive_telemetry_and_launch_responds(server):
    """Regression: the fleet list used to be rebuilt about twice a second, so a click that straddled a rebuild did nothing."""
    base, app = server
    post(base, "/api/live/speed", {"speed": 30})
    with pw.sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True, args=["--ignore-gpu-blocklist", "--enable-unsafe-swiftshader"])
        pg = b.new_context(viewport={"width": 1440, "height": 900}).new_page(); errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(base + "/drone"); pg.wait_for_selector("[data-a=launch]", timeout=30000)
        post(base, "/api/drone/auto", {"enabled": True})
        pg.wait_for_selector("[data-a=rth]", timeout=60000)                               # the auto drone is flying: its card changes all the time
        pg.evaluate("document.querySelector('[data-a=rth]').dataset.tag = 'same node'")
        pg.evaluate("document.querySelector('.drone-main').dataset.tag = 'same node'")
        pg.wait_for_timeout(3500)
        assert pg.evaluate("document.querySelector('[data-a=rth]').dataset.tag") == "same node"         # not replaced while only the numbers changed
        assert pg.evaluate("document.querySelector('.drone-main').dataset.tag") == "same node"
        pg.click("[data-a=launch]")                                                      # the camera drone's Take off
        pg.wait_for_function("__drone.store.drones().some(d => d.role === 'camera' && d.status !== 'ready')", timeout=10000)
        assert "Taking off" in pg.inner_text("[data-r=empty]") or pg.evaluate("__drone.store.drones().some(d => d.role === 'camera' && d.z > 0.5)")
        pg.wait_for_function("__drone.store.drones().some(d => d.role === 'camera' && d.z > 0.5)", timeout=30000)
        assert errors == [], errors
        for d in app.state.drone.units.values():
            if d.kd.airborne:
                app.state.drone.return_home(d.id)
        b.close()
