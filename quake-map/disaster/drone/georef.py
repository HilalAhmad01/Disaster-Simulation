"""WGS84 <-> a local east-north-up frame in metres (origin at the area centre, ground at z = 0). Server maths is float64."""
import math
import numpy as np
from pyproj import Transformer


def utm_epsg(lon: float, lat: float) -> int:
    zone = int((lon + 180.0) // 6.0) + 1
    return (32600 if lat >= 0 else 32700) + min(max(zone, 1), 60)


class AreaFrame:
    def __init__(self, lon0: float, lat0: float):
        self.lon0, self.lat0 = float(lon0), float(lat0)
        self.epsg = utm_epsg(lon0, lat0)
        self._fwd = Transformer.from_crs(4326, self.epsg, always_xy=True)
        self._inv = Transformer.from_crs(self.epsg, 4326, always_xy=True)
        self.ox, self.oy = self._fwd.transform(lon0, lat0)

    # ---- points (scalars or arrays)
    def to_local(self, lon, lat):
        x, y = self._fwd.transform(np.asarray(lon, float), np.asarray(lat, float))
        return x - self.ox, y - self.oy

    def to_lonlat(self, x, y):
        return self._inv.transform(np.asarray(x, float) + self.ox, np.asarray(y, float) + self.oy)

    def utm_to_local(self, x, y):
        return np.asarray(x) - self.ox, np.asarray(y) - self.oy

    def polygon_to_local(self, coords_lonlat):
        a = np.asarray(coords_lonlat, float)
        x, y = self.to_local(a[:, 0], a[:, 1])
        return list(zip(x.tolist(), y.tolist()))

    def polygon_to_lonlat(self, pts):
        a = np.asarray(pts, float)
        lon, lat = self.to_lonlat(a[:, 0], a[:, 1])
        return [[float(u), float(v)] for u, v in zip(lon, lat)]

    # ---- chunks: a deterministic grid in the local frame
    @staticmethod
    def chunk_id(x: float, y: float, size: float) -> tuple[int, int]:
        return int(math.floor(x / size)), int(math.floor(y / size))

    @staticmethod
    def chunk_origin(i: int, j: int, size: float) -> tuple[float, float]:
        return i * size, j * size


def heading_deg(yaw_rad: float) -> float:
    """Worker yaw (0 = east, counter-clockwise, radians) -> compass heading in degrees (0 = north, clockwise)."""
    return (90.0 - math.degrees(yaw_rad)) % 360.0


def yaw_from_heading(deg: float) -> float:
    return math.radians(90.0 - deg)
