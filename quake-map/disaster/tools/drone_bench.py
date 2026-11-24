"""Benchmark the drone service on a saved area: tick time with several drones flying, memory growth, planner and queue cost, message volume.

    python tools/drone_bench.py [--area baseline] [--speed 60] [--minutes 30] [--drones 5]
"""
import argparse, json, pathlib, resource, sys, time
import numpy as np
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def rss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default="baseline"); ap.add_argument("--speed", type=float, default=60); ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--drones", type=int, default=5); ap.add_argument("--mag", type=float, default=7.2)
    a = ap.parse_args()
    from areas import Registry
    from simulate import Model
    import responder_layer, live
    from drone.config import Config
    from drone.service import DroneService, FakeCalls
    data = ROOT / "data"
    t = time.perf_counter(); m = Model(Registry(data).load_exposure(a.area), data_dir=data); b = m.bounds
    m.run((b[1] + b[3]) / 2, (b[0] + b[2]) / 2, 10, a.mag, "night"); print(f"model + run {time.perf_counter()-t:.1f} s, {len(m.g):,} buildings")
    t = time.perf_counter(); layer = responder_layer.build_for_area(data, a.area, m, tuple(float(x) for x in b)); print(f"responder layer {time.perf_counter()-t:.1f} s")
    t = time.perf_counter(); p_edge = live.blockage_now(layer); print(f"blockage {time.perf_counter()-t:.1f} s")
    cfg = Config(max_stations=a.drones, launch_delay_s=20.0)
    calls = FakeCalls(speed=a.speed); svc = DroneService(cfg, calls)
    n_msg, n_bytes = [0], [0]
    def count(msg):
        n_msg[0] += 1; n_bytes[0] += len(json.dumps(msg, default=lambda o: o.item() if hasattr(o, "item") else list(o), separators=(",", ":")))
    svc.subscribe(count)
    t = time.perf_counter(); svc.bind(m, layer, calls, p_edge); print(f"bind {time.perf_counter()-t:.2f} s, {len(svc.units)} drones")
    t = time.perf_counter(); svc.refresh_queue(force=True); print(f"queue refresh {1000*(time.perf_counter()-t):.0f} ms")
    r0 = rss_mb(); ticks, wall, wall_t0 = [], 1000.0, time.perf_counter()
    sim_end = a.minutes * 60
    while svc.clock.t < sim_end:
        wall += 0.1
        t0 = time.perf_counter(); svc.tick(0.1, wall); ticks.append((time.perf_counter() - t0) * 1000)
    el = time.perf_counter() - wall_t0
    tk = np.array(ticks)
    print(f"\n{a.minutes:.0f} simulated minutes at x{a.speed:g} ({len(ticks)} ticks of 0.1 s wall): ran in {el:.1f} s")
    print(f"tick ms: median {np.median(tk):.2f}  p95 {np.percentile(tk,95):.2f}  p99 {np.percentile(tk,99):.2f}  max {tk.max():.1f}  (budget 100 ms)")
    print(f"messages {n_msg[0]:,} ({n_msg[0]/ (len(ticks)*0.1):.0f}/s wall), {n_bytes[0]/1e6:.1f} MB ({n_bytes[0]/(len(ticks)*0.1)/1024:.0f} KB/s wall)")
    print(f"memory growth {rss_mb()-r0:.0f} MB (peak RSS {rss_mb():.0f} MB); recording {len(svc.recording):,} msgs, cloud {len(svc.cloud):,} pts, detections {len(svc.detections)}")
    st = [u.status for u in svc.units.values()]; print("drone states:", {s: st.count(s) for s in set(st)}, "| scanned", svc.scan.counts()["scanned"], "buildings")
    t = time.perf_counter(); svc.model_summary(); print(f"model summary {1000*(time.perf_counter()-t):.0f} ms")


if __name__ == "__main__":
    main()
