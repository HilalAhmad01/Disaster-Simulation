"""Compare the GeoJSON building path with the vector-tile path (MASTER_PLAN 6.1): same scenario, same camera, buildings only.
Reports the pixel difference between the two renderings and the bytes/requests each path needs for the view and for a pan.
Needs a running server (default http://localhost:8765) and playwright with Chrome:  python tools/compare_tiles.py
"""
import argparse, io, json, sys
import numpy as np
from PIL import Image

VIEWS = {"2D z16 centre": dict(center=[77.3301, 28.5749], zoom=16.0, pitch=0, bearing=0, three=False),
         "2D z15 wide": dict(center=[77.3301, 28.5749], zoom=15.0, pitch=0, bearing=0, three=False),
         "3D z15.5 pitch 60": dict(center=[77.3301, 28.5749], zoom=15.5, pitch=60, bearing=-20, three=True),
         "3D z14.2 pitch 50": dict(center=[77.33, 28.575], zoom=14.2, pitch=50, bearing=30, three=True)}

BG = np.array([242, 244, 245])      # canvas colour with the basemap hidden

def near_match(a, b):
    """True where pixel a has a colour within 24 of some pixel in the 3x3 neighbourhood of b (absorbs anti-aliasing and 1-px edge shifts)."""
    from numpy.lib.stride_tricks import sliding_window_view
    win = sliding_window_view(np.pad(b, ((1, 1), (1, 1), (0, 0)), mode="edge"), (3, 3), axis=(0, 1))
    return (np.abs(win - a[:, :, :, None, None]).max(axis=2) <= 24).any(axis=(2, 3))

def grab(p, base, tiles, view, scenario):
    pg = p.new_page(viewport={"width": 1100, "height": 800})
    resp = {"n": 0, "bytes": 0, "kinds": set()}
    def on_resp(r):
        u = r.url
        if "/api/buildings" in u or "/tiles/" in u:
            try:
                resp["bytes"] += len(r.body())
            except Exception:
                pass
            resp["n"] += 1
            resp["kinds"].add("tiles" if "/tiles/" in u else "geojson")
    pg.on("response", on_resp)
    pg.goto(f"{base}/?tiles={1 if tiles else 0}")
    pg.wait_for_function("window.map && map.loaded()", timeout=30000)
    pg.wait_for_timeout(1500)
    pg.fill("#mag", str(scenario["mag"])); pg.fill("#depth", str(scenario["depth"]))
    pg.evaluate("document.getElementById('shrink').checked = %s; document.getElementById('shrink').dispatchEvent(new Event('change'))" % ("true" if scenario.get("shrink") else "false"))
    import time as _t
    t_run = _t.time()
    pg.click("#run"); pg.wait_for_function("document.querySelector('#out') && !document.querySelector('#out').hidden", timeout=120000)
    t_shown = _t.time() - t_run
    if tiles:
        pg.wait_for_function("typeof tileMode!=='undefined' && tileMode===true", timeout=60000)
    resp["apply_seconds"] = round(_t.time() - t_run, 2); resp["shown_seconds"] = round(t_shown, 2)
    resp.update(n=0, bytes=0)
    v = VIEWS[view]
    pg.evaluate("""([v])=>{ if(v.three!==is3d) set3d(v.three);
        for (const id of ['osm','heat']) if(map.getLayer(id)) map.setLayoutProperty(id,'visibility','none');
        document.querySelectorAll('.maplibregl-marker').forEach(e=>e.style.display='none');
        map.jumpTo({center:v.center,zoom:v.zoom,pitch:v.pitch,bearing:v.bearing}); }""", [v])
    import time
    t0 = time.time()
    pg.wait_for_timeout(300)
    pg.wait_for_function("map.loaded() && !map.isMoving()", timeout=60000)
    resp["seconds"] = round(time.time() - t0 - 0.3, 2)
    pg.wait_for_timeout(1500)
    shot = pg.screenshot(clip={"x": 330, "y": 0, "width": 760, "height": 800})
    first = dict(n=resp["n"], bytes=resp["bytes"], seconds=resp.get("seconds"), apply=resp.get("apply_seconds"))
    resp.update(n=0, bytes=0)
    pg.evaluate("([v])=>map.jumpTo({center:[v.center[0]+0.004,v.center[1]+0.002],zoom:v.zoom,pitch:v.pitch,bearing:v.bearing})", [v])   # a pan of ~500 m
    pg.wait_for_timeout(500); pg.wait_for_function("map.loaded() && !map.isMoving()", timeout=60000); pg.wait_for_timeout(1000)
    pan = dict(n=resp["n"], bytes=resp["bytes"])
    pg.close()
    return np.asarray(Image.open(io.BytesIO(shot)).convert("RGB")).astype(int), first, pan, sorted(resp["kinds"])

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--base", default="http://localhost:8765"); ap.add_argument("--mag", type=float, default=7.0)
    ap.add_argument("--depth", type=float, default=10); ap.add_argument("--shrink", action="store_true"); ap.add_argument("--out", default=None)
    a = ap.parse_args()
    from playwright.sync_api import sync_playwright
    sc = dict(mag=a.mag, depth=a.depth, shrink=a.shrink)
    rows = []
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True, args=["--use-gl=swiftshader", "--enable-unsafe-swiftshader"])
        for view in VIEWS:
            g, g1, g2, gk = grab(b, a.base, False, view, sc)
            t, t1, t2, tk = grab(b, a.base, True, view, sc)
            d = np.abs(g - t).max(axis=2)
            drawn = (np.abs(g - BG).sum(axis=2) > 30) | (np.abs(t - BG).sum(axis=2) > 30)           # pixels drawn in either image (BG = empty canvas)
            tol = ~(near_match(g, t) & near_match(t, g))
            rows.append({"view": view, "pixels_drawn": int(drawn.sum()), "differ_strict_%": round(100 * float((d[drawn] > 24).mean()), 2),
                         "differ_1px_tolerance_%": round(100 * float(tol[drawn].mean()), 2), "mean_abs_diff": round(float(d[drawn].mean()), 2),
                         "geojson_seconds": g1["seconds"], "tiles_seconds": t1["seconds"], "run_to_tiles_applied_s": t1["apply"], "run_to_geojson_ready_s": g1["apply"],
                         "geojson_first_kB": round(g1["bytes"] / 1e3), "tiles_first_kB": round(t1["bytes"] / 1e3), "geojson_pan_kB": round(g2["bytes"] / 1e3), "tiles_pan_kB": round(t2["bytes"] / 1e3),
                         "geojson_requests": g1["n"], "tile_requests": t1["n"], "path_check": [gk, tk]})
            Image.fromarray(np.hstack([g, t]).astype("uint8")).save(f"cmp_{len(rows)}.png")
        b.close()
    print(json.dumps(rows, indent=1))
    if a.out:
        open(a.out, "w").write(json.dumps(rows, indent=1))

if __name__ == "__main__":
    main()
