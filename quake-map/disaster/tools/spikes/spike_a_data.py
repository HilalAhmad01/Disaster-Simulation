"""Spike A (drone Phase 0): what per-building data does a finished run expose, how fast, and how big?

Loads a saved area, runs one earthquake, then times and sizes the arrays the drone module needs:
building id, centroid, footprint, height, occupants, damage probabilities, police stations (and their linked buildings),
and the road-graph blockage array. Read-only: it changes no project file.

    python tools/spikes/spike_a_data.py [--area noida] [--mag 7.0]
"""
import argparse, pathlib, sys, time
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

def tic():
    t = time.perf_counter()
    return lambda: time.perf_counter() - t

def mb(a):
    return a.nbytes / 1e6

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", default="noida")
    ap.add_argument("--mag", type=float, default=7.0)
    a = ap.parse_args()

    from areas import Registry
    from simulate import Model
    import facilities as F

    data = ROOT / "data"
    reg = Registry(data)
    t = tic(); g = reg.load_exposure(a.area); print(f"load exposure     {t():6.2f} s   {len(g):,} buildings, columns {list(g.columns)}")
    t = tic(); m = Model(g, data_dir=data); print(f"Model()           {t():6.2f} s")
    b = m.bounds
    lat, lon = (b[1] + b[3]) / 2, (b[0] + b[2]) / 2
    t = tic(); out = m.run(lat, lon, 10.0, a.mag, "night"); print(f"run (M{a.mag})        {t():6.2f} s   summary keys: {sorted(out['summary'])[:8]}...")
    L = m.last
    print("\nmodel.last columns:", list(L.columns))

    # --- arrays the drone module needs, and their size in memory
    t = tic()
    geom = L.geometry
    pc = L["p_collapse"].to_numpy(float); pe = L["p_extensive"].to_numpy(float)
    occ = L["occupants"].to_numpy(float); lv = L["levels"].to_numpy(float)
    print(f"\nextract arrays    {t():6.3f} s   (id = row index 0..{len(L)-1}; no stable string id exists)")
    for name, arr in (("lat", m.lat), ("lon", m.lon), ("x", m.x), ("y", m.y), ("p_collapse", pc), ("p_extensive", pe), ("occupants", occ), ("levels", lv), ("W (types)", m.W)):
        print(f"   {name:12s} {arr.shape}  {mb(arr):6.1f} MB")
    print(f"   levels_src counts: {L['levels_src'].value_counts().to_dict() if 'levels_src' in L else 'n/a'}")
    print(f"   expected collapses {pc.sum():,.0f}   people at risk {out['summary']['people_at_risk']:,}   occupants total {occ.sum():,.0f}")

    # --- footprint -> oriented box (the drone's collider) for the densest 250 m chunk
    import shapely
    utm = m.g.estimate_utm_crs()
    gu = L.geometry.to_crs(utm)
    key = (m.x // 250).astype(np.int64) * 100000 + (m.y // 250).astype(np.int64)
    ck, cnt = np.unique(key, return_counts=True)
    j = int(np.argmax(cnt))
    print(f"\n250 m chunks: {len(ck):,} non-empty; buildings per chunk median {int(np.median(cnt))}, p95 {int(np.percentile(cnt, 95))}, max {int(cnt.max())}")
    idx = np.flatnonzero(key == ck[j])
    t = tic()
    rects = shapely.minimum_rotated_rectangle(gu.geometry.to_numpy()[idx])
    print(f"min rotated rectangle for {len(idx)} buildings: {t()*1000:.1f} ms")
    area_ratio = shapely.area(rects) / np.maximum(shapely.area(gu.geometry.to_numpy()[idx]), 1e-6)
    print(f"   rectangle/footprint area ratio: median {np.median(area_ratio):.2f}, share >1.4 (L-shaped etc.) {np.mean(area_ratio > 1.4):.1%}")

    # --- stations
    fac = F.load_or_build(data, a.area, tuple(float(x) for x in b), m.g) if F.path_for(data, a.area).exists() else None
    if fac is None:
        print("\nno cached facilities for this area"); return
    print(f"\nfacilities columns: {list(fac.columns)}")
    print("facility kinds:", fac["kind"].value_counts().to_dict())
    pol = fac[fac["kind"] == "police"]
    print(f"police stations: {len(pol)}")
    st = F.functionality(fac, pe)
    print("functionality columns:", list(st.columns))
    pol_state = pol.join(st) if len(st) == len(fac) else None
    if pol_state is not None:
        print(pol_state[[c for c in ("id", "name", "lat", "lon", "p_damaged", "functionality", "may_not_work", "building") if c in pol_state.columns]].head(8).to_string())

    # --- road graph
    import roads as R
    gp = R.graph_path(data, a.area)
    print(f"\nroad graph cache for '{a.area}': {'present' if gp.exists() else 'MISSING (would need network)'}")

if __name__ == "__main__":
    main()
