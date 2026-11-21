"""Area registry: every downloaded area lives in data/areas/<id>/ so the same place is never downloaded twice and the
raw footprints are kept, letting the exposure be rebuilt whenever the method improves.

  raw.parquet       footprints with Overture's bbox columns (xmin/xmax/ymin/ymax) + the few labels we use
  exposure.parquet  output of exposure.make_exposure(raw)
  meta.json         bbox, Overture release, creation time, building count, exposure-method version
"""
import json, pathlib, re, shutil, time
import geopandas as gpd
from overture import clip_to_bbox
import exposure

def _slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return s[:40] or "area"

def contains(outer, inner, tol=0.0):
    """True if bbox `outer` (w, s, e, n) contains bbox `inner`."""
    return outer[0] <= inner[0] + tol and outer[1] <= inner[1] + tol and outer[2] >= inner[2] - tol and outer[3] >= inner[3] - tol

class Registry:
    def __init__(self, data_dir):
        self.dir = pathlib.Path(data_dir) / "areas"

    def path(self, aid):
        return self.dir / aid

    def meta(self, aid):
        f = self.path(aid) / "meta.json"
        return json.loads(f.read_text()) if f.exists() else None

    def list(self):
        out = []
        if self.dir.exists():
            for d in sorted(self.dir.iterdir()):
                m = self.meta(d.name) if d.is_dir() else None
                if m:
                    out.append(m)
        return sorted(out, key=lambda m: m.get("last_used", 0), reverse=True)

    def find_covering(self, bbox, release=None):
        """Smallest cached area whose bbox contains `bbox` (and, if given, was made from `release`)."""
        best = None
        for m in self.list():
            if contains(m["bbox"], bbox) and (release is None or m.get("release") == release):
                a = (m["bbox"][2] - m["bbox"][0]) * (m["bbox"][3] - m["bbox"][1])
                if best is None or a < best[0]:
                    best = (a, m)
        return best[1] if best else None

    def touch(self, aid):
        m = self.meta(aid)
        if m:
            m["last_used"] = time.time()
            (self.path(aid) / "meta.json").write_text(json.dumps(m, indent=1))

    def create(self, bbox, raw, release, aid=None, name=None, parent=None):
        """Store raw footprints (already limited to bbox), build the exposure, write meta. Returns the meta dict."""
        aid = aid or f"{_slug(name or 'area')}-{int(time.time())}"
        d = self.path(aid)
        d.mkdir(parents=True, exist_ok=True)
        raw = raw.reset_index(drop=True)
        raw.to_parquet(d / "raw.parquet")
        return self._build(aid, list(map(float, bbox)), raw, release, name=name, extra={"parent": parent} if parent else None)

    def raster_inputs(self, aid):
        """(height_dir, pop_raster, signature). Rasters are looked up for the area, then for its parents (a derived
        area is cut from a parent that covers it, so the parent's rasters cover it too). signature changes when
        a raster is added/replaced, which makes the stored exposure stale."""
        import population
        root = self.dir.parent / "rasters"
        chain, seen = [], set()
        while aid and aid not in seen:
            seen.add(aid)
            chain.append(aid)
            aid = (self.meta(aid) or {}).get("parent")
        height_dir = pop = None
        for a in chain:
            hd = root / "google_heights" / a
            if height_dir is None and any(hd.glob("h*_*.tif")) if hd.exists() else False:
                height_dir = hd
            pp = population.raster_path(root / "ghs_pop" / a)
            if pop is None and pp:
                pop = pp
        sig = [[p.name, p.stat().st_size, int(p.stat().st_mtime)] for p in sorted(height_dir.glob("h*_*.tif"))] if height_dir else []
        sig += [[pop.name, pop.stat().st_size, int(pop.stat().st_mtime)]] if pop else []
        return height_dir, pop, sig

    def _build(self, aid, bbox, raw, release, name=None, extra=None):
        d = self.path(aid)
        old = self.meta(aid) or {}
        parent = (extra or {}).get("parent", old.get("parent"))
        if parent and not old.get("parent"):
            (d / "meta.json").write_text(json.dumps({**old, "id": aid, "parent": parent}))   # so raster lookup sees the parent
        hd, pop, sig = self.raster_inputs(aid)
        g = exposure.make_exposure(raw, height_dir=hd, pop_raster=pop, data_dir=self.dir.parent)
        g.to_parquet(d / "exposure.parquet")
        m = {"id": aid, "name": name or old.get("name") or aid, "bbox": bbox, "release": release,
             "created": old.get("created", time.time()), "last_used": time.time(),
             "buildings": int(len(g)), "people": int(g["occupants"].sum()),
             "exposure_version": exposure.METHOD_VERSION, "inputs": sig, "stats": g.attrs.get("stats", {})}
        if parent:
            m["parent"] = parent
        m.update(extra or {})
        (d / "meta.json").write_text(json.dumps(m, indent=1))
        return m

    def derive(self, bbox, parent_id, name=None):
        """New area for `bbox` clipped (with Overture's own rule) from a cached parent's raw footprints. No network."""
        pm = self.meta(parent_id)
        raw = clip_to_bbox(gpd.read_parquet(self.path(parent_id) / "raw.parquet"), bbox)
        return self.create(bbox, raw, pm["release"], name=name, parent=parent_id)

    def load_exposure(self, aid):
        """Exposure GeoDataFrame of an area; rebuilt from raw if the method version changed since it was stored."""
        d, m = self.path(aid), self.meta(aid)
        if m is None:
            raise KeyError(aid)
        stale = m.get("exposure_version") != exposure.METHOD_VERSION or m.get("inputs", []) != self.raster_inputs(aid)[2]
        if stale and (d / "raw.parquet").exists():
            self._build(aid, m["bbox"], gpd.read_parquet(d / "raw.parquet"), m.get("release"), name=m.get("name"))
        return gpd.read_parquet(d / "exposure.parquet")

    def delete(self, aid):
        shutil.rmtree(self.path(aid), ignore_errors=True)
