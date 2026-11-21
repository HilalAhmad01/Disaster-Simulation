"""Tropical cyclone wind hazard (MASTER_PLAN 7.3): Holland (1980) parametric wind field along a straight track.

Gradient wind at distance r from the eye (Holland 1980):
    V_g = sqrt( (B/rho) * dp * (Rmax/r)^B * exp(-(Rmax/r)^B) + (r f / 2)^2 ) - r f / 2
with dp the central pressure deficit (Pa), Rmax the radius of maximum winds, B the profile parameter, rho = 1.15 kg/m3 and f the
Coriolis parameter. Surface wind = V_g x surface reduction; the forward motion adds 0.5 x speed x sin(bearing - heading)
(stronger on the right of the track in the northern hemisphere, weaker on the left); the 3-s gust at the building = wind x gust
factor x terrain factor. The factors are documented placeholders. The peak over the whole track is kept per building.
Damage: lognormal gust fragility per construction type (priors/wind_fragility.json, approximate placeholders, not HAZUS).
"""
import json
import numpy as np
from scipy.special import ndtr
from .base import Hazard

RHO = 1.15
OMEGA = 7.2921e-5
SURFACE_REDUCTION, GUST_FACTOR, TERRAIN_FACTOR = 0.8, 1.3, 0.9

def holland_wind(r_km, dp_hpa, rmax_km, b=1.5, lat=20.0):
    """Holland (1980) gradient wind speed (m/s) at r_km from the eye."""
    r = np.maximum(np.asarray(r_km, float), 1e-3) * 1000.0
    rm = rmax_km * 1000.0
    f = 2 * OMEGA * np.sin(np.radians(np.abs(lat)))
    x = (rm / r) ** b
    v = np.sqrt((b / RHO) * (dp_hpa * 100.0) * x * np.exp(-x) + (r * f / 2) ** 2) - r * f / 2
    return v

def track_points(lat, lon, heading_deg, half_km=400.0, step_km=5.0):
    """Points of a straight track through (lat, lon) with the given heading, half_km before and after."""
    s = np.arange(-half_km, half_km + 1e-9, step_km)
    h = np.radians(heading_deg)
    dlat = s * np.cos(h) / 111.19
    dlon = s * np.sin(h) / (111.19 * np.cos(np.radians(lat)))
    return lat + dlat, lon + dlon

def _gust_at(blat, blon, tl, tn, heading_deg, speed_ms, dp_hpa, rmax_km, b, surface):
    """Surface wind (m/s, before gust/terrain factors) at buildings (n,) for track points tl, tn broadcast as (n, k) or (1, k)."""
    from simulate import haversine
    p = np.pi / 180
    la, lo = blat[:, None], blon[:, None]
    r = haversine(tl, tn, la, lo)
    dl = (lo - tn) * p
    brg = np.degrees(np.arctan2(np.sin(dl) * np.cos(la * p), np.cos(tl * p) * np.sin(la * p) - np.sin(tl * p) * np.cos(la * p) * np.cos(dl)))
    v = holland_wind(r, dp_hpa, rmax_km, b, la) * surface + 0.5 * speed_ms * np.sin(np.radians(brg - heading_deg))
    return np.maximum(v, 0.0)

def peak_gust(blat, blon, track_lat, track_lon, heading_deg, speed_ms, dp_hpa, rmax_km, b=1.5, chunk=24, fast=True, near=None,
              surface=SURFACE_REDUCTION, gust=GUST_FACTOR, terrain=TERRAIN_FACTOR):
    """Peak 3-s gust (m/s) at each building over the track. On a straight track the peak occurs at the closest approach, or, for a
    building nearer the track than Rmax, where the eye is sqrt(Rmax^2 - d^2) < Rmax along the track from it. So fast=True evaluates
    only the track points within 1.5 Rmax (plus 2 points) either side of the nearest one (KD-tree); fast=False checks every point
    (chunked; the tests confirm the shortcut gives the same answer)."""
    blat, blon = np.asarray(blat, float), np.asarray(blon, float)
    if not fast:
        best = np.zeros(len(blat))
        for a in range(0, len(track_lat), chunk):
            best = np.maximum(best, _gust_at(blat, blon, track_lat[a:a + chunk][None, :], track_lon[a:a + chunk][None, :], heading_deg, speed_ms, dp_hpa, rmax_km, b, surface).max(axis=1))
        return best * gust * terrain
    from scipy.spatial import cKDTree
    from simulate import haversine
    if near is None:
        step = float(haversine(track_lat[0], track_lon[0], track_lat[1], track_lon[1]))
        near = int(np.ceil(1.5 * rmax_km / max(step, 1e-3))) + 2
    c0 = np.cos(np.radians(np.mean(blat)))
    idx0 = cKDTree(np.column_stack([track_lon * c0, track_lat])).query(np.column_stack([blon * c0, blat]))[1]
    best = np.zeros(len(blat))
    for k in range(-near, near + 1):
        j = np.clip(idx0 + k, 0, len(track_lat) - 1)
        v = _gust_at(blat, blon, track_lat[j][:, None], track_lon[j][:, None], heading_deg, speed_ms, dp_hpa, rmax_km, b, surface)[:, 0]
        best = np.maximum(best, v)
    return best * gust * terrain

class CycloneHazard(Hazard):
    name = "cyclone"
    cell_key = "gust_ms"

    def intensity(self, model, params):
        lat, lon = float(params["lat"]), float(params["lon"])
        h = float(params.get("heading_deg", 0.0))
        rmax = float(params.get("rmax_km", 40.0))
        tl, tn = track_points(lat, lon, h, float(params.get("track_km", 400.0)), step_km=max(2.0, rmax / 8.0))     # step small against Rmax
        g = peak_gust(model.lat, model.lon, tl, tn, h, float(params.get("speed_ms", 5.0)), float(params.get("dp_hpa", 60.0)),
                      float(params.get("rmax_km", 40.0)), float(params.get("b", 1.5)))
        return {"value": g, "track": (tl, tn)}

    def damage(self, model, intensity):
        import priors
        d = json.loads(priors.path("wind_fragility.json").read_text())
        beta, W, v = d["beta"], model.W, intensity["value"]
        lv = np.log(np.maximum(v, 1e-6))[:, None]
        P = np.zeros((len(v), 4))
        for j, t in enumerate(("URM", "RC_LOW", "RC_MID")):
            sel = W[:, j] > 0
            if sel.any():
                P[sel] += W[sel, j:j + 1] * ndtr((lv[sel] - np.log(np.asarray(d["gust_ms_medians"][t], float))) / beta)
        return P

    def table(self, model, intensity):
        return {"intensity": intensity["value"].round(1)}

    def summary(self, model, intensity, p, occ):
        v = intensity["value"]
        return {"peak": [["Peak gust at a building (m/s)", round(float(v.max()), 1)], ["Peak gust (km/h)", round(float(v.max()) * 3.6)]],
                "max_gust_ms": round(float(v.max()), 1),
                "hazard_note": "Wind fragility curves are approximate placeholders; casualties are not modelled for cyclones."}

    def origin(self, params):
        return float(params["lat"]), float(params["lon"])

    def extra_output(self, model, params, intensity):
        tl, tn = intensity["track"]
        return {"track": {"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[float(b), float(a)] for a, b in zip(tl[::4], tn[::4])]}, "properties": {}}}

    def describe(self):
        return {"name": self.name, "label": "Cyclone", "intensity": "Peak 3-second gust", "unit": "m/s",
                "inputs": ["lat", "lon", "heading_deg", "speed_ms", "dp_hpa", "rmax_km", "b", "time_of_day"],
                "note": "Holland (1980) wind field along a straight track through the chosen point; fragility curves are placeholders."}
