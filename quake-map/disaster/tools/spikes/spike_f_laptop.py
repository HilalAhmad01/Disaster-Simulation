"""Spike F (drone Phase 0): is this laptop ready to run the Drone-SLAM physics drone and the tablet link?

Run on the demo laptop (the RTX 3050 one) with the Python that has torch:

    python -I tools/spikes/spike_f_laptop.py --repo /path/to/Drone-SLAM

Prints: OS / Python / torch / CUDA / GPU name and memory, the tools the tablet connection needs (adb, openssl, a firewall note),
and a control-step benchmark of the Drone-SLAM environment on CPU (1 thread and default threads) and on CUDA if present.
Then also run:  python -I tools/spikes/spike_c_flyer.py --repo /path/to/Drone-SLAM --device cuda
"""
import argparse, os, pathlib, platform, shutil, subprocess, sys, time


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception as ex:
        return f"({ex})"


def bench(torch, repo, device, threads, n=1, steps=600):
    from droneforest.env import DroneForestEnv
    from droneforest.heuristic import heuristic_action
    torch.set_num_threads(threads)
    env = DroneForestEnv(n, torch.device(device), realism=True, drift=0.12, wind_sigma=0.4, seed=1, init_level=0.3, max_level=0.3, freeze_curriculum=True)
    env.reset()
    for _ in range(30):                                   # warm-up (kernel compilation, caches)
        with torch.no_grad():
            env.step(heuristic_action(env, vmax=5.0))
    if device == "cuda":
        torch.cuda.synchronize()
    ts = []
    for _ in range(steps):
        t0 = time.perf_counter()
        with torch.no_grad():
            env.step(heuristic_action(env, vmax=5.0))
        if device == "cuda":
            torch.cuda.synchronize()
        ts.append(time.perf_counter() - t0)
    ts.sort()
    med, p95 = ts[len(ts) // 2] * 1000, ts[int(len(ts) * .95)] * 1000
    real_time_x = (0.02 * 1000) / med                     # one control step is 20 ms of simulated time (50 Hz)
    print(f"  {device:5s} threads={threads:2d} envs={n}: step median {med:6.2f} ms, p95 {p95:6.2f} ms  -> about {real_time_x:4.1f}x real time for ONE drone")
    return med


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    a = ap.parse_args()
    repo = pathlib.Path(a.repo).expanduser().resolve()
    if not (repo / "droneforest" / "env.py").is_file():
        print(f"NOTE: {repo} does not look like a Drone-SLAM checkout (no droneforest/env.py); the control-step benchmark will be skipped. "
              "The hardware and tablet-link checks below still run.")
    sys.path.insert(0, str(repo))
    print(f"OS: {platform.platform()}\nPython: {sys.version.split()[0]}  ({sys.executable})")
    try:
        import torch
    except Exception as ex:
        print("torch: NOT importable:", ex); torch = None
    if torch:
        print(f"torch {torch.__version__}  cuda build {torch.version.cuda}  cuda available {torch.cuda.is_available()}")
        if torch.cuda.is_available():
            p = torch.cuda.get_device_properties(0)
            free, total = torch.cuda.mem_get_info()
            print(f"GPU: {p.name}, {p.total_memory/2**30:.1f} GB total, {free/2**30:.1f} GB free right now (a browser with a 3D map uses part of this)")
    print("nvidia-smi:", sh("nvidia-smi --query-gpu=name,memory.total,memory.used,driver_version --format=csv,noheader") or "not found")
    print("\ntablet link tools:")
    print("  adb:      ", sh("adb version | head -1") or "NOT installed (needed for the USB option: install android-tools / platform-tools)")
    print("  openssl:  ", sh("openssl version") or "NOT installed (needed by tools/https_proxy.py for the hotspot option)")
    print("  firewall: ", sh("ufw status 2>/dev/null | head -1") or "ufw not found; if the tablet cannot connect on the hotspot, allow ports 8000/8443")
    print("  wifi/hotspot capable: check that the laptop can start a hotspot (nmcli device wifi hotspot ...) or use a small router")
    if torch is None or not (repo / "droneforest" / "env.py").is_file():
        return
    print("\nDrone-SLAM control step benchmark (policy runs at 50 Hz = 20 ms per step):")
    cores = os.cpu_count() or 4
    try:
        bench(torch, repo, "cpu", 1)
        bench(torch, repo, "cpu", cores)
        if torch.cuda.is_available():
            bench(torch, repo, "cuda", 1)
    except Exception as ex:                                # a missing dependency or a Drone-SLAM version mismatch: say so, do not lose the report above
        print(f"  benchmark failed: {type(ex).__name__}: {ex}")
    print("\nRule of thumb: if CPU with 1 thread is already >= 4x real time, the GPU is not needed for one drone; keep it for the browser.")


if __name__ == "__main__":
    main()
