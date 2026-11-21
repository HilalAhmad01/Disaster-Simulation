# Data sources, licences and what each is used for

| Data | Used for | Licence / terms | How obtained |
|---|---|---|---|
| Overture Maps buildings and places (release recorded per area) | Footprints, facility points | CDLA-Permissive-2.0 for buildings (includes OSM, ODbL, and other sources: follow the Overture terms) | `overture.py`, public S3 |
| OpenStreetMap (Overpass, OSMnx, Nominatim, tiles) | Facilities, roads, place names, basemap | ODbL; tile and Nominatim usage policies (light use) | live queries |
| Google Open Buildings 2.5D Temporal v1 (2021-2023) | Building heights -> storeys | CC BY 4.0 (attribution required) | `tools/get_google_heights.py`, public GCS bucket |
| GHSL GHS-POP R2023A (epoch 2025, 3 arc-second) | Population by 1 km cell | CC BY 4.0 (JRC); E2025 is a modelled estimate | `tools/get_ghs_pop.py`, public AWS bucket |
| USGS global Vs30 (Heath et al. 2020 hybrid map, `global_vs30.grd`) | Soil stiffness | public domain | `tools/get_vs30.py` (curl from apps.usgs.gov; the newer mosaic on ScienceBase blocks scripts) |
| USGS ShakeMaps (us20002926, us6000jllz) | Validation shaking | public domain | `tools/validate_events.py` |
| Copernicus EMS EMSR125, EMSR648 grading | Validation damage | free use with attribution (Copernicus EMS) | `tools/validate_events.py` |
| USGS ps2ff Rjb ratio table (v1.5.9) | Unknown-strike distance | public domain (CC0) | copied into `priors/ps2ff_rjb_wc94_ratios.csv` |
| OpenQuake hazardlib (tests only) | Equation verification | AGPL-3.0; **not a runtime dependency**, no code copied: coefficient values were read out of its tables and verified | separate environment |
| Boore & Atkinson 2008, Boore et al. 2014, Sharma et al. 2009, Worden et al. 2012, Wells & Coppersmith 1994, Holland 1980 | Equations | published methods | implemented in `gmpe.py`, `gmm.py`, `rupture.py`, `hazards/cyclone.py` |
| Selvan & Sinha (2024), arXiv:2410.05918 | Ground-motion model weights | CC BY (arXiv) | read |
| Census of India 2011, urban census houses by wall and roof material (state tables) | Construction-type prior | Government of India data | read from MoHUA/NBO "State of Housing in India: A Statistical Compendium 2013", Appendix 17 and 22 (Internet Archive copy); `priors/census_materials.csv` |
| Hazus 6.1 Earthquake Model Technical Manual (FEMA 2024) | Casualty rates (tables 12-3 to 12-8), occupancy shares (table 12-2) | US government work | read from the Internet Archive copy (fema.gov answers scripts with 403); values typed into `priors/` with table references |
| USGS OFR 2009-1136 (Jaiswal, Wald & Hearne) | PAGER fatality parameters for India | public domain | pubs.usgs.gov PDF |
| JRC Global flood depth-damage functions (Huizinga, Moel & Szewczyk 2017, EUR 28552 EN) | Flood damage curve (Asia residential) | CC BY 4.0 | JRC publications repository, JRC105688 workbook |
| JRC / Copernicus EMS global river flood hazard maps v2.1 | Flood depth for return periods | open (see README on the JRC server) | `tools/get_jrc_flood.py` |
| Nepal 2015 Household Registration for Housing Reconstruction survey, building table (762,094 buildings; derived mirror of the Nepal Open Data Portal file) | Fragility fit and held-out test | non-commercial research and education, attribution Government of Nepal (NPC/CBS) and Kathmandu Living Labs | `tools/get_nepal_calibration.py` (open GitHub mirror); official source NSO catalog 69 (registration) |
| HRRP / MoFALD federal-structure ward boundaries, 31 earthquake districts | Ward polygons for the survey join | CC0 (HDX) | `tools/get_nepal_calibration.py` |
| GEM Global Vulnerability Model v2026.0.0, Nepal and India structural files | Comparison with our fragility curves | CC BY-NC-SA 4.0: **non-commercial only**, decision D5 open; kept in git-ignored `data/external/gem/`, nothing copied into the repository | `tools/get_nepal_calibration.py` |
| USGS DYFI and finite-fault rupture for Gorkha (us20002926) | Delhi intensity check | public domain | `tools/get_nepal_calibration.py` |
| Anbazhagan & Bajaj (2023) chapter, Springer | Regional model ranks by distance band | paywalled | **not read** (no access); Selvan & Sinha 2024 is the evidence used |
| Anelli, Mori & Vona (2020), Applied Sciences 10(4):1289 | Debris width and the 3.5 m free-width criterion | CC BY 4.0 | read (Internet Archive copy) |

Everything under `data/` is downloaded or generated locally and is not in git. Each download script can rebuild it.
