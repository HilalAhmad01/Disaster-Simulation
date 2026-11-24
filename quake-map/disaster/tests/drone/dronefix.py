"""A small synthetic city for the drone tests: a 20 x 20 grid of buildings over ~2 km, a street grid, facilities with police stations.
No network, no real data. `make_city()` returns everything the drone service needs."""
import numpy as np, pandas as pd, geopandas as gpd, shapely
from shapely.geometry import box, LineString
from pyproj import Transformer

import roads as R, responder_layer as RL


def make_city(monkeypatch=None, tmp_path=None, quake=(7.4, 10.0), n_side=20, spacing=100.0, seed=3):
    if monkeypatch is not None and tmp_path is not None:
        monkeypatch.setenv("VS30_TIF", str(tmp_path / "none.tif"))
    from simulate import Model
    to_ll = Transformer.from_crs(32643, 4326, always_xy=True)
    x0, y0 = Transformer.from_crs(4326, 32643, always_xy=True).transform(77.40, 28.50)
    rng = np.random.default_rng(seed)
    geoms, lv = [], []
    for j in range(n_side):
        for i in range(n_side):
            cx, cy = x0 + 40 + i * spacing + rng.uniform(-4, 4), y0 + 40 + j * spacing + rng.uniform(-4, 4)
            w, d = rng.uniform(14, 24), rng.uniform(10, 18)
            g = shapely.affinity.rotate(box(cx - w / 2, cy - d / 2, cx + w / 2, cy + d / 2), rng.uniform(-25, 25))
            geoms.append(shapely.transform(g, lambda c: np.column_stack(to_ll.transform(c[:, 0], c[:, 1]))))
            lv.append(int(rng.choice([1, 2, 3, 4, 6])))
    ex = gpd.GeoDataFrame({"btype": np.where(np.array(lv) <= 2, "URM", "RC_LOW"), "levels": lv, "occupants": 18, "use": "residential"}, geometry=geoms, crs=4326)
    m = Model(ex)
    # street grid, nodes every 200 m
    n = n_side // 2 + 1
    xy = [(x0 + i * 200, y0 + j * 200) for j in range(n) for i in range(n)]
    u, v, g, rid = [], [], [], []
    k = 0
    for j in range(n):
        for i in range(n):
            for di, dj in ((1, 0), (0, 1)):
                if i + di < n and j + dj < n:
                    a, b = j * n + i, (j + dj) * n + i + di
                    line = LineString([xy[a], xy[b]]); u += [a, b]; v += [b, a]; g += [line, line]; rid += [k, k]; k += 1
    net = R.RoadNet(xy, u, v, [20.0] * len(u), [200.0] * len(u), g, [7.0] * len(u), road_id=rid, utm="EPSG:32643")
    pts = [(x0 + 10, y0 + 10), (x0 + 1990, y0 + 1990), (x0 + 1990, y0 + 10), (x0 + 10, y0 + 1990), (x0 + 1000, y0 + 1000), (x0 + 500, y0 + 1500)]
    kinds = ["hospital", "hospital", "fire_station", "police", "police", "police"]
    ll = [to_ll.transform(*p) for p in pts]
    fac = pd.DataFrame({"id": range(len(pts)), "kind": kinds, "name": [f"{k}{i}" for i, k in enumerate(kinds)], "lon": [p[0] for p in ll], "lat": [p[1] for p in ll],
                        "source": "osm", "beds": np.nan, "building_id": -1})
    layer = RL.Layer(m, net, fac)
    lon0, lat0 = to_ll.transform(x0 + 1000, y0 + 1000)
    if quake:
        m.run(lat0, lon0, quake[1], quake[0])
    return {"model": m, "layer": layer, "lon0": lon0, "lat0": lat0, "x0": x0, "y0": y0}
