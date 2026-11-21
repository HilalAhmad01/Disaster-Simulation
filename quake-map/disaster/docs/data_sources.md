# Data sources, licences and what each is used for

| Data | Used for | Licence / terms | How obtained |
|---|---|---|---|
| Overture Maps buildings and places (release recorded per area) | Footprints, facility points | CDLA-Permissive-2.0 for buildings (includes OSM, ODbL, and other sources: follow the Overture terms) | `overture.py`, public S3 |
| OpenStreetMap (Overpass, OSMnx, Nominatim, tiles) | Facilities, roads, place names, basemap | ODbL; tile and Nominatim usage policies (light use) | live queries |
| Google Open Buildings 2.5D Temporal v1 (2021-2023) | Building heights -> storeys | CC BY 4.0 (attribution required) | `tools/get_google_heights.py`, public GCS bucket |
| GHSL GHS-POP R2023A (epoch 2025, 3 arc-second) | Population by 1 km cell | CC BY 4.0 (JRC); E2025 is a modelled estimate | `tools/get_ghs_pop.py`, public AWS bucket |
| USGS Global Vs30 Mosaic | Soil stiffness | CC0 | manual download (ScienceBase blocks scripts): `tools/get_vs30.py` |
| USGS ShakeMaps (us20002926, us6000jllz) | Validation shaking | public domain | `tools/validate_events.py` |
| Copernicus EMS EMSR125, EMSR648 grading | Validation damage | free use with attribution (Copernicus EMS) | `tools/validate_events.py` |
| USGS ps2ff Rjb ratio table (v1.5.9) | Unknown-strike distance | public domain (CC0) | copied into `priors/ps2ff_rjb_wc94_ratios.csv` |
| OpenQuake hazardlib (tests only) | Equation verification | AGPL-3.0; **not a runtime dependency**, no code copied: coefficient values were read out of its tables and verified | separate environment |
| Boore & Atkinson 2008, Boore et al. 2014, Sharma et al. 2009, Worden et al. 2012, Wells & Coppersmith 1994, Holland 1980 | Equations | published methods | implemented in `gmpe.py`, `gmm.py`, `rupture.py`, `hazards/cyclone.py` |
| Selvan & Sinha (2024), arXiv:2410.05918 | Ground-motion model weights | CC BY (arXiv) | read |
| Census of India 2011 HL-03 | Construction-type prior | Government of India data | **not looked up**: `priors/census_materials.csv` holds an ASSUMED default |
| Hazus 6.1 manual (casualty rates, occupancy shares), GEM vulnerability, JRC depth-damage (Huizinga 2017), Nepal 2015 household survey | Calibration and verification | various; GEM models are CC BY-NC-SA (non-commercial; decision D5 on intended use is open) | **not obtained**; the corresponding values in `priors/` are marked UNVERIFIED / placeholder |

Everything under `data/` is downloaded or generated locally and is not in git. Each download script can rebuild it.
