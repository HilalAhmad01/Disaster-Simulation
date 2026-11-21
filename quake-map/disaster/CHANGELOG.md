# Changelog

Every change to a model number must be explained here (see MASTER_PLAN.md section 4).

## Unreleased (master-plan implementation)

### Phase 0: foundations
- git repository created; first commit is the v1.x baseline after performance fixes 1-4.
- Server starts without data (`/api/meta` returns `empty: true`; data endpoints return 409 "Load an area first.").
  `QUAKE_DATA_DIR` and `DEFAULT_CENTER` environment variables added. `app.create_app(data_dir)` factory.
- Area registry (`areas.py`): `data/areas/<id>/{raw,exposure}.parquet + meta.json`. Covered boxes are cut from saved raw
  footprints with Overture's own bbox rule; a newer Overture release is offered, never fetched silently.
- Background jobs (`jobs.py`): `POST /api/area` returns a job id; the page polls and shows "Downloading n/N parts".
- Baseline harness (`tools/baseline.py`) with 3 fixed scenarios on a 6 x 5 km Noida box; deterministic synthetic fixture.
- Decision D2: distant 3D buildings use `shapely.set_precision` again (identical to v1.0, no visual change).
- Soil raster is looked up in `data/rasters/` first. The USGS ScienceBase host blocks scripted downloads;
  `tools/get_vs30.py` documents the manual step. Without the file the model uses flat 250 m/s and says so.

**Reference numbers (v1.x model, flat soil, Overture 2026-11-18.0, baseline box, 32,400 buildings)** are stored in
`tests/baselines/*.json` in the first commit that contains them; later phases must explain every change from them.
