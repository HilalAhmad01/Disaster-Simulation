"""Ground motion: Boore & Atkinson (2008) PGA model (NGA-West1) plus Worden et al. (2012) PGA->MMI.

BA08 is built for shallow crustal quakes, roughly M5-8, distance < 200 km, Vs30 180-1300 m/s.
Beyond that range treat results as rough. Himalayan-arc events 200+ km away need a regional model (later fix).
"""
import numpy as np

# BA08 PGA coefficients (unspecified mechanism, SS/RS/NS offsets)
E = {"unspec": -0.53804, "ss": -0.50350, "rs": -0.75472, "ns": -0.50970}
E5, E6, E7, MH = 0.28805, -0.10164, 0.0, 6.75
C1, C2, C3, H = -0.66050, 0.11970, -0.01151, 1.35
MREF, RREF = 4.5, 1.0
BLIN, B1, B2 = -0.360, -0.640, -0.14
VREF, V1, V2 = 760.0, 180.0, 300.0
A1, PGA_LOW, A2 = 0.03, 0.06, 0.09

def _fm(m, e_mech):
    d = m - MH
    return np.where(m <= MH, e_mech + E5 * d + E6 * d * d, e_mech + E7 * d)

def _fd(r_km, m):
    r = np.sqrt(r_km ** 2 + H ** 2)
    return (C1 + C2 * (m - MREF)) * np.log(r / RREF) + C3 * (r - RREF)

def _bnl(vs):
    return np.where(vs <= V1, B1,
           np.where(vs <= V2, (B1 - B2) * np.log(vs / V2) / np.log(V1 / V2) + B2,
           np.where(vs <= VREF, B2 * np.log(vs / VREF) / np.log(V2 / VREF), 0.0)))

def _fs(pga4nl, vs):
    flin = BLIN * np.log(vs / VREF)
    bnl = _bnl(vs)
    dx, dy = np.log(A2 / A1), bnl * np.log(A2 / PGA_LOW)
    c, d = (3 * dy - bnl * dx) / dx ** 2, -(2 * dy - bnl * dx) / dx ** 3
    p = np.maximum(pga4nl, 1e-12)
    x = np.log(p / A1)
    base = bnl * np.log(PGA_LOW / 0.1)
    fnl = np.where(p <= A1, base, np.where(p <= A2, base + c * x ** 2 + d * x ** 3, bnl * np.log(p / 0.1)))
    return flin + fnl

def pga_g(mag, dist_km, vs30=250.0, mech="unspec"):
    """Median PGA in g. dist_km is hypocentral distance, so depth matters (point-source simplification)."""
    m = np.asarray(mag, float)
    r = np.asarray(dist_km, float)
    vs = np.asarray(vs30, float)
    base = _fm(m, E[mech]) + _fd(r, m)
    return np.exp(base + _fs(np.exp(base), vs))   # PGA on rock (Vs30=760) drives the nonlinear soil term

def mmi_from_pga(pga_in_g):
    """Worden et al. (2012) PGA -> Modified Mercalli Intensity."""
    lg = np.log10(np.maximum(np.asarray(pga_in_g, float) * 980.665, 1e-6))
    return np.clip(np.where(lg <= 1.57, 1.78 + 1.55 * lg, -1.60 + 3.70 * lg), 1, 10)
