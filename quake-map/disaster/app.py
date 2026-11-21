import os, pathlib, time, threading
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from simulate import Model
from overture import fetch_buildings, latest_release
from places import reverse_name
from areas import Registry
from jobs import Jobs
import exposure, responder_layer, report, confidence, hazards, re, tiles
import base64

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

class CycloneIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    heading_deg: float = Field(0, ge=0, lt=360)          # direction of travel, degrees clockwise from north
    speed_ms: float = Field(5, ge=0, le=30)              # translation speed
    dp_hpa: float = Field(60, ge=5, le=150)              # central pressure deficit
    rmax_km: float = Field(40, ge=5, le=200)             # radius of maximum winds
    track_km: float = Field(400, ge=50, le=1500)         # track length each side of the point
    time_of_day: str = Field("night", pattern="^(night|day|commute)$")

class FloodIn(BaseModel):
    raster: str = Field(min_length=1, max_length=80)     # name of an uploaded depth raster
    scale: float = Field(1.0, gt=0, le=10)               # depth multiplier (e.g. climate or what-if factor)
    time_of_day: str = Field("night", pattern="^(night|day|commute)$")

class Teams(BaseModel):
    fire_station: int = Field(0, ge=0, le=50)
    police: int = Field(0, ge=0, le=50)
    ambulance: int = Field(0, ge=0, le=50)       # drawn from ambulance stations and hospitals

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
        self.mc, self.mc_jobs = {}, {}              # run number -> uncertainty report / job id
        self.layer = None                           # responder layer for the active model (facilities, roads)
        self.report_img, self.last_dispatch = {}, {}   # run -> browser map image (bytes) / last dispatch list
        self.tile_state = {}                        # area id -> {"state": building|ready|error|unavailable, "version", "error"}
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
            self.model, self.active_id, self.layer = m, aid, None
        self.registry.touch(aid)
        self.ensure_tiles(aid, m)
        return m

    def ensure_tiles(self, aid, model):
        """Make sure the area's static vector tiles exist and are current; builds them in the background otherwise."""
        if tiles.tippecanoe_path() is None:
            self.tile_state[aid] = {"state": "unavailable", "error": "tippecanoe is not installed"}
            return
        path, ver = tiles.tile_path(self.data, aid), tiles.version_of(model.g)
        m = tiles.meta(path)
        if m and m["version"] == ver:
            self.tile_state[aid] = {"state": "ready", "version": ver}
            return
        if self.tile_state.get(aid, {}).get("state") == "building":
            return
        self.tile_state[aid] = {"state": "building", "version": ver}
        def work(job):
            try:
                job.update(0.1, "Cutting vector tiles…")
                tiles.build(model.g, path)
                self.tile_state[aid] = {"state": "ready", "version": ver}
            except Exception as ex:
                self.tile_state[aid] = {"state": "error", "error": str(ex)[:200]}
                raise
            return {"area": aid}
        self.jobs.submit("tiles", work)

    def start_uncertainty(self, model, run_id, deterministic):
        """Monte Carlo for the run just made, as a background job on a snapshot of that run."""
        import uncertainty
        snap = model.mc_snapshot()
        def work(job):
            res = uncertainty.run_mc(model, n=200, seed=run_id, snapshot=snap, progress=lambda f: job.update(f, f"Computing uncertainty… {int(f * 100)}%"))
            rep = uncertainty.report(snap, res, deterministic)
            self.mc[run_id] = rep
            for old in sorted(self.mc)[:-5]:
                self.mc.pop(old, None)
            if model.run_id == run_id:              # popups show the ranges only while this run is the one on the map
                model.mc = {"run": run_id, "p_extensive_mean": res["p_extensive_mean"], "p_extensive_p90": res["p_extensive_p90"],
                            "p_collapse_mean": res["p_collapse_mean"]}
            return {"run": run_id}
        job = self.jobs.submit("uncertainty", work)
        self.mc_jobs[run_id] = job.id
        return job.id

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
        out = m.run(q.lat, q.lon, q.depth_km, q.magnitude, time_of_day=q.time_of_day,
                    strike=q.strike, dip=q.dip, rake=q.rake)
        if q.uncertainty:
            out["uncertainty_job"] = st.start_uncertainty(m, out["run"], {k: out["summary"][k] for k in ("people_at_risk", "expected_collapses", "heavy")})
        return JSONResponse(out)

    @app.get("/api/uncertainty/{run}")
    def uncertainty_result(run: int):
        """Ranges for a run: the report when finished, otherwise the job's progress."""
        if run in st.mc:
            return {"state": "done", **st.mc[run]}
        jid = st.mc_jobs.get(run)
        j = st.jobs.get(jid) if jid else None
        if j is None:
            raise HTTPException(404, "No uncertainty run for that simulation.")
        return {"state": j.state, "progress": j.progress, "message": j.message, **({"error": j.error} if j.state == "error" else {})}           # already plain types, skip re-encoding

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

    # ---- responder layer (Phase 5) ----
    def need_layer():
        m = st.need()
        if st.layer is None or st.layer.model is not m:
            raise HTTPException(409, "Prepare the responder layer first (hospitals and roads).")
        return st.layer

    @app.post("/api/responders/prepare")
    def responders_prepare():
        """Find facilities and download the road network for the active area (background job; first run needs internet)."""
        m = st.need()
        aid = st.active_id or "area"
        bbox = tuple(float(x) for x in m.bounds)
        def work(job):
            job.update(0.05, "Finding hospitals, fire stations and shelters…")
            layer = responder_layer.build_for_area(st.data, aid, m, bbox, log=lambda msg: job.update(None, str(msg)))
            if st.model is m:
                st.layer = layer
            kinds = layer.fac.kind.value_counts().to_dict()
            return {"facilities": kinds, "road_nodes": int(layer.net.n_nodes), "road_segments": int(layer.net.n_roads),
                    "notes": layer.fac.attrs.get("notes", [])}
        return {"job": st.jobs.submit("responders", work).id}

    @app.get("/api/facilities")
    def facilities():
        return need_layer().facilities_geojson()

    @app.post("/api/access")
    def access():
        """Blockage and hospital access for the simulation on the map (background job)."""
        layer = need_layer()
        if layer.model.last is None:
            raise HTTPException(409, "Run a simulation first.")
        run = layer.model.run_id
        def work(job):
            job.update(0.1, "Estimating which streets debris may block…")
            res = layer.compute_access()
            if layer.model.run_id != run:
                raise ValueError("The simulation changed while this was running. Run access again.")
            return {"run": run, "summary": res["summary"]}
        return {"job": st.jobs.submit("access", work).id, "run": run}

    @app.get("/api/access/{run}")
    def access_result(run: int):
        layer = need_layer()
        a = layer.access.get(run)
        if a is None:
            raise HTTPException(404, "No access result for that simulation.")
        return {"run": run, "summary": a["summary"], "cells": a["cells"]}

    @app.get("/api/route")
    def route(rank: int, source: str = "nearest:fire_station"):
        layer = need_layer()
        try:
            return layer.route(source if source.startswith("nearest:") else int(source), rank)
        except (ValueError, KeyError) as ex:
            raise HTTPException(409 if "first" in str(ex) else 404, str(ex).strip("'\""))

    @app.post("/api/dispatch")
    def dispatch(t: Teams):
        layer = need_layer()
        try:
            res = layer.dispatch({k: v for k, v in t.model_dump().items() if v})
            st.last_dispatch[layer.model.run_id] = res
            return {"assignments": res}
        except ValueError as ex:
            raise HTTPException(409, str(ex))

    # ---- static vector tiles (Phase 6.1) ----
    @app.get("/api/tiles/status")
    def tiles_status():
        aid = st.active_id
        s = st.tile_state.get(aid, {"state": "unavailable"}) if aid else {"state": "unavailable"}
        out = {"state": s["state"], "area": aid}
        if s["state"] == "ready":
            out.update(url=f"/tiles/{aid}/{{z}}/{{x}}/{{y}}.pbf?v={s['version']}", minzoom=tiles.MINZ, maxzoom=tiles.MAXZ, layer=tiles.LAYER,
                       buildings=len(st.model.g))
        elif "error" in s:
            out["error"] = s["error"]
        return out

    @app.get("/tiles/{aid}/{z}/{x}/{y}.pbf")
    def tile(aid: str, z: int, x: int, y: int):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", aid):
            raise HTTPException(404, "Unknown area.")
        p = tiles.tile_path(st.data, aid)
        if not p.exists():
            raise HTTPException(404, "No tiles for this area.")
        data = tiles.read_tile(p, z, x, y)
        if data is None:
            return Response(status_code=204)
        return Response(data, media_type="application/x-protobuf", headers={"Content-Encoding": "gzip", "Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/api/runattrs/{run}")
    def run_attrs(run: int):
        """Damage class and collapse chance of every building for the run on the map, as digit strings indexed by feature id."""
        m = st.need()
        if run != m.run_id or m.last is None:
            raise HTTPException(409, "These results are out of date. Run the simulation again.")
        return tiles.run_attrs(m.last)

    # ---- other hazards (Phase 7) ----
    HZ = hazards.registry()
    MAX_RASTER = 300_000_000

    def flood_dir():
        d = st.data / "hazards" / "flood"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @app.get("/api/hazards")
    def hazard_list():
        return {"hazards": [h.describe() for h in HZ.values()], "flood_rasters": sorted(p.stem for p in flood_dir().glob("*.tif"))}

    @app.post("/api/hazard/cyclone")
    def run_cyclone(q: CycloneIn):
        out = st.need().run_hazard(HZ["cyclone"], q.model_dump())
        return JSONResponse(out)

    @app.post("/api/hazard/flood")
    def run_flood(q: FloodIn):
        f = flood_dir() / f"{q.raster}.tif"
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", q.raster) or not f.exists():
            raise HTTPException(404, "Upload a flood depth raster first.")
        try:
            out = st.need().run_hazard(HZ["flood"], {"raster": str(f), "scale": q.scale, "time_of_day": q.time_of_day})
        except ValueError as ex:
            raise HTTPException(400, str(ex))
        return JSONResponse(out)

    @app.post("/api/flood/upload")
    async def flood_upload(request: Request, name: str):
        """Body = a GeoTIFF of water depth in metres (any CRS, one band). Saved as data/hazards/flood/<name>.tif."""
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,60}", name):
            raise HTTPException(400, "Use letters, digits, dot, dash or underscore in the name.")
        body = await request.body()
        if not body or len(body) > MAX_RASTER:
            raise HTTPException(400, "Send a GeoTIFF of at most 300 MB.")
        import rasterio, tempfile
        with tempfile.NamedTemporaryFile(suffix=".tif", dir=flood_dir(), delete=False) as tmp:
            tmp.write(body)
        try:
            with rasterio.open(tmp.name) as ds:
                if ds.count < 1 or ds.crs is None:
                    raise ValueError("The file needs a coordinate system and at least one band.")
            pathlib.Path(tmp.name).replace(flood_dir() / f"{name}.tif")
        except Exception as ex:
            pathlib.Path(tmp.name).unlink(missing_ok=True)
            raise HTTPException(400, f"Not a readable GeoTIFF: {str(ex)[:100]}")
        return {"raster": name}

    # ---- report export (Phase 6.3) ----
    class Image(BaseModel):
        png: str = Field(max_length=12_000_000)         # base64 of the browser's map image

    @app.post("/api/report/{run}/image")
    def report_image(run: int, img: Image):
        m = st.need()
        if run != m.run_id:
            raise HTTPException(409, "These results are out of date. Run the simulation again.")
        try:
            raw = base64.b64decode(img.png.split(",")[-1], validate=True)
        except Exception:
            raise HTTPException(400, "The image could not be read.")
        if not raw.startswith(b"\x89PNG"):
            raise HTTPException(400, "Send a PNG image.")
        st.report_img[run] = raw
        return {"ok": True}

    def report_ctx(run):
        m = st.need()
        if run != m.run_id or m.last is None:
            raise HTTPException(409, "These results are out of date. Run the simulation again.")
        area = st.registry.meta(st.active_id) if st.active_id else {"name": "loaded area"}
        acc = st.layer.access.get(run) if st.layer is not None and st.layer.model is m else None
        return report.build_context(m, area, st.mc.get(run), acc["summary"] if acc else None, st.last_dispatch.get(run), st.report_img.get(run))

    @app.get("/api/report/{run}.pdf")
    def report_pdf(run: int):
        return Response(report.to_pdf(report_ctx(run)), media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="quake-report-{run}.pdf"'})

    @app.get("/api/report/{run}.csv")
    def report_csv(run: int):
        return Response(report.to_csv(report_ctx(run)), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="quake-report-{run}.csv"'})

    @app.get("/api/confidence")
    def confidence_items():
        """Status of each input (placeholder, estimated, measured) for the model-confidence panel."""
        return {"items": confidence.build(st.model, st.registry.meta(st.active_id) if st.active_id else None, st.data)}

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
