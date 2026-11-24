#!/usr/bin/env python3
"""Start the whole demo with one command: dashboard, simulation, responders, drones, phone links, Kindle.

    ./start_demo.sh                      (from quake-map/disaster; or:  python tools/start_demo.py)
    ./start_demo.sh --restart            stop what is running first and start everything fresh
    ./start_demo.sh --no-kindle --no-browser --mag 7.1 --speed 10

What it does, in order (anything already running and healthy is reused, nothing is started twice):
  1. dashboard            uvicorn app:app on 127.0.0.1:8000
  2. scenario             runs an earthquake, loads hospitals / fire stations / streets, starts the two simulated drones
  3. phone, USB           adb reverse tcp:8000 (so http://localhost:8000/fpv and /sos work on a phone on a cable)
  4. phone, Wi-Fi         the https proxy on :8443 (phones only share camera and GPS on https)
  5. Kindle               the kindle-dash server on :8080, and, if the Kindle is plugged in, installs the current address on it
  6. a short menu         p = new pairing code, s = send test SOS calls, c = clear them, r = run a new earthquake, d = start the drones, q = quit

Ctrl+C stops everything this script started. It ignores Ctrl+Z, because a frozen proxy looks like a working one.
It never uses sudo: if the firewall needs a rule it prints the command.
"""
import argparse, json, os, pathlib, re, select, shutil, signal, socket, subprocess, sys, time, urllib.error, urllib.request

HERE = pathlib.Path(__file__).resolve().parent.parent          # quake-map/disaster
LOGS = HERE / "data" / "demo-logs"                              # data/ is git-ignored
DASH_PORT, PROXY_PORT, KINDLE_PORT = 8000, 8443, 8080
PROCS = []                                                      # (name, Popen) started by this script


# ------------------------------------------------------------------ small helpers
def say(msg="", *a):
    print(msg % a if a else msg, flush=True)

def step(msg):
    say("\n== %s", msg)

def ok(msg):
    say("   ok    %s", msg)

def warn(msg):
    say("   WARN  %s", msg)

def fail(msg):
    say("   FAIL  %s", msg)

def http(method, url, body=None, timeout=10, verify=True):
    data = json.dumps(body if body is not None else {}).encode() if method == "POST" else None
    req = urllib.request.Request(url, data=data, method=method, headers={"content-type": "application/json"} if data is not None else {})
    ctx = None
    if url.startswith("https") and not verify:
        import ssl
        ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        raw = r.read()
        return r.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw.decode(errors="replace"))

def up(url, verify=True, timeout=4):
    try:
        return http("GET", url, timeout=timeout, verify=verify)[0] < 500
    except Exception:
        return False

def lan_ip():
    try:
        out = subprocess.run(["ip", "-4", "route", "get", "1.1.1.1"], capture_output=True, text=True, timeout=5).stdout
        m = re.search(r"src (\S+)", out)
        if m:
            return m.group(1)
    except Exception:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1)); return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"

def wifi_name():
    try:
        out = subprocess.run(["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"], capture_output=True, text=True, timeout=5).stdout
        return next((l.split(":", 1)[1] for l in out.splitlines() if l.startswith("yes:")), None)
    except Exception:
        return None

def listener(port):
    """(pid, command line) of whatever listens on a TCP port, or None."""
    try:
        out = subprocess.run(["ss", "-ltnpH", f"sport = :{port}"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    m = re.search(r"pid=(\d+)", out)
    if not m:
        return (None, "") if out.strip() else None
    pid = int(m.group(1))
    try:
        cmd = pathlib.Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
    except OSError:
        cmd = ""
    return pid, cmd

def proc_state(pid):
    try:
        return pathlib.Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except OSError:
        return "?"

def stop_pid(pid, wait=4.0):
    try:
        os.kill(pid, signal.SIGCONT)                              # a stopped process cannot handle SIGTERM
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    t0 = time.time()
    while time.time() - t0 < wait and os.path.exists(f"/proc/{pid}"):
        time.sleep(0.1)
    if os.path.exists(f"/proc/{pid}"):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

def launch(name, cmd, cwd, env=None):
    LOGS.mkdir(parents=True, exist_ok=True)
    log = open(LOGS / f"{name}.log", "w")
    p = subprocess.Popen(cmd, cwd=cwd, env=env or os.environ.copy(), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         start_new_session=True)                  # own session: Ctrl+C / Ctrl+Z in this terminal never reach it
    PROCS.append((name, p))
    return p

def wait_for(check, seconds, every=0.4):
    t0 = time.time()
    while time.time() - t0 < seconds:
        if check():
            return True
        time.sleep(every)
    return False

def tail(name, n=6):
    try:
        return "\n".join("         " + l for l in (LOGS / f"{name}.log").read_text(errors="replace").splitlines()[-n:])
    except OSError:
        return ""

def ensure_service(name, port, match, health, start, restart, verify=True, seconds=40):
    """Reuse a healthy service on the port, replace a stopped or stuck one of ours, refuse to touch a foreign one."""
    found = listener(port)
    if found:
        pid, cmd = found
        ours = match in cmd
        if not ours:
            fail(f"port {port} is used by something else ({cmd or 'unknown'}). Stop it or free the port.")
            return False
        state = proc_state(pid)
        if restart or state in ("T", "t") or not up(health, verify):
            why = "restart requested" if restart else "stopped (Ctrl+Z?)" if state in ("T", "t") else "not answering"
            warn(f"{name} on :{port} is {why}: replacing it")
            stop_pid(pid)
            time.sleep(0.5)
        else:
            ok(f"{name} already running on :{port}: reusing it (pid {pid})")
            return True
    start()
    if wait_for(lambda: up(health, verify), seconds):
        ok(f"{name} started on :{port}")
        return True
    fail(f"{name} did not come up in {seconds} s. Last log lines:\n{tail(name)}")
    return False


# ------------------------------------------------------------------ the steps
def dashboard(a):
    base = f"http://127.0.0.1:{DASH_PORT}"
    return ensure_service("dashboard", DASH_PORT, "uvicorn", base + "/api/meta",
                          lambda: launch("dashboard", [sys.executable, "-m", "uvicorn", "app:app", "--port", str(DASH_PORT)], HERE), a.restart, seconds=90)

RESP_JOB = [None]                                               # the id of the responders download, so the menu can ask how it is going

def pick_area(a, base):
    """--area: activate a cached area by id or name. An area never prepared before needs a road download (minutes); a cached one takes seconds."""
    areas = http("GET", base + "/api/areas")[1]
    cur = next((x for x in areas["areas"] if x["id"] == areas.get("active")), None)
    if a.area:
        want = next((x for x in areas["areas"] if a.area.lower() in (str(x["id"]).lower(), str(x.get("name", "")).lower())), None)
        if want is None:
            warn("no area called '%s'. Available: %s" % (a.area, ", ".join(f"{x['id']} ({x.get('buildings', 0):,} buildings)" for x in areas["areas"])))
        elif not cur or want["id"] != cur["id"]:
            http("POST", f"{base}/api/areas/{want['id']}/activate", timeout=180)
            cur = want
            ok(f"switched to area {want['id']}")
    if cur:
        ok(f"area: {cur['id']} ({cur.get('buildings', 0):,} buildings). Pick another with --area; the others: " +
           (", ".join(x["id"] for x in areas["areas"] if x["id"] != cur["id"]) or "none"))

def load_responders(base, wait):
    """True once hospitals, fire stations and streets are loaded. Starts the download if needed and waits up to `wait` seconds."""
    try:
        if http("GET", base + "/api/live/resources", timeout=90)[1].get("loaded"):
            return True
    except Exception:
        pass
    if RESP_JOB[0] is None:
        say("   loading hospitals, fire stations and streets (a cached area takes seconds, a new one a few minutes and needs internet)...")
        RESP_JOB[0] = http("POST", base + "/api/responders/prepare")[1]["job"]
    last, t0, j = "", time.time(), {}
    while True:
        j = http("GET", f"{base}/api/jobs/{RESP_JOB[0]}")[1]
        msg = j.get("message") or ""
        if msg and msg != last:
            say("         %s", msg); last = msg
        if j.get("state") in ("done", "error", "failed") or time.time() - t0 >= wait:
            break
        time.sleep(2)
    if j.get("state") == "done":
        RESP_JOB[0] = None
        return True
    if j.get("state") in ("error", "failed"):
        RESP_JOB[0] = None
        warn(f"responders failed: {j.get('error') or j.get('message')}")
    return False

def start_drones(a, base):
    try:
        snap = http("POST", base + "/api/drone/start", timeout=60)[1]
        ok("drones started: " + ", ".join(f"{d['label']} ({d['status']})" for d in snap.get("snapshot", {}).get("fleet", [])))
    except urllib.error.HTTPError as e:
        warn(f"drones did not start: {e.read().decode(errors='replace')[:160]}")
        return False
    try:
        http("POST", base + "/api/live/speed", {"speed": a.speed})
        ok(f"simulation speed set to x{a.speed:g}")
    except Exception as e:
        warn(f"could not set the speed: {e}")
    return True

def scenario(a):
    base = f"http://127.0.0.1:{DASH_PORT}"
    meta = http("GET", base + "/api/meta")[1]
    if meta.get("empty"):
        warn("no area is loaded yet. Load one on the dashboard (Load a new area), then run this again. Skipping the scenario.")
        return False
    pick_area(a, base)
    meta = http("GET", base + "/api/meta")[1]
    if not a.no_sim:
        try:
            run = http("GET", base + "/api/live/area")[1].get("run", 0)
        except Exception:
            run = 0
        if run and not a.new_sim and not a.restart:
            ok(f"an earthquake is already loaded (run {run}): keeping it. Use --new-sim for a fresh one, or press r below.")
        else:
            new_run(a, meta)
    if load_responders(base, a.responders_wait):
        ok("hospitals, fire stations and streets loaded")
        return start_drones(a, base)
    warn("the responders are still downloading. The rest starts now. Press d below once it says done to start the drones "
         "(or restart with --area <a cached area> for an instant start).")
    return False


def new_run(a, meta=None):
    base = f"http://127.0.0.1:{DASH_PORT}"
    meta = meta or http("GET", base + "/api/meta")[1]
    lon, lat = (a.lon, a.lat) if a.lat is not None and a.lon is not None else meta["center"]
    body = {"lat": lat, "lon": lon, "depth_km": a.depth, "magnitude": a.mag, "time_of_day": a.time_of_day, "uncertainty": False}
    out = http("POST", base + "/api/simulate", body, timeout=180)[1]
    s = out["summary"]
    ok(f"earthquake M{a.mag:g} run {out['run']}: {s['people_at_risk']:,} people at risk, {s['expected_collapses']:,} expected collapses")

def adb_usb():
    if not shutil.which("adb"):
        warn("adb is not installed: the USB phone link is skipped (the Wi-Fi link below still works)")
        return False
    out = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=20).stdout
    devices = [l.split()[0] for l in out.splitlines()[1:] if l.split()[-1:] == ["device"]]
    if not devices:
        warn("no phone on USB (plug it in with USB debugging on, then run `adb reverse tcp:8000 tcp:8000`). Wi-Fi link below.")
        return False
    r = subprocess.run(["adb", "reverse", f"tcp:{DASH_PORT}", f"tcp:{DASH_PORT}"], capture_output=True, text=True, timeout=20)
    if r.returncode:
        warn("adb reverse failed: " + (r.stderr or r.stdout).strip())
        return False
    ok(f"USB phone link ready ({len(devices)} device): on the phone open http://localhost:{DASH_PORT}/fpv")
    return True

def firewall_hint(port, who):
    try:
        rules = pathlib.Path("/etc/ufw/user.rules").read_text()
    except OSError:
        return
    if "ufw-user-input" in rules and f"--dport {port}" not in rules:
        warn(f"the firewall has no rule for port {port} ({who}). Run once:  sudo ufw allow from 192.168.0.0/24 to any port {port} proto tcp")

def proxy(a):
    return ensure_service("https-proxy", PROXY_PORT, "https_proxy", f"https://127.0.0.1:{PROXY_PORT}/api/meta",
                          lambda: launch("https-proxy", [sys.executable, str(HERE / "tools" / "https_proxy.py")], HERE), a.restart, verify=False, seconds=30)

def kindle(a, ip):
    kd = pathlib.Path(a.kindle_dir).expanduser()
    py = kd / "server" / ".venv" / "bin" / "python"
    if not (kd / "server" / "dash.py").exists():
        warn(f"kindle-dash not found at {kd}: skipping the Kindle (use --kindle-dir)")
        return False
    good = ensure_service("kindle-server", KINDLE_PORT, "dash.py", f"http://127.0.0.1:{KINDLE_PORT}/healthz",
                          lambda: launch("kindle-server", [str(py if py.exists() else sys.executable), "dash.py", "serve"], kd / "server"), a.restart)
    if not good:
        return False
    st = up(f"http://{ip}:{KINDLE_PORT}/state", timeout=15)
    ok(f"Kindle server answers on http://{ip}:{KINDLE_PORT}") if st else warn("the Kindle server does not answer on the network address")
    firewall_hint(KINDLE_PORT, "Kindle")
    if a.no_kindle_install:
        return True
    user = os.environ.get("USER", "")
    vol = next((p for p in (f"/run/media/{user}/Kindle", f"/media/{user}/Kindle") if os.path.isdir(p + "/documents")), None)
    if not vol:
        say("   (Kindle not plugged in. When you plug it in, run this again and it installs the current address, or: sh %s/scripts/install-to-kindle.sh)", kd)
        return True
    env = dict(os.environ, KINDLE_VOL=vol, SERVER_IP=ip)
    r = subprocess.run(["sh", str(kd / "scripts" / "install-to-kindle.sh")], cwd=kd, env=env, capture_output=True, text=True, timeout=120)
    if r.returncode:
        warn("installing on the Kindle failed: " + (r.stdout + r.stderr).strip()[-200:])
        return True
    conf = pathlib.Path(vol) / "extensions" / "kindledash" / "kindledash.conf"
    try:                                                          # quick checks for new SOS calls and simulations
        text = conf.read_text()
        text = re.sub(r"(?m)^POLL_SECONDS=\d+", "POLL_SECONDS=3", text)
        conf.write_text(text)
    except OSError:
        pass
    subprocess.run(["sync"], timeout=60)
    src = subprocess.run(["findmnt", "-n", "-o", "SOURCE", vol], capture_output=True, text=True).stdout.strip()
    if src and shutil.which("udisksctl"):
        r = subprocess.run(["udisksctl", "unmount", "-b", src], capture_output=True, text=True, timeout=30)
        ok("Kindle updated for http://%s:%d and safely unmounted: unplug it, then tap 'Kindle Dash'" % (ip, KINDLE_PORT) if not r.returncode
           else "Kindle updated for http://%s:%d (eject it before unplugging)" % (ip, KINDLE_PORT))
    else:
        ok("Kindle updated for http://%s:%d (eject it before unplugging)" % (ip, KINDLE_PORT))
    return True

def pair_code():
    base = f"http://127.0.0.1:{DASH_PORT}"
    try:
        c = http("POST", base + "/api/drone/pair/code", {"role": "pilot"})[1]
        return c["code"], c.get("expires_in")
    except Exception as e:
        return None, str(e)

def send_sos(n):
    try:
        say("   sent %s test SOS call(s)", http("POST", f"http://127.0.0.1:{DASH_PORT}/api/live/simulate", {"n": n})[1].get("added"))
    except Exception as e:
        warn(f"could not send SOS calls: {e}")

def summary(a, ip, usb):
    ssid = wifi_name()
    code, exp = pair_code()
    L = f"http://localhost:{DASH_PORT}"
    S = f"https://{ip}:{PROXY_PORT}"
    say("\n" + "=" * 70)
    say(" READY. Everything below is simulated; the phone camera picture is real.")
    say("=" * 70)
    say(" Laptop       %s              the dashboard", L)
    say("              %s/drone        the drone console (fleet, live view)", L)
    say(" Phone (USB)  %s/fpv    controller   %s/sos    send an SOS%s", L, L, "" if usb else "   [no phone on USB right now]")
    say(" Phone (Wi-Fi)%s/fpv     controller   %s/sos    send an SOS", S, S)
    say("              the phone must be on %s; first time tap Advanced > Proceed on the certificate warning", ssid or "the same Wi-Fi as this laptop")
    say(" Kindle        http://%s:%d   (the Kindle must be on %s)", ip, KINDLE_PORT, ssid or "the same Wi-Fi")
    if code:
        say(" Pair a phone 6-digit code:  %s     (valid %s s; press p for a new one)", code, exp)
    say("-" * 70)
    say(" p new pairing code   s send 3 test SOS   c clear SOS calls   r new earthquake   d start drones   q quit")
    say("=" * 70)

def shutdown(*_):
    say("\nStopping what this script started...")
    for name, p in reversed(PROCS):
        if p.poll() is None:
            stop_pid(p.pid)
            say("   stopped %s", name)
    if shutil.which("adb"):
        subprocess.run(["adb", "reverse", "--remove", f"tcp:{DASH_PORT}"], capture_output=True)
    say("Done. (Anything that was already running before this script is left running.)")
    sys.exit(0)

def loop(a):
    stdin_ok = sys.stdin is not None and not sys.stdin.closed
    fd = sys.stdin.fileno() if stdin_ok else None
    pending = ""
    while True:
        for name, p in PROCS:
            if p.poll() is not None:
                fail(f"{name} stopped by itself (exit {p.returncode}). Last log lines:\n{tail(name)}")
                shutdown()
        if not stdin_ok:
            time.sleep(2)                                         # no terminal (running in the background): just keep everything up
            continue
        if not select.select([fd], [], [], 2.0)[0]:
            continue
        chunk = os.read(fd, 1024)                                 # os.read, not readline: several piped lines must not get stuck in a buffer
        if not chunk:
            stdin_ok = False
            continue
        pending += chunk.decode(errors="replace")
        while "\n" in pending:
            line, pending = pending.split("\n", 1)
            k = line.strip().lower()[:1]
            if k == "p":
                code, exp = pair_code()
                say("   pairing code: %s  (valid %s s)" % (code, exp) if code else "   could not make a code: %s" % exp)
            elif k == "s":
                send_sos(3)
            elif k == "c":
                try:
                    http("POST", f"http://127.0.0.1:{DASH_PORT}/api/live/clear"); say("   SOS calls cleared")
                except Exception as e:
                    warn(str(e))
            elif k == "r":
                try:
                    new_run(a)
                except Exception as e:
                    warn(f"could not run the earthquake: {e}")
            elif k == "d":
                base = f"http://127.0.0.1:{DASH_PORT}"
                try:
                    start_drones(a, base) if load_responders(base, 0) else say("   the responders are still loading: try again in a minute")
                except Exception as e:
                    warn(str(e))
            elif k == "q":
                shutdown()
            elif k:
                say(" p new pairing code   s send 3 test SOS   c clear SOS calls   r new earthquake   d start drones   q quit")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--restart", action="store_true", help="stop what is running first and start everything fresh (also runs a new earthquake)")
    ap.add_argument("--new-sim", action="store_true", help="run a new earthquake even if one is already loaded")
    ap.add_argument("--no-sim", action="store_true", help="do not run an earthquake (use whatever is loaded)")
    ap.add_argument("--mag", type=float, default=6.8, help="earthquake magnitude (default 6.8)")
    ap.add_argument("--depth", type=float, default=10.0, help="depth in km (default 10)")
    ap.add_argument("--lat", type=float), ap.add_argument("--lon", type=float)
    ap.add_argument("--time-of-day", default="night", choices=["day", "evening", "night"])
    ap.add_argument("--speed", type=float, default=5.0, help="simulation speed (default x5)")
    ap.add_argument("--area", help="use this area (id or name) instead of the one the dashboard opened on; a cached one starts in seconds")
    ap.add_argument("--responders-wait", type=float, default=240, help="how long to wait for the hospitals and streets download before carrying on (default 240 s)")
    ap.add_argument("--kindle-dir", default="~/kindle-dash", help="where the kindle-dash project is (default ~/kindle-dash)")
    ap.add_argument("--no-kindle", action="store_true", help="skip the Kindle server")
    ap.add_argument("--no-kindle-install", action="store_true", help="start the Kindle server but do not touch a plugged-in Kindle")
    ap.add_argument("--no-proxy", action="store_true", help="skip the https proxy (phones then work over USB only)")
    ap.add_argument("--no-browser", action="store_true", help="do not open the drone console in the browser")
    a = ap.parse_args()

    signal.signal(signal.SIGTSTP, signal.SIG_IGN)                  # Ctrl+Z would freeze this script; ignore it
    signal.signal(signal.SIGINT, shutdown); signal.signal(signal.SIGTERM, shutdown)
    ip = lan_ip()
    say("Disaster demo launcher  (this laptop: %s%s)", ip, f", Wi-Fi {wifi_name()}" if wifi_name() else "")

    step("1/5 dashboard")
    if not dashboard(a):
        shutdown()
    step("2/5 scenario, responders and drones")
    scenario(a)
    step("3/5 phone over USB")
    usb = adb_usb()
    step("4/5 phone over Wi-Fi (https)")
    if a.no_proxy:
        say("   skipped (--no-proxy)")
    else:
        if proxy(a):
            firewall_hint(PROXY_PORT, "phone https link")
    step("5/5 Kindle")
    if a.no_kindle:
        say("   skipped (--no-kindle)")
    else:
        kindle(a, ip)
    summary(a, ip, usb)
    if not a.no_browser and shutil.which("xdg-open") and os.environ.get("DISPLAY", os.environ.get("WAYLAND_DISPLAY")):
        subprocess.Popen(["xdg-open", f"http://localhost:{DASH_PORT}/drone"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    loop(a)


if __name__ == "__main__":
    main()
