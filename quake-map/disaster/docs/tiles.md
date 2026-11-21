# Static vector tiles for the 3D / 2D building layers (MASTER_PLAN 6.1)

## Design
* `tiles.py` cuts an area's buildings once into `data/tiles/<area>.pmtiles` with **tippecanoe** (built from source into `.tools/` by
  `tools/install_tippecanoe.sh`; no root needed). Tiles hold geometry, feature id (= row of the exposure table) and storeys only.
  Settings keep the picture the same as the GeoJSON path: extent 8192, no line simplification, no tiny-polygon reduction, no feature
  or size limits, zooms 13-16 (the page over-zooms beyond 16). Noida: 483,358 buildings, 34 MB, cut in about 7 s; built in the
  background whenever an area is activated and rebuilt only if footprints or storeys change.
* The server serves them at `/tiles/{area}/{z}/{x}/{y}.pbf` (immutable cache headers; a version in the URL busts the cache).
* **Per-run results do not live in the tiles.** `GET /api/runattrs/{run}` returns two digit strings indexed by feature id (damage class
  0-3, collapse chance 000-100). The page turns them into numeric arrays and puts them in the style expressions
  (`['at', ['id'], ['literal', array]]`), so a new run changes one paint property. This avoids `feature-state` (483k calls).
  String `slice` was tried first and was ~10x slower than `at` on a numeric array (4.3 s vs 0.4 s for a view), so arrays are used.
* The page uses tiles automatically when they are ready and falls back to the previous GeoJSON path otherwise (or with `?tiles=0`).

## Measured: same scenario, same camera, buildings only (`python tools/compare_tiles.py`)
Chrome with software rendering, M7.0 at 10 km. Pixel difference = share of drawn pixels that differ by more than 24/255 in any
channel, strict and after allowing a 1-pixel neighbourhood (anti-aliasing / sub-pixel edge shifts). Raw results:
`docs/tiles_comparison_*.json`.

| view | pixels differing (strict / 1-px tolerance) | data for the first view, GeoJSON → tiles |
|---|---|---|
| 2D z16 | 0.14% / 0.08% | 590 kB → 36 kB |
| 2D z15 wide | 1.3% / 0.8% | 1.9 MB → 77 kB |
| 3D z15.5 pitch 60 | 1.5% / 0.1% | 7.0 MB → 92 kB |
| 3D z14.2 pitch 50 (32k-building area) | 4.6% / 1.7% | 8.5 MB → 175 kB |

(Noida, 483k buildings. In the 3D z14.2 Noida view the GeoJSON path returned no buildings during the test, so that row is excluded.)
With "lower buildings by collapse chance" on (the per-run collapse array in the height expression) the results are the same (<= 1.7%
after tolerance). Visually the renderings are indistinguishable (see `docs/tiles_compare_3d.png`).

Time, Noida: applying a new run's attributes takes about 11 ms for the paint property plus about 0.3 s for the tiles to restyle;
a first 3D view settles in 1-2 s and a pan onto uncovered ground in about 0.8 s (GeoJSON: about 1.3 s and a 5-8 MB download each
time the view leaves the loaded box). Panning back over tiles already seen is served from the browser cache.

## Limits
* Tiles exist only after the one-off cut; until then (and if tippecanoe is missing) the GeoJSON path is used.
* The 483k-entry arrays are parsed by every map worker, so a pan onto new tiles costs more than with a constant colour (0.8 s vs 0.07 s).
* Tiles are not used for the per-view "far ring" simplification; every building is drawn at full detail.
