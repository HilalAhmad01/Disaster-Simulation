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

### Phase 1: building data
- **Storeys** come from Google Open Buildings 2.5D Temporal heights (median over 2021-2023 of the zonal median inside each
  footprint, `heights.py`), then `num_floors`/height labels, then the old heuristic. New column `levels_src`
  (label, label_height, google, guess, guess_unstable). Heights with spread > 6 m or < 2 m are not trusted.
  Noida (483,358 buildings): 66.7% google + 0.2% labelled = 66.9% measured (the plan's 80% target is NOT met; the 6 x 5 km
  baseline box reaches 81.8%). 30% of Noida's footprints read < 2 m in the height product (62% of footprints < 20 m2, 47% of
  20-50 m2) and fall back to the heuristic by design; only 2.7% fail on year-to-year spread. See docs/validation.md. Storey height is 2.8 m (was 3.0 for height labels).
- **Construction type is now a probability mix** (`p_URM, p_RC_LOW, p_RC_MID`, `priors.py`). Damage is the mix of the three
  curves. One-hot weights reproduce v1 exactly (tests/test_regression.py passed unchanged at that step).
  The default census shares are an ASSUMPTION (priors/census_materials.csv), not looked up from the census tables.
- **People**: `capacity` (v1's typical occupancy) is scaled so each 1 km cell matches GHS-POP R2023A (epoch 2025); night /
  day / commute totals (`occupants`, `occ_day`, `occ_commute`); `time_of_day` is a scenario input. Noida box: v1 put 9.1 M
  people in the box, GHS-POP has 4.9 M; the new night total is within 2.2% of GHS-POP.
- Area exposure rebuilds automatically when `exposure.METHOD_VERSION` changes or a raster is added.

### Phase 2: shaking
- BA08 verified against OpenQuake to machine precision. **Bug fixed:** v1 had the normal and reverse mechanism offsets
  swapped (not used by the app, which always used "unspecified").
- Finite-fault distance (`rupture.py`): Wells & Coppersmith (1994) rupture, exact Rjb; unknown strike uses the USGS ps2ff
  table (public domain, copied into priors/). Depth acts through Ztor: r_eff = sqrt(Rjb^2 + Ztor^2).
- Regional mix (`gmm.py`): BA08 + BSSA14 (+ Sharma 2009 near the source in the Himalayan region), ported and checked
  against OpenQuake (< 1%). Weights from Selvan & Sinha (2024) IGP/Himalaya LLH scores; Sharma's 0.2 is a judgement.
- Optional local Vs30 override raster.

**Baseline changes vs v1.x (6 x 5 km Noida box, 32,400 buildings, flat soil):**

| scenario | max PGA g | buildings moderate+ | expected collapses | people at risk |
|---|---|---|---|---|
| a M6.5, 15 km, centre | 0.194 -> 0.264 | 21,758 -> 32,063 | 931 -> 2,017 | 54,935 -> 115,209 |
| b M7.5, 10 km | 0.288 -> 0.497 | 21,804 -> 32,400 | 3,289 -> 9,280 | 149,963 -> 302,755 |
| c M7.5, 50 km north | 0.155 -> 0.207 | 0 -> 0 | 348 -> 784 | 24,950 -> 60,130 |

Why: (1) distance now enters through Ztor (M6.5 at 15 km depth: ~8.9 km instead of the 15 km hypocentral distance), so PGA
near the source is higher; (2) types are mixed, so some "masonry" buildings are now concrete and vice versa; (3) people use
GHS-POP (the box total fell from ~771k capacity to ~527k). These are a *calibration gap*, not a finding: the fragility
curves are still the v1 placeholders until Phase 3.
