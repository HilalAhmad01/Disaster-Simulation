"""Write priors/regions.geojson: the 'himalaya' region used to pick the ground-motion weights (gmm.py).

The polygon is the Himalayan arc's centreline (approximate, hand-placed from the geography of the range) buffered by
1.5 degrees (~150 km) so it covers the Siwalik/foothill belt and the northern Indo-Gangetic edge. It is an
approximation for choosing model weights, not a tectonic boundary map.  python tools/build_regions.py
"""
import json, pathlib
from shapely.geometry import LineString, mapping
CENTRE = [(74.6, 35.2), (75.0, 34.0), (77.0, 32.0), (79.0, 30.5), (81.0, 29.5), (84.0, 28.2), (88.5, 27.5), (91.0, 27.3), (94.0, 28.2), (95.2, 29.5)]
poly = LineString(CENTRE).buffer(1.5, cap_style="round", join_style="round").simplify(0.02)
fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"region": "himalaya",
      "note": "Himalayan arc centreline (approximate) buffered 1.5 deg; used only to select ground-motion weights"},
      "geometry": mapping(poly)}]}
out = pathlib.Path(__file__).resolve().parent.parent / "priors" / "regions.geojson"
out.write_text(json.dumps(fc))
print("wrote", out, poly.bounds)
