"""tools/start_demo.py: the one-command launcher. Only its logic is tested here (reuse, refuse, replace); the real run needs the whole app."""
import http.server, importlib.util, os, pathlib, signal, socket, subprocess, sys, threading, time
import pytest

SPEC = importlib.util.spec_from_file_location("start_demo", pathlib.Path(__file__).resolve().parent.parent / "tools" / "start_demo.py")
sd = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(sd)


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.fixture()
def server():
    """A tiny HTTP server in its own process, so it shows up as a listener with a pid and a command line."""
    port = free_port()
    p = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert sd.wait_for(lambda: sd.up(f"http://127.0.0.1:{port}/"), 10)
    yield port, p
    p.kill(); p.wait()


def test_listener_finds_the_pid_and_command_line(server):
    port, p = server
    pid, cmd = sd.listener(port)
    assert pid == p.pid and "http.server" in cmd


def test_a_healthy_service_of_ours_is_reused_not_started_twice(server):
    port, p = server
    started = []
    assert sd.ensure_service("web", port, "http.server", f"http://127.0.0.1:{port}/", lambda: started.append(1), restart=False)
    assert started == [] and p.poll() is None


def test_a_foreign_process_on_the_port_is_never_touched(server):
    port, p = server
    started = []
    assert not sd.ensure_service("web", port, "some-other-program", f"http://127.0.0.1:{port}/", lambda: started.append(1), restart=False)
    assert started == [] and p.poll() is None


def test_a_stopped_service_is_replaced(server):
    """A process frozen with Ctrl+Z holds its port but answers nothing: the launcher must replace it, not reuse it."""
    port, p = server
    os.kill(p.pid, signal.SIGSTOP)
    time.sleep(0.3)
    assert sd.proc_state(p.pid) in ("T", "t")
    new = []
    def start():
        new.append(subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    try:
        assert sd.ensure_service("web", port, "http.server", f"http://127.0.0.1:{port}/", start, restart=False, seconds=15)
        assert new and p.wait(timeout=5) is not None                              # the old one is gone, the new one answers
    finally:
        for n in new:
            n.kill(); n.wait()


def test_help_lists_the_options():
    out = subprocess.run([sys.executable, str(pathlib.Path(sd.__file__)), "--help"], capture_output=True, text=True, timeout=30).stdout
    for flag in ("--restart", "--area", "--no-kindle", "--no-proxy", "--speed", "--mag"):
        assert flag in out
