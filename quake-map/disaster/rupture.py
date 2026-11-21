"""Finite-fault distance for ground-motion models (MASTER_PLAN 2.2).

BA08, BSSA14 and Sharma 2009 are written for the Joyner-Boore distance Rjb (distance to the surface projection of the
rupture), not the point distance v1.0 used. Two cases:

* strike (and optionally dip, rake) known: a rectangular rupture is built (Wells & Coppersmith 1994 all-slip-type
  regressions, hypocentre at the middle along strike and 60% down the width, clipped to the seismogenic layer) and Rjb
  is the exact distance from each site to its surface projection.
* strike unknown (the default in the UI): the USGS ps2ff table gives the mean Rjb for an epicentral distance and
  magnitude, averaged over strikes, dips and hypocentre positions.

Depth: these models have no depth term. So that deep events still shake less overhead, the distance handed to the
ground-motion model is r_eff = sqrt(Rjb^2 + Ztor^2) with Ztor the depth to the top of the rupture (what Rrup does for a
vertical fault). For big shallow ruptures Ztor = 0 and r_eff = Rjb. A documented modelling choice, see README.
Below the seismogenic layer (depth >= z_bottom) no rupture can be built; the point source at the hypocentre is used.
"""
import pathlib, re
import numpy as np, pandas as pd

Z_BOTTOM = 20.0                       # km, bottom of the seismogenic layer (setting)
HYPO_FRACTION = 0.6                   # hypocentre sits 60% of the way down the rupture width (convention)
DEFAULT_DIP = {"unspec": 60.0, "ss": 90.0, "rs": 30.0, "ns": 55.0}     # degrees, used when only the strike is given

def wc94(mag):
    """Wells & Coppersmith (1994), all slip types: subsurface rupture length L (km), down-dip width W (km), area A (km2).
    log10 L = -2.44 + 0.59 M;  log10 W = -1.01 + 0.32 M;  log10 A = -3.49 + 0.91 M."""
    m = np.asarray(mag, float)
    return 10 ** (-2.44 + 0.59 * m), 10 ** (-1.01 + 0.32 * m), 10 ** (-3.49 + 0.91 * m)

def mechanism(rake):
    """BA08 mechanism class from rake (degrees): ss, rs (reverse), ns (normal); 'unspec' if unknown."""
    if rake is None:
        return "unspec"
    r = (float(rake) + 180.0) % 360.0 - 180.0
    if abs(r) <= 30 or abs(r) >= 150:
        return "ss"
    return "rs" if r > 0 else "ns"

class Rupture:
    """Rectangular rupture. Local axes: u along strike, v horizontal across strike towards the down-dip side.
    Surface projection = rectangle u in [-L/2, L/2], v in [v0, v1] (a line when dip = 90), epicentre at u = v = 0."""
    def __init__(self, mag, depth_km, strike, dip, z_bottom=Z_BOTTOM):
        self.mag, self.strike, self.dip, self.depth = float(mag), float(strike) % 360.0, float(dip), float(depth_km)
        L, W, A = (float(x) for x in wc94(mag))
        sd, cd = np.sin(np.radians(dip)), np.cos(np.radians(dip))
        self.point = depth_km >= z_bottom - 1e-9
        if self.point:                                  # hypocentre in or below the bottom of the layer
            self.L = self.W = 0.0
            self.z_top, self.v0, self.v1 = float(depth_km), 0.0, 0.0
            return
        for _ in range(50):                             # W and z_top depend on each other; converges in a few steps
            z_top = max(0.0, depth_km - HYPO_FRACTION * W * sd)
            w_max = (z_bottom - z_top) / sd
            if W <= w_max + 1e-9:
                break
            W = w_max
        L = A / W if W < wc94(mag)[1] - 1e-12 else L    # keep the area when the width was clipped
        self.L, self.W = float(L), float(W)
        self.z_top = float(max(0.0, depth_km - HYPO_FRACTION * W * sd))
        w_h = (depth_km - self.z_top) / sd              # slant distance from the top edge down to the hypocentre
        self.v0 = -w_h * cd
        self.v1 = self.v0 + W * cd

    def polygon_xy(self):
        """Corners of the surface projection as (east, north) metres relative to the epicentre (for drawing)."""
        s = np.array([np.sin(np.radians(self.strike)), np.cos(np.radians(self.strike))])
        d = np.array([np.cos(np.radians(self.strike)), -np.sin(np.radians(self.strike))])     # strike + 90 degrees
        h = self.L * 500.0
        pts = [-h * s + self.v0 * 1000 * d, h * s + self.v0 * 1000 * d, h * s + self.v1 * 1000 * d, -h * s + self.v1 * 1000 * d]
        return np.array(pts)

    def rjb(self, dx_m, dy_m):
        """Exact Joyner-Boore distance (km) from sites at (dx, dy) metres east/north of the epicentre."""
        th = np.radians(self.strike)
        u = (dx_m * np.sin(th) + dy_m * np.cos(th)) / 1000.0
        v = (dx_m * np.cos(th) - dy_m * np.sin(th)) / 1000.0
        du = np.maximum(np.abs(u) - self.L / 2, 0.0)
        dv = np.maximum(np.maximum(self.v0 - v, v - self.v1), 0.0)
        return np.hypot(du, dv)

_TABLE = None
def _ps2ff():
    global _TABLE
    if _TABLE is None:
        from scipy.interpolate import RegularGridInterpolator
        f = pathlib.Path(__file__).resolve().parent / "priors" / "ps2ff_rjb_wc94_ratios.csv"
        df = pd.read_csv(f, comment="#")
        mags = np.array([float(re.findall(r"R(\d+\.?\d*)", c)[0]) for c in df.columns[1:]])
        _TABLE = RegularGridInterpolator((np.log(df.iloc[:, 0].to_numpy()), mags), df.iloc[:, 1:].to_numpy(), bounds_error=False, fill_value=None)
        _TABLE.lo = (np.log(df.iloc[0, 0]), mags[0])
        _TABLE.hi = (np.log(df.iloc[-1, 0]), mags[-1])
    return _TABLE

def ps2ff_rjb(repi_km, mag):
    """Mean Rjb (km) for an epicentral distance when the strike is unknown (USGS ps2ff, WC94 scaling): repi * ratio.
    The ratio is looked up at the distance clipped to the table's range (0.1-1000 km) and the magnitude clipped to 4-9."""
    t = _ps2ff()
    repi = np.asarray(repi_km, float)
    r = np.clip(repi, 0.1, 1000.0)
    m = np.clip(float(mag), t.lo[1], t.hi[1])
    return repi * t((np.log(r), np.full(r.shape, m)))

def distances(dx_m, dy_m, mag, depth_km, strike=None, dip=None, rake=None, z_bottom=Z_BOTTOM):
    """Distances for ground-motion models. dx_m, dy_m: site offsets (metres east, north) from the epicentre.
    Returns (r_eff_km, rjb_km, info dict) with r_eff = sqrt(Rjb^2 + Ztor^2), and info holding the mechanism, the
    rupture (or None), Ztor and which method was used."""
    mech = mechanism(rake)
    repi = np.hypot(dx_m, dy_m) / 1000.0
    if strike is None:
        rjb = ps2ff_rjb(repi, mag)
        dip_u = DEFAULT_DIP[mech] if dip is None else dip
        rup = Rupture(mag, depth_km, 0.0, dip_u, z_bottom)       # strike does not matter for Ztor
        info = {"method": "ps2ff (strike unknown)", "rupture": None}
        ztor = rup.z_top
    else:
        dip_u = DEFAULT_DIP[mech] if dip is None else dip
        rup = Rupture(mag, depth_km, strike, dip_u, z_bottom)
        rjb = repi if rup.point else rup.rjb(dx_m, dy_m)
        info = {"method": "finite fault (strike given)", "rupture": rup}
        ztor = rup.z_top
    info.update(mech=mech, ztor_km=float(ztor), dip=float(dip_u))
    return np.sqrt(rjb ** 2 + ztor ** 2), rjb, info
