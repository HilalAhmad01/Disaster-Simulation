from .base import Hazard
from .earthquake import EarthquakeHazard

def registry():
    from .flood import FloodHazard
    from .cyclone import CycloneHazard
    return {h.name: h for h in (EarthquakeHazard(), FloodHazard(), CycloneHazard())}
