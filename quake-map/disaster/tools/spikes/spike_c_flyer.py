"""Spike C (drone Phase 0): can the Drone-SLAM quadrotor + heuristic flyer do what a city sortie needs?

Run with an interpreter that has torch, and point --repo at a clone of Drone-SLAM (its `droneforest` package is imported
read-only; nothing is installed or copied):

    python -I tools/spikes/spike_c_flyer.py --repo /path/to/Drone-SLAM [--device cpu|cuda]

Measures: (1) control-step time (policy runs at 50 Hz, so it must stay under 20 ms), (2) the stock forest scenario with the
heuristic flyer (success / collision / timeout), (3) straight-line tracking of a moving look-ahead waypoint at several
cruise speeds over flat ground (the way a city transit leg is flown), (4) INS drift growth.
"""
import argparse, pathlib, sys, time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--leg", type=float, default=400.0, help="straight transit leg length in metres")
    a = ap.parse_args()
    sys.path.insert(0, str(pathlib.Path(a.repo).expanduser().resolve()))
    sys.path.insert(0, str(pathlib.Path(a.repo).expanduser().resolve() / "droneforest"))
    import torch
    from droneforest.env import DroneForestEnv, MAX_RANGE
    from droneforest.heuristic import heuristic_action
    from droneforest.world import Forest
    dev = torch.device(a.device)
    print(f"torch {torch.__version__} device {dev} threads {torch.get_num_threads()}")

    # ---- 1 + 2: stock forest, many envs
    for n in (1, 32):
        env = DroneForestEnv(n, dev, realism=True, drift=0.12, wind_sigma=0.4, seed=3, init_level=0.5, max_level=0.5, freeze_curriculum=True, ep_len=600)
        env.reset()
        ts, succ, coll, tout, ep = [], 0, 0, 0, 0
        t_end = time.time() + (25 if n == 1 else 60)
        steps = 0
        while time.time() < t_end and steps < 3000:
            t0 = time.perf_counter()
            with torch.no_grad():
                act = heuristic_action(env, vmax=4.5)
            _, _, done, info = env.step(act)
            ts.append(time.perf_counter() - t0); steps += 1
            if done.any():
                succ += int(info["success"].sum()); coll += int(info["collision"].sum()); tout += int(info["timeout"].sum())
        tot = succ + coll + tout
        print(f"[forest level 0.5, {n:2d} envs] step median {1000*sorted(ts)[len(ts)//2]:6.2f} ms  p95 {1000*sorted(ts)[int(len(ts)*.95)]:6.2f} ms  "
              f"episodes {tot}: success {succ}, collision {coll}, timeout {tout}" + (f"  (success rate {succ/tot:.0%})" if tot else ""))

    # ---- 3: straight transit leg over flat ground, moving look-ahead waypoint (the mission.py technique)
    print(f"\ntransit leg of {a.leg:.0f} m, flat ground, no obstacles, wind sigma 0.4, drift 0.12:")
    for vmax in (5.0, 8.0, 12.0, 15.0):
        env = DroneForestEnv(1, dev, realism=True, drift=0.12, wind_sigma=0.4, seed=5)
        f = Forest(1, 8, dev); f.terr = torch.zeros_like(f.terr)               # flat: zero terrain coefficients
        env.forest = f
        ids = torch.zeros(1, dtype=torch.long, device=dev)
        start = torch.tensor([[0.0, 0.0, 30.0]], device=dev)
        env.q.reset(ids, start, torch.zeros(1, device=dev)); env.est_bias.zero_(); env.prev_a.zero_()
        env._compute_obs(ids, fresh=True)
        dt = env.dt_phys * env.decim
        look, errs, speeds, tilt, steps, t0 = 9.0, [], [], [], 0, time.perf_counter()
        gnd = float(f.ground(ids, start[:, 0:1], start[:, 1:2])[0, 0])
        while env.q.pos[0, 0] < a.leg and steps < int(180 / dt):
            x = float(env.q.pos[0, 0])
            env.goal[0] = torch.tensor([min(x + look * max(vmax / 5, 1.0), a.leg), 0.0, 30.0], device=dev)
            with torch.no_grad():
                act = heuristic_action(env, vmax=vmax)
            env.q.wind += (-env.q.wind / 2.0) * dt + 0.4 * (2 * dt / 2.0) ** 0.5 * torch.randn_like(env.q.wind)
            env.est_bias += env.drift * dt ** 0.5 * torch.randn_like(env.est_bias)
            for _ in range(env.decim):
                env.q.step(act, env.dt_phys)
            env.prev_a = act.clone(); env._compute_obs(ids)
            errs.append(abs(float(env.q.pos[0, 1]))); speeds.append(float(env.q.vel[0, :2].norm()))
            steps += 1
        wall = time.perf_counter() - t0
        sim_t = steps * dt
        reached = float(env.q.pos[0, 0]) >= a.leg - 1
        print(f"  vmax {vmax:4.1f} m/s: {'reached' if reached else 'NOT reached'} in {sim_t:5.1f} sim s; mean speed {sum(speeds)/len(speeds):5.2f}, "
              f"max cross-track {max(errs):5.2f} m, ground z {gnd:.2f}, wall {wall:.1f} s ({sim_t/wall:.1f}x real time)")

    # ---- 4: drift growth
    print("\nINS drift (random walk, sigma per axis per sqrt(s)=0.12): expected |bias| after 60 s ~ "
          f"{0.12 * 60 ** 0.5 * (3 ** 0.5):.1f} m, after 600 s ~ {0.12 * 600 ** 0.5 * (3 ** 0.5):.1f} m (3-D)")
    print(f"scan: {env.local_dirs.shape[0]} rays in a forward fan (az +-70 deg, el +-35 deg), MAX_RANGE {MAX_RANGE} m, policy rate {1/ (env.dt_phys*env.decim):.0f} Hz")


if __name__ == "__main__":
    main()
