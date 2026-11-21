import os, pathlib, time, threading
from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from simulate import Model
from overture import fetch_buildings, latest_release
from places import reverse_name
from areas import Registry
from jobs import Jobs
import exposure

here = pathlib.Path(__file__).resolve().parent
root = here.parent if here.name == "backend" else here   # works with or without a backend/ folder
index = next(p for p in (root / "frontend" / "index.html", root / "index.html") if p.exists())
MAX_DEG2 = 0.08          # about 30 km x 30 km
NEEDS_AREA = "Load an area first."

class Quake(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    depth_km: float = Field(10, ge=0, le=700)
    magnitude: float = Field(ge=3, le=9.5)
    time_of_day: str = Field("night", pattern="^(night|day|commute)$")
    strike: float | None = Field(None, ge=0, lt=360)       # fault direction, degrees clockwise from north
    dip: float | None = Field(None, gt=0, le=90)
    rake: float | None = Field(None, ge=-180, le=180)
    uncertainty: bool = True

class Box(BaseModel):
    w: float = Field(ge=-180, le=180)
    s: float = Field(ge=-90, le=90)
    e: float = Field(ge=-180, le=180)
    n: float = Field(ge=-90, le=90)
    refresh: bool = False        # True: ignore the cache and download the latest Overture release

class State:
    """Everything the server holds in memory: the active model, the area registry and the job list."""
    def __init__(self, data_dir):
        self.data = pathlib.Path(data_dir)
        self.data.mkdir(parents=True, exist_ok=True)
        self.registry, self.jobs = Registry(self.data), Jobs(workers=2)
        self.model, self.active_id, self.legacy_file = None, None, None
        self.lock = threading.Lock()
        self.load_default()

    def load_default(self):
        """Reopen the most recently used area. Falls back to an old data/buildings.parquet or .geojson; with nothing
        at all the server still starts (model stays None, the page asks you to load an area)."""
        for m in self.registry.list():
            try:
                self.activate(m["id"])
                return
            except Exception as ex:
                print(f"Could not reopen area {m['id']}: {ex}")
        pq, gj = self.data / "buildings.parquet", self.data / "buildings.geojson"
        if pq.exists() and (not gj.exists() or pq.stat().st_mtime >= gj.stat().st_mtime):
            self.model, self.legacy_file = Model(pq), pq
        elif gj.exists():
            self.model, self.legacy_file = Model(gj), pq
            self.model.g.to_parquet(pq)

    def activate(self, aid):
        m = Model(self.registry.load_exposure(aid), data_dir=self.data)
        with self.lock:
            self.model, self.active_id = m, aid
        self.registry.touch(aid)
        return m

    def need(self):
        if self.model is None:
            raise HTTPException(409, NEEDS_AREA)
        return self.model

def default_center():
    try:
        lon, lat = (float(x) for x in os.environ.get("DEFAULT_CENTER", "77.39,28.57").split(","))
        return [lon, lat]
    except ValueError:
        return [77.39, 28.57]

def _summary(model, aid, extra=None):
    w, s, e, n = model.bounds
    d = {"buildings": int(len(model.g)), "people": int(model.g["occupants"].sum()), "area_id": aid,
         "bounds": [w, s, e, n], "center": [(w + e) / 2, (s + n) / 2]}
    d.update(extra or {})
    return d

def _load_area_job(st, bbox, refresh, job):
    """Worker: reuse a cached area when one covers the box (no network), otherwise download. Then switch models."""
    reg = st.registry
    bbox = tuple(float(x) for x in bbox)
    job.update(0.02, "Checking saved areas")
    hit = None if refresh else reg.find_covering(bbox)
    note, newer = None, None
    if hit is not None:
        exact = all(abs(a - b) < 1e-9 for a, b in zip(hit["bbox"], bbox))
        if exact:
            aid = hit["id"]
        else:
            same = next((m for m in reg.list() if all(abs(a - b) < 1e-9 for a, b in zip(m["bbox"], bbox))
                         and m.get("release") == hit.get("release")), None)
            job.update(0.3, "Cutting the area from saved data")
            aid = same["id"] if same else reg.derive(bbox, hit["id"], name=f"area-{hit['id']}")["id"]
        newer = latest_release()
        if newer and hit.get("release") and newer > hit["release"]:
            note = f"A newer Overture release ({newer}) is available. Use 'Update to latest' to re-download."
        else:
            newer = None
        job.update(0.8, "Preparing the model")
        cached = True
    else:
        job.update(0.03, "Looking up the latest Overture release")
        release = latest_release()
        def prog(done, total):
            job.update(0.05 + 0.65 * done / max(total, 1), f"Downloading {done}/{total} parts…")
        raw = fetch_buildings(bbox, release=release, progress=prog)
        if len(raw) == 0:
            raise ValueError("No buildings found in that box.")
        job.update(0.72, f"Building the exposure table ({len(raw):,} buildings)")
        aid = reg.create(bbox, raw, release, name="area")["id"]
        cached = False
    job.update(0.9, "Loading the model")
    st.activate(aid)
    return _summary(st.model, aid, {"cached": cached, "update_note": note, "newer_release": newer})

def create_app(data_dir=None):
    data_dir = data_dir or os.environ.get("QUAKE_DATA_DIR") or (root / "data")
    st = State(data_dir)
    app = FastAPI(title="Quake damage map")
    app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)   # building GeoJSON shrinks ~10x
    app.state.st = st

    @app.get("/")
    def home():
        return FileResponse(index)

    @app.get("/api/meta")
    def meta():
        m = st.model
        if m is None:
            return {"center": default_center(), "bounds": None, "empty": True, "area_id": None}
        w, s, e, n = m.bounds
        return {"center": [(w + e) / 2, (s + n) / 2], "bounds": [w, s, e, n], "empty": False, "area_id": st.active_id}

    @app.post("/api/simulate")
    def simulate(q: Quake):
        m = st.need()
        out = m.run(q.lat, q.lon, q.depth_km, q.magnitude)
        return JSONResponse(out)           # already plain types, skip re-encoding

    @app.get("/api/buildings")
    def buildings(w: float, s: float, e: float, n: float):
        return Response(st.need().buildings_in(w, s, e, n), media_type="application/json")

    @app.get("/api/building/{i}")
    def building(i: int, run: int = 0):
        """Popup details for one building. run is the simulation the map shows; a mismatch means the map is stale."""
        m = st.need()
        if run and run != m.run_id:
            raise HTTPException(409, "These results are out of date. Run the simulation again.")
        b = m.building(i)
        if b is None:
            raise HTTPException(404, "Building not found.")
        return b

    @app.get("/api/buildings3d")
    def buildings3d(w: float, s: float, e: float, n: float, clat: float, clon: float):
        return Response(st.need().buildings_3d(w, s, e, n, clat, clon), media_type="application/json")

    @app.post("/api/area")
    def load_area(b: Box):
        """Start loading a drawn box in the background. Returns {"job": id}; poll GET /api/jobs/{id}."""
        if b.e <= b.w or b.n <= b.s:
            raise HTTPException(400, "Draw a box with some size.")
        if (b.e - b.w) * (b.n - b.s) > MAX_DEG2:
            raise HTTPException(400, "That area is too large. Choose a box of up to about 30 km x 30 km.")
        try:
            import overturemaps   # noqa: F401  (fail early with a clear message)
        except ImportError:
            raise HTTPException(500, "The server needs the overturemaps package: pip install overturemaps")
        job = st.jobs.submit("area", lambda j: _load_area_job(st, (b.w, b.s, b.e, b.n), b.refresh, j))
        return {"job": job.id}

    @app.get("/api/jobs/{jid}")
    def job(jid: str):
        j = st.jobs.get(jid)
        if j is None:
            raise HTTPException(404, "Unknown job.")
        return j.public()

    @app.get("/api/areas")
    def areas():
        return {"active": st.active_id, "areas": st.registry.list()}

    @app.post("/api/areas/{aid}/activate")
    def activate(aid: str):
        if st.registry.meta(aid) is None:
            raise HTTPException(404, "Unknown area.")
        m = st.activate(aid)
        return _summary(m, aid)

    @app.get("/api/name")
    def place_name(lat: float, lon: float):
        return {"name": reverse_name(lat, lon)}

    @app.get("/api/area/download")
    def download_area():
        f = (st.registry.path(st.active_id) / "exposure.parquet") if st.active_id else st.legacy_file
        if f is None or not f.exists():
            raise HTTPException(404, "No area has been loaded yet.")
        return FileResponse(f, filename=f"{st.active_id or f.stem}.parquet", media_type="application/octet-stream")

    return app

app = create_app()
