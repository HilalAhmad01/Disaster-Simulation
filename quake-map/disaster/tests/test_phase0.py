"""Phase 0: startup without data, area registry (clip rule, cache, release), background jobs."""
import time, types
import numpy as np, pandas as pd, pytest
gpd = pytest.importorskip("geopandas")
import shapely
from shapely.geometry import box

def fake_raw(n=400, seed=3, box_=(77.30, 28.55, 77.36, 28.60)):
    rng = np.random.default_rng(seed)
    x = box_[0] - 0.01 + rng.random(n) * (box_[2] - box_[0] + 0.02)
    y = box_[1] - 0.01 + rng.random(n) * (box_[3] - box_[1] + 0.02)
    hw = rng.uniform(5e-5, 2e-4, n)
    geoms = [box(a - h, b - h, a + h, b + h) for a, b, h in zip(x, y, hw)]
    b = shapely.bounds(np.array(geoms, dtype=object))
    return gpd.GeoDataFrame({"class": [None] * n, "subtype": [None] * n, "height": np.nan, "num_floors": np.nan,
                             "xmin": b[:, 0], "xmax": b[:, 2], "ymin": b[:, 1], "ymax": b[:, 3]}, geometry=geoms, crs=4326)

@pytest.fixture(autouse=True)
def _flat_soil(monkeypatch, tmp_path):
    monkeypatch.setenv("VS30_TIF", str(tmp_path / "none.tif"))

# ---------- 0.2 ----------
def test_server_starts_without_data_and_says_so(tmp_path):
    from fastapi.testclient import TestClient
    from app import create_app
    c = TestClient(create_app(tmp_path))
    m = c.get("/api/meta").json()
    assert m["empty"] is True and m["bounds"] is None and len(m["center"]) == 2
    assert c.get("/").status_code == 200
    for r in (c.post("/api/simulate", json={"lat": 28.5, "lon": 77.4, "magnitude": 6.5}),
              c.get("/api/buildings", params=dict(w=77, s=28, e=78, n=29)),
              c.get("/api/building/0"),
              c.get("/api/buildings3d", params=dict(w=77, s=28, e=78, n=29, clat=28.5, clon=77.4))):
        assert r.status_code == 409 and "Load an area first" in r.json()["detail"]
    assert c.get("/api/area/download").status_code == 404

def test_default_center_env(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import create_app
    monkeypatch.setenv("DEFAULT_CENTER", "72.8,19.07")
    assert TestClient(create_app(tmp_path)).get("/api/meta").json()["center"] == [72.8, 19.07]

# ---------- 0.4 ----------
def test_clip_rule_matches_overture_filter():
    """clip_to_bbox must pick exactly what overturemaps' own bbox filter would pick on the same rows."""
    import pyarrow as pa, pyarrow.dataset as ds, pyarrow.compute as pc
    from overture import clip_to_bbox
    raw = fake_raw(600)
    q = (77.31, 28.56, 77.33, 28.58)
    t = pa.table({"bbox": pa.StructArray.from_arrays(
        [pa.array(raw[c].to_numpy()) for c in ("xmin", "xmax", "ymin", "ymax")], names=["xmin", "xmax", "ymin", "ymax"]),
        "k": pa.array(range(len(raw)))})
    w, s, e, n = q
    flt = ((pc.field("bbox", "xmin") < e) & (pc.field("bbox", "xmax") > w) & (pc.field("bbox", "ymin") < n) & (pc.field("bbox", "ymax") > s))
    theirs = ds.dataset(t).to_table(filter=flt)["k"].to_pylist()
    assert sorted(theirs) == sorted(raw.index[(raw.xmin < e) & (raw.xmax > w) & (raw.ymin < n) & (raw.ymax > s)].tolist())
    assert len(clip_to_bbox(raw, q)) == len(theirs) > 0
    # touching edges only (xmax == w) is excluded, as in Overture
    edge = raw.iloc[:1].copy(); edge["xmax"] = w
    assert len(clip_to_bbox(edge, q)) == 0

def test_overture_fetch_reports_progress_and_flattens_bbox():
    pa = pytest.importorskip("pyarrow")
    from overture import fetch_buildings
    import overturemaps.core as core
    geoms = [box(77.3, 28.5, 77.301, 28.501), box(77.31, 28.51, 77.311, 28.511)]
    def table(i):
        g = geoms[i]
        b = pa.StructArray.from_arrays([pa.array([g.bounds[k]]) for k in (0, 2, 1, 3)], names=["xmin", "xmax", "ymin", "ymax"])
        return pa.table({"geometry": pa.array([g.wkb], pa.binary()), "bbox": b, "class": pa.array(["house"]),
                         "subtype": pa.array([None], pa.string()), "height": pa.array([None], pa.float64()),
                         "num_floors": pa.array([None], pa.float64())})
    class Frag:
        def __init__(s, i): s.i = i
        def split_by_row_group(s, flt): return [s]
        def to_table(s, columns, filter): return table(s.i).select(columns)
    class DS:
        schema = table(0).schema
        def get_fragments(s, filter=None): return [Frag(0), Frag(1)]
    calls, seen = [], {}
    orig = core._prepare_query
    def prep(kind, bbox, release=None):
        seen["rel"] = release
        return DS(), None
    core._prepare_query = prep
    try:
        raw = fetch_buildings((77, 28, 78, 29), release="2026-01-01.0", progress=lambda d, t: calls.append((d, t)))
    finally:
        core._prepare_query = orig
    assert calls == [(1, 2), (2, 2)] and seen["rel"] == "2026-01-01.0"
    assert len(raw) == 2 and {"xmin", "xmax", "ymin", "ymax"} <= set(raw.columns) and "bbox" not in raw
    assert raw.xmin.iloc[0] == pytest.approx(77.3)

def test_registry_roundtrip_and_covering(tmp_path):
    import areas
    reg = areas.Registry(tmp_path)
    raw = fake_raw()
    big = (77.30, 28.55, 77.36, 28.60)
    from overture import clip_to_bbox
    m = reg.create(big, clip_to_bbox(raw, big), "2026-01-01.0", aid="big", name="big")
    assert m["buildings"] == len(reg.load_exposure("big")) > 100
    assert reg.find_covering((77.31, 28.56, 77.33, 28.58))["id"] == "big"
    assert reg.find_covering((77.20, 28.56, 77.33, 28.58)) is None          # sticks out
    assert reg.find_covering((77.31, 28.56, 77.33, 28.58), release="other") is None

def test_derived_area_equals_fresh_clip(tmp_path):
    """Smaller box cut from a cached area gives the same exposure, row by row, as building it from a fresh download."""
    import areas
    from overture import clip_to_bbox
    reg = areas.Registry(tmp_path)
    raw = fake_raw(800)
    big, small = (77.30, 28.55, 77.36, 28.60), (77.31, 28.56, 77.34, 28.59)
    reg.create(big, clip_to_bbox(raw, big), "r1", aid="big")
    d = reg.derive(small, "big", name="small")
    got = reg.load_exposure(d["id"])
    fresh = areas.exposure.make_exposure(clip_to_bbox(raw, small))      # what a fresh download of `small` would give
    assert len(got) == len(fresh)
    pd.testing.assert_frame_equal(pd.DataFrame(got.drop(columns="geometry")), pd.DataFrame(fresh.drop(columns="geometry")))
    assert all(a.equals_exact(b, 1e-12) for a, b in zip(got.geometry, fresh.geometry))

def test_exposure_rebuilt_when_method_version_changes(tmp_path, monkeypatch):
    import areas
    from overture import clip_to_bbox
    reg = areas.Registry(tmp_path)
    reg.create((77.30, 28.55, 77.36, 28.60), clip_to_bbox(fake_raw(), (77.30, 28.55, 77.36, 28.60)), "r", aid="a")
    monkeypatch.setattr(areas.exposure, "METHOD_VERSION", "999")
    reg.load_exposure("a")
    assert reg.meta("a")["exposure_version"] == "999"

def test_second_load_of_covered_area_makes_no_network_call(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app as appmod
    from overture import clip_to_bbox
    calls = {"fetch": 0}
    raw = fake_raw(500)
    def fake_fetch(bbox, release=None, progress=None):
        calls["fetch"] += 1
        if progress: progress(1, 2); progress(2, 2)
        return clip_to_bbox(raw, bbox)
    monkeypatch.setattr(appmod, "fetch_buildings", fake_fetch)
    monkeypatch.setattr(appmod, "latest_release", lambda: "2026-01-01.0")
    c = TestClient(appmod.create_app(tmp_path))
    def load(b, **kw):
        jid = c.post("/api/area", json=dict(w=b[0], s=b[1], e=b[2], n=b[3], **kw)).json()["job"]
        for _ in range(200):
            j = c.get(f"/api/jobs/{jid}").json()
            if j["state"] in ("done", "error"):
                return j
            time.sleep(0.05)
        raise AssertionError("job did not finish")
    big, small = (77.30, 28.55, 77.36, 28.60), (77.31, 28.56, 77.34, 28.59)
    j1 = load(big)
    assert j1["state"] == "done" and calls["fetch"] == 1 and j1["result"]["cached"] is False
    assert c.get("/api/meta").json()["empty"] is False
    t = time.time(); j2 = load(big)
    assert j2["state"] == "done" and calls["fetch"] == 1 and j2["result"]["cached"] and j2["result"]["buildings"] == j1["result"]["buildings"]
    j3 = load(small)
    assert j3["state"] == "done" and calls["fetch"] == 1 and j3["result"]["buildings"] < j1["result"]["buildings"]
    n_areas = len(c.get("/api/areas").json()["areas"])
    load(small)
    assert len(c.get("/api/areas").json()["areas"]) == n_areas              # no duplicate area for the same box
    # a newer release is offered, never downloaded silently
    monkeypatch.setattr(appmod, "latest_release", lambda: "2027-01-01.0")
    j4 = load(big)
    assert calls["fetch"] == 1 and "newer Overture release" in j4["result"]["update_note"]
    j5 = load(big, refresh=True)
    assert calls["fetch"] == 2
    # activate + download
    aid = c.get("/api/areas").json()["areas"][0]["id"]
    assert c.post(f"/api/areas/{aid}/activate").status_code == 200
    assert c.post("/api/areas/nope/activate").status_code == 404
    assert c.get("/api/area/download").status_code == 200
    # restart reopens the last used area
    c2 = TestClient(appmod.create_app(tmp_path))
    assert c2.get("/api/meta").json()["empty"] is False

def test_area_job_errors_are_reported(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import app as appmod
    monkeypatch.setattr(appmod, "fetch_buildings", lambda bbox, release=None, progress=None: gpd.GeoDataFrame(geometry=[], crs=4326))
    monkeypatch.setattr(appmod, "latest_release", lambda: "r")
    c = TestClient(appmod.create_app(tmp_path))
    assert c.post("/api/area", json=dict(w=1, s=1, e=1, n=2)).status_code == 400          # no size
    assert c.post("/api/area", json=dict(w=70, s=20, e=80, n=30)).status_code == 400      # too large
    jid = c.post("/api/area", json=dict(w=77.3, s=28.5, e=77.31, n=28.51)).json()["job"]
    for _ in range(100):
        j = c.get(f"/api/jobs/{jid}").json()
        if j["state"] in ("done", "error"): break
        time.sleep(0.05)
    assert j["state"] == "error" and "No buildings" in j["error"]
    assert c.get("/api/jobs/nope").status_code == 404

# ---------- 0.5 ----------
def test_jobs_progress_and_errors():
    from jobs import Jobs
    jobs, gate = Jobs(workers=1), __import__("threading").Event()
    def slow(job):
        job.update(0.4, "half"); gate.wait(2); return {"x": 1}
    j = jobs.submit("t", slow)
    for _ in range(100):
        if jobs.get(j.id).progress > 0.3: break
        time.sleep(0.01)
    assert jobs.get(j.id).public()["state"] == "running" and jobs.get(j.id).message == "half"
    gate.set()
    for _ in range(100):
        if jobs.get(j.id).state == "done": break
        time.sleep(0.01)
    p = jobs.get(j.id).public()
    assert p["state"] == "done" and p["result"] == {"x": 1} and p["progress"] == 1.0
    def boom(job): raise RuntimeError("nope")
    e = jobs.submit("t", boom)
    for _ in range(100):
        if jobs.get(e.id).state == "error": break
        time.sleep(0.01)
    assert jobs.get(e.id).public()["error"] == "nope"

def test_background_lane_does_not_block_interactive_jobs():
    """Monte Carlo ranges and tile cutting run in their own lanes, so e.g. the responder layer never queues behind them."""
    from jobs import Jobs
    import threading
    jobs, gate = Jobs(workers=1, lanes={"uncertainty": 1}), threading.Event()
    slow = jobs.submit("uncertainty", lambda job: gate.wait(5))
    quick = jobs.submit("responders", lambda job: {"ok": True})
    for _ in range(200):
        if jobs.get(quick.id).state == "done": break
        time.sleep(0.01)
    assert jobs.get(quick.id).state == "done" and jobs.get(slow.id).state == "running"
    gate.set()
