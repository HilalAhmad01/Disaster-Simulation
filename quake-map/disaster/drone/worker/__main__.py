"""Physics worker: Drone-SLAM's quadrotor dynamics and heuristic flyer, one simulated quadrotor per drone, driven over JSON lines.

    python -m drone.worker [--device cpu|cuda]

stdin  -> {"op": "reset", "id", "pos": [x, y, z], "yaw", "seed"}
          {"op": "step",  "id", "goal": [x, y, z], "vmax", "n": control steps (20 ms each)}
          {"op": "quit"}
stdout <- one JSON line per request: the quadrotor's {pos, vel, yaw, tilt}.
The flyer here only tracks a waypoint (the city is flown above roof level, so no obstacle scan is needed): obstacle ranges are all 'clear'.
"""
import argparse, json, math, sys
import torch
from drone.droneforest.dynamics import Quadrotor, quat_to_rot, quat_yaw
from drone.droneforest.flyer import MAX_RANGE, N_AZ, N_EL, heuristic_action

DT_PHYS, DECIM = 0.01, 2


class _Flat:
    def ground(self, idx, x, y):
        return torch.zeros_like(x)


class Rig:
    """One quadrotor and the little 'environment' object the heuristic flyer reads."""
    def __init__(self, device, seed):
        torch.manual_seed(seed)
        self.device, self.n = device, 1
        self.q = Quadrotor(1, device)
        self.goal = torch.zeros(1, 3, device=device)
        self.tree_t = torch.full((1, N_AZ * N_EL), MAX_RANGE, device=device)
        self.forest = _Flat()

    def reset(self, pos, yaw):
        self.q.reset(torch.zeros(1, dtype=torch.long, device=self.device), torch.tensor([pos], dtype=torch.float32, device=self.device), torch.tensor([yaw], dtype=torch.float32, device=self.device))
        self.q.wind.zero_()

    def step(self, goal, vmax, n):
        self.goal[0] = torch.tensor(goal, dtype=torch.float32, device=self.device)
        dt = DT_PHYS * DECIM
        for _ in range(n):
            with torch.no_grad():
                a = heuristic_action(self, vmax=vmax)
            self.q.wind += (-self.q.wind / 2.0) * dt + 0.4 * (2 * dt / 2.0) ** 0.5 * torch.randn_like(self.q.wind)       # gusts (Ornstein-Uhlenbeck)
            for _ in range(DECIM):
                self.q.step(a, DT_PHYS)
        return self.state()

    def state(self):
        q = self.q
        R = quat_to_rot(q.quat)[0]
        tilt = math.degrees(math.acos(max(-1.0, min(1.0, float(R[2, 2])))))
        return {"pos": [float(v) for v in q.pos[0]], "vel": [float(v) for v in q.vel[0]], "yaw": float(quat_yaw(q.quat)[0]), "tilt": tilt}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="cpu")
    a = ap.parse_args()
    torch.set_num_threads(1)                                  # one small quadrotor: threads only add overhead
    dev = torch.device(a.device if a.device != "auto" else ("cuda" if torch.cuda.is_available() and False else "cpu"))
    rigs: dict[str, Rig] = {}
    print(json.dumps({"hello": True, "torch": torch.__version__, "device": str(dev), "python": sys.version.split()[0]}), flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
            op = m["op"]
            if op == "quit":
                break
            if op == "reset":
                r = rigs[m["id"]] = Rig(dev, int(m.get("seed", 0)))
                r.reset(m["pos"], float(m.get("yaw", 0.0)))
                out = r.state()
            elif op == "step":
                out = rigs[m["id"]].step(m["goal"], float(m["vmax"]), int(m["n"]))
            else:
                out = {"error": f"unknown op {op}"}
        except Exception as ex:                               # report and keep serving
            out = {"error": f"{type(ex).__name__}: {ex}"}
        print(json.dumps(out), flush=True)


if __name__ == "__main__":
    main()
