import pathlib, time
from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from simulate import Model
from exposure import make_exposure
from overture import fetch_buildings
from places import reverse_name

here = pathlib.Path(__file__).resolve().parent
root = here.parent if here.name == "backend" else here   # works with or without a backend/ folder
index = next(p for p in (root / "frontend" / "index.html", root / "index.html") if p.exists())

app = FastAPI(title="Quake damage map")
app.add_middleware(GZipMiddleware, minimum_size=1000, compresslevel=5)   # building GeoJSON shrinks ~10x
def load_default():
    """Prefer the fast parquet file; convert the old GeoJSON once if that is all there is."""
    pq, gj = root / "data" / "buildings.parquet", root / "data" / "buildings.geojson"
    if pq.exists() and (not gj.exists() or pq.stat().st_mtime >= gj.stat().st_mtime):
        return Model(pq)
    m = Model(gj)
    m.g.to_parquet(pq)
    return m

model = load_default()
latest = None            # parquet file of the most recently loaded area
MAX_DEG2 = 0.08          # about 30 km x 30 km

class Quake(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    depth_km: float = Field(10, ge=0, le=700)
    magnitude: float = Field(ge=3, le=9.5)

class Box(BaseModel):
    w: float = Field(ge=-180, le=180)
    s: float = Field(ge=-90, le=90)
    e: float = Field(ge=-180, le=180)
    n: float = Field(ge=-90, le=90)

@app.get("/")
def home():
    return FileResponse(index)

@app.get("/api/meta")
def meta():
    w, s, e, n = model.bounds
    return {"center": [(w + e) / 2, (s + n) / 2], "bounds": [w, s, e, n]}

@app.post("/api/simulate")
def simulate(q: Quake):
    return JSONResponse(model.run(q.lat, q.lon, q.depth_km, q.magnitude))   # already plain types, skip re-encoding

@app.get("/api/buildings")
def buildings(w: float, s: float, e: float, n: float):
    return Response(model.buildings_in(w, s, e, n), media_type="application/json")

@app.get("/api/building/{i}")
def building(i: int, run: int = 0):
    """Popup details for one building. run is the simulation the map shows; a mismatch means the map is stale."""
    if run and run != model.run_id:
        raise HTTPException(409, "These results are out of date. Run the simulation again.")
    b = model.building(i)
    if b is None:
        raise HTTPException(404, "Building not found.")
    return b

@app.get("/api/buildings3d")
def buildings3d(w: float, s: float, e: float, n: float, clat: float, clon: float):
    return Response(model.buildings_3d(w, s, e, n, clat, clon), media_type="application/json")

@app.post("/api/area")
def load_area(b: Box):
    """Download buildings for a drawn box, build the exposure table, and make it the active area."""
    global model, latest
    if b.e <= b.w or b.n <= b.s:
        raise HTTPException(400, "Draw a box with some size.")
    if (b.e - b.w) * (b.n - b.s) > MAX_DEG2:
        raise HTTPException(400, "That area is too large. Choose a box of up to about 30 km x 30 km.")
    try:
        raw = fetch_buildings((b.w, b.s, b.e, b.n))
    except ImportError:
        raise HTTPException(500, "The server needs the overturemaps package: pip install overturemaps")
    except Exception as ex:
        raise HTTPException(502, f"Download failed: {str(ex)[:150]}")
    if len(raw) == 0:
        raise HTTPException(404, "No buildings found in that box.")
    g = make_exposure(raw)
    folder = root / "data" / "areas"
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / f"area-{int(time.time())}.parquet"
    g.to_parquet(f)
    model, latest = Model(g), f
    w, s, e, n = model.bounds
    return {"buildings": len(g), "people": int(g["occupants"].sum()),
            "bounds": [w, s, e, n], "center": [(w + e) / 2, (s + n) / 2]}

@app.get("/api/name")
def place_name(lat: float, lon: float):
    return {"name": reverse_name(lat, lon)}

@app.get("/api/area/download")
def download_area():
    if latest is None:
        raise HTTPException(404, "No area has been loaded yet.")
    return FileResponse(latest, filename=latest.name, media_type="application/octet-stream")
