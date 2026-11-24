"""L1 flight: Drone-SLAM's quadrotor dynamics and flyer run in a worker process (torch); this side has no torch.

`PhysicsDrone` is a KDrone whose position comes from the worker instead of straight-line interpolation: the flyer tracks a look-ahead
waypoint, so the drone banks, overshoots a little and fights gusts. Battery drain uses the simulated velocity with the same power model.
If the worker dies, the drone keeps flying with the simple (L0) motion.
"""
import json, math, os, pathlib, select, subprocess, threading
import numpy as np
from .sim import KDrone, KIND_STATUS

ROOT = pathlib.Path(__file__).resolve().parent.parent


class WorkerError(RuntimeError):
    pass


class WorkerClient:
    def __init__(self, python: str, device: str = "cpu", timeout: float = 10.0):
        self.python, self.device, self.timeout = python, device, timeout
        self.proc = None
        self.lock = threading.Lock()
        self.info = {}
        self.calls, self.total_s = 0, 0.0

    def start(self):
        self.proc = subprocess.Popen([self.python, "-u", "-m", "drone.worker", "--device", self.device], cwd=str(ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1, env={**os.environ, "PYTHONPATH": str(ROOT)})
        self.info = self._read(30.0)
        if "hello" not in self.info:
            raise WorkerError(f"physics worker did not start: {self.info}")
        return self.info

    def _read(self, timeout):
        r, _, _ = select.select([self.proc.stdout], [], [], timeout)
        if not r:
            raise WorkerError("physics worker timed out")
        line = self.proc.stdout.readline()
        if not line:
            raise WorkerError("physics worker exited")
        return json.loads(line)

    def call(self, msg: dict) -> dict:
        import time
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                raise WorkerError("physics worker is not running")
            t0 = time.perf_counter()
            try:
                self.proc.stdin.write(json.dumps(msg) + "\n"); self.proc.stdin.flush()
                out = self._read(self.timeout)
            except (BrokenPipeError, OSError, ValueError) as ex:
                raise WorkerError(str(ex))
            self.calls += 1; self.total_s += time.perf_counter() - t0
        if "error" in out:
            raise WorkerError(out["error"])
        return out

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def close(self):
        try:
            if self.alive:
                self.proc.stdin.write('{"op":"quit"}\n'); self.proc.stdin.flush(); self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


class PhysicsDrone(KDrone):
    fidelity = "L1"

    def __init__(self, cfg, did, station_id, home, seed, worker: WorkerClient):
        super().__init__(cfg, did, station_id, home, seed)
        self.worker, self.phys_ok, self.seed = worker, True, seed
        self.tilt = 0.0

    def launch(self, route, sortie_id=None):
        super().launch(route, sortie_id)
        self._reset_quad()

    def _reset_quad(self):
        try:
            self.worker.call({"op": "reset", "id": self.id, "pos": [float(self.pos[0]), float(self.pos[1]), 0.0], "yaw": float(self.yaw), "seed": int(self.seed)})
            self.phys_ok = True
        except WorkerError:
            self.phys_ok = False

    def take_control(self):
        super().take_control()
        if self.phys_ok:
            self._sync_quad()

    def _sync_quad(self):
        try:
            self.worker.call({"op": "reset", "id": self.id, "pos": [float(v) for v in self.pos], "yaw": float(self.yaw), "seed": int(self.seed)})
        except WorkerError:
            self.phys_ok = False

    def _phys(self, goal, vmax, h) -> bool:
        try:
            r = self.worker.call({"op": "step", "id": self.id, "goal": [float(v) for v in goal], "vmax": float(vmax), "n": max(1, round(h / 0.02))})
        except WorkerError:
            self.phys_ok = False
            return False
        self.pos[:] = r["pos"]; self.vel[:] = r["vel"]; self.tilt = r["tilt"]
        if math.hypot(self.vel[0], self.vel[1]) > 0.8:
            self.yaw = math.atan2(self.vel[1], self.vel[0])
        return True

    # ---- route following with the physics drone
    def _follow(self, dt, ev, sim_t):
        if not self.phys_ok:
            return super()._follow(dt, ev, sim_t)
        cfg, remaining, guard = self.cfg, dt, 0
        while remaining > 1e-9 and self.airborne and self.mode in ("auto", "assisted", "rth") and guard < 200 and self.phys_ok:
            guard += 1
            if self.dwell > 0:
                use = min(self.dwell, remaining, 0.5)
                self._phys(self.pos, 3.0, use)
                self._drain(0.0, 0.0, use); self.dwell -= use; remaining -= use
                self.status = "surveying"
                continue
            if self.i >= len(self.route):
                self._finish(ev); break
            wp = self.route[self.i]
            self.status = KIND_STATUS.get(wp.get("kind"), "transit"); self.task_id = wp.get("task_id")
            tgt = np.array([wp["x"], wp["y"], wp["z"]], float)
            d = tgt - self.pos
            dh, dz = math.hypot(d[0], d[1]), d[2]
            speed = wp.get("speed", cfg.cruise_speed_ms)
            land = wp.get("kind") == "land"
            tol = max(3.0, 0.5 * speed)
            if (land and self.pos[2] < 1.7 and dh < 4.0) or (not land and dh < tol and abs(dz) < max(2.5, 0.3 * speed)):
                if land:
                    self._drain(0.0, 0.0, 0.0, cfg.landing_wh)
                self.pos[2] = 0.0 if land else self.pos[2]
                self._arrive(wp, ev, sim_t)
                if self.status in ("landed", "lost", "crashed"):
                    break
                continue
            h = min(remaining, 0.5)
            look = 9.0 + 0.8 * speed
            dist = float(np.linalg.norm(d))
            goal = tgt if dist <= look else self.pos + d / dist * look
            if not self._phys(goal, min(cfg.max_speed_ms * 1.1, max(speed / 0.8, 3.0)), h):
                break
            self._drain(math.hypot(self.vel[0], self.vel[1]), max(0.0, float(self.vel[2])), h)
            self.dist_m += math.hypot(self.vel[0], self.vel[1]) * h
            remaining -= h
        if remaining > 1e-9 and not self.phys_ok and self.airborne and self.mode in ("auto", "assisted", "rth"):
            self._leg = None
            super()._follow(remaining, ev, sim_t)               # the worker died mid-step: finish this tick with the simple motion

    def _integrate(self, wvx, wvy, vz, dt):
        if not self.phys_ok:
            return super()._integrate(wvx, wvy, vz, dt)
        look = 2.0
        goal = self.pos + np.array([wvx, wvy, vz]) * look
        goal[2] = max(goal[2], 0.5)
        speed = math.hypot(wvx, wvy)
        if not self._phys(goal if (wvx or wvy or vz) else self.pos, max(speed * 1.25, 1.0) if (wvx or wvy or vz) else 1.0, dt):
            return super()._integrate(wvx, wvy, vz, dt)
        return self.pos.copy()
