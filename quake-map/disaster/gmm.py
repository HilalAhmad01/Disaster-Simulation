"""Ground-motion models for PGA and their weighted mix (MASTER_PLAN 2.3). Pure numpy; OpenQuake is NOT a runtime
dependency: every equation below is a port checked against OpenQuake in tests/test_gmpe_openquake.py.

Each model function is f(mag, dist_km, vs30, mech) -> (ln PGA in g, sigma_total, tau, phi) as arrays, in natural-log
units, where dist_km is the distance handed over by rupture.py (Rjb, or sqrt(Rjb^2 + Ztor^2)) and mech is one of
'ss', 'rs', 'ns', 'unspec'.

Models
  BA08     Boore & Atkinson (2008), NGA-West1, worldwide shallow crust, distance < 200 km     (gmpe.py)
  BSSA14   Boore, Stewart, Seyhan & Atkinson (2014), NGA-West2, valid to ~400 km, no basin term for PGA
  SHARMA09 Sharma, Douglas, Bungum & Kotadia (2009), Himalaya + Zagros, PGA taken as SA(0.04 s) as OpenQuake does

Weights (LOGIC) are decided in pga(): see the comments there for where every number comes from.
"""
import numpy as np
import gmpe as ba08

# ---------------- BSSA14 (PGA row of the coefficient table, copied from OpenQuake 3.26.2 BooreEtAl2014) ----------------
B14 = dict(e0=0.4473, e1=0.4856, e2=0.2459, e3=0.4539, e4=1.431, e5=0.05053, e6=-0.1662, Mh=5.5,
           c1=-1.134, c2=0.1917, c3=-0.008088, h=4.5, Dc3=0.0, c=-0.6, Vc=1500.0, f4=-0.15, f5=-0.00701,
           R1=110.0, R2=270.0, DfR=0.1, DfV=0.07, f1=0.695, f2=0.495, tau1=0.398, tau2=0.348)
B14_MREF, B14_RREF, B14_VREF, B14_F1, B14_F3, B14_V1, B14_V2 = 4.5, 1.0, 760.0, 0.0, 0.1, 225.0, 300.0

def _b14_emech(mech):
    return {"unspec": B14["e0"], "ss": B14["e1"], "ns": B14["e2"], "rs": B14["e3"]}[mech]

def _b14_rock(mag, r, mech):
    """ln PGA on rock (no site term): magnitude scaling (eq. 2) + path scaling (eq. 3)."""
    d = mag - B14["Mh"]
    fm = _b14_emech(mech) + np.where(mag <= B14["Mh"], B14["e4"] * d + B14["e5"] * d * d, B14["e6"] * d)
    rr = np.sqrt(r ** 2 + B14["h"] ** 2)
    fp = (B14["c1"] + B14["c2"] * (mag - B14_MREF)) * np.log(rr / B14_RREF) + (B14["c3"] + B14["Dc3"]) * (rr - B14_RREF)
    return fm + fp

def bssa14(mag, dist_km, vs30, mech="unspec"):
    mag, r, vs = np.broadcast_arrays(np.asarray(mag, float), np.asarray(dist_km, float), np.asarray(vs30, float))
    rock = _b14_rock(mag, r, mech)
    pga_r = np.exp(rock)
    flin = B14["c"] * np.log(np.minimum(vs, B14["Vc"]) / B14_VREF)                       # eq. 6
    f2 = B14["f4"] * (np.exp(B14["f5"] * (np.minimum(vs, 760.0) - 360.0)) - np.exp(B14["f5"] * 400.0))   # eq. 8
    fnl = B14_F1 + f2 * np.log((pga_r + B14_F3) / B14_F3)                                # eq. 7
    mean = rock + flin + fnl
    tau = np.where(mag <= 4.5, B14["tau1"], np.where(mag >= 5.5, B14["tau2"], B14["tau1"] + (B14["tau2"] - B14["tau1"]) * (mag - 4.5)))
    phi = np.where(mag <= 4.5, B14["f1"], np.where(mag >= 5.5, B14["f2"], B14["f1"] + (B14["f2"] - B14["f1"]) * (mag - 4.5)))      # eq. 17
    phi = phi + np.where(r > B14["R2"], B14["DfR"], np.where(r > B14["R1"], B14["DfR"] * np.log(np.maximum(r, 1e-9) / B14["R1"]) / np.log(B14["R2"] / B14["R1"]), 0.0))   # eq. 16
    phi = phi - np.where(vs <= B14_V1, B14["DfV"], np.where(vs < B14_V2, B14["DfV"] * np.log(B14_V2 / vs) / np.log(B14_V2 / B14_V1), 0.0))                         # eq. 15
    return mean, np.sqrt(tau ** 2 + phi ** 2), tau, phi

# ---------------- Sharma et al. (2009), PGA = SA(0.04 s) row ----------------
S09 = dict(b1=1.0170, b2=0.1046, b3=-1.0070, b4=15.0, b5=-0.0735, b6=-0.3068, sigma=0.3227)     # log10 units, Y in m/s2
G_MS2 = 9.80665
SHARMA_H = {"ss": 1.0, "ns": 1.0, "rs": 0.0, "unspec": 0.5}   # H=1 strike-slip (normal is treated as strike-slip, as the paper
                                                              # has no normal events), H=0 reverse; unspecified = average of the two

def sharma09(mag, dist_km, vs30, mech="unspec"):
    mag, r, vs = np.broadcast_arrays(np.asarray(mag, float), np.asarray(dist_km, float), np.asarray(vs30, float))
    rock_site = (vs > 760.0).astype(float)                       # S = 1 for rock (NEHRP B/C boundary, 760 m/s)
    log10y = S09["b1"] + S09["b2"] * mag + S09["b3"] * np.log10(np.sqrt(r ** 2 + S09["b4"] ** 2)) + S09["b5"] * rock_site + S09["b6"] * SHARMA_H[mech]
    mean = log10y * np.log(10.0) - np.log(G_MS2)
    sig = np.full(mean.shape, S09["sigma"] * np.log(10.0))
    # the paper gives only a total sigma; split it with BA08's tau/sigma ratio (an assumption, used by the Monte Carlo only)
    tau = sig * (ba08.TAU / ba08.SIGMA)
    return mean, sig, tau, np.sqrt(np.maximum(sig ** 2 - tau ** 2, 0.0))

def ba08_model(mag, dist_km, vs30, mech="unspec"):
    mag, r, vs = np.broadcast_arrays(np.asarray(mag, float), np.asarray(dist_km, float), np.asarray(vs30, float))
    mean = np.log(ba08.pga_g(mag, r, vs, mech))
    one = np.ones(mean.shape)
    return mean, one * ba08.SIGMA, one * ba08.TAU, one * ba08.PHI

MODELS = {"BA08": ba08_model, "BSSA14": bssa14, "SHARMA09": sharma09}

# ---------------- logic mix ----------------
# Evidence: Selvan & Sinha (2024), arXiv:2410.05918, Table 5 (Indo-Gangetic Plains) and Table 4 (Western/Central
# Himalaya), PGA (0.01 s) column. Log-likelihood scores (lower = better): IGP BA08 2.04, BSSA14 2.92; Himalaya BA08 2.73,
# BSSA14 3.37. Scherbaum et al. (2009) weights w_i ~ 2^(-LLH_i): IGP BA08:BSSA14 = 0.65:0.35 (Himalaya 0.61:0.39).
# The paper's best PGA models (Zhao06, Kanno06, Idriss08) are not ported (outside the plan); its mean Z shows BA08
# nearly unbiased (+0.0 IGP) and BSSA14 under-predicting (+1.0 IGP): this is why BA08 gets the larger weight.
# Validity: BA08 is meant for < 200 km, BSSA14 for < 400 km, Sharma 2009 is only used out to ~100 km (assumption: its
# data cover the near/regional field). Weights ramp linearly in log distance between these nodes so maps show no steps.
LLH_W = {"BA08": 0.65, "BSSA14": 0.35}
# distance nodes (km) and per-model weights at those nodes (rows follow NODES)
NODES = np.array([1.0, 80.0, 120.0, 160.0, 240.0, 400.0])
LOGIC = {
    # Himalaya / IGP: LLH weights for BA08 and BSSA14; Sharma 2009 gets 0.2 near the source as a Himalayan-data model
    # (JUDGEMENT: it was not ranked by Selvan & Sinha), fading out by 120 km; BA08 fades out 160 -> 240 km.
    "himalaya": {"BA08": [.52, .52, .65, .65, 0.0, 0.0], "BSSA14": [.28, .28, .35, .35, 1.0, 1.0], "SHARMA09": [.2, .2, 0.0, 0.0, 0.0, 0.0]},
    # elsewhere: no regional evidence, equal weight to the two worldwide models; BA08 fades out 160 -> 240 km
    "crustal": {"BA08": [.5, .5, .5, .5, 0.0, 0.0], "BSSA14": [.5, .5, .5, .5, 1.0, 1.0]},
}
MAX_VALID_KM = 400.0              # beyond this every model is extrapolated; the summary says so

def weights(dist_km, region="himalaya"):
    """{model: weight array} at the given effective distances (km); rows sum to 1."""
    x = np.log10(np.clip(np.asarray(dist_km, float), NODES[0], NODES[-1]))
    out = {m: np.interp(x, np.log10(NODES), w) for m, w in LOGIC[region].items()}
    tot = sum(out.values())
    return {m: w / tot for m, w in out.items()}

def pga(mag, dist_km, vs30, mech="unspec", region="himalaya"):
    """Weighted mix: returns (ln median PGA in g, sigma_total, tau, phi, info). The mean is the weight-averaged ln PGA;
    sigma^2 = sum w (sigma_i^2 + (mu_i - mu)^2) keeps the spread between models. tau and phi are the weighted rms of
    the models' own values (the between-model spread is added to phi)."""
    w = weights(dist_km, region)
    res = {m: MODELS[m](mag, dist_km, vs30, mech) for m in w if np.any(w[m] > 0)}
    mu = sum(w[m] * res[m][0] for m in res)
    spread = sum(w[m] * (res[m][0] - mu) ** 2 for m in res)
    sig2 = sum(w[m] * res[m][1] ** 2 for m in res) + spread
    tau2 = sum(w[m] * res[m][2] ** 2 for m in res)
    phi2 = sum(w[m] * res[m][3] ** 2 for m in res) + spread
    return mu, np.sqrt(sig2), np.sqrt(tau2), np.sqrt(phi2), {"region": region, "models": sorted(res), "beyond_validity": bool(np.any(np.asarray(dist_km) > MAX_VALID_KM))}

# ---------------- region selection ----------------
_REGIONS = None
def region_for(lat, lon):
    """'himalaya' if the epicentre lies in priors/regions.geojson's Himalayan-arc polygon, else 'crustal'."""
    global _REGIONS
    import json, pathlib, shapely
    if _REGIONS is None:
        f = pathlib.Path(__file__).resolve().parent / "priors" / "regions.geojson"
        feats = json.loads(f.read_text())["features"]
        _REGIONS = [(ft["properties"]["region"], shapely.from_geojson(json.dumps(ft["geometry"]))) for ft in feats]
    p = shapely.Point(lon, lat)
    for name, poly in _REGIONS:
        if poly.contains(p):
            return name
    return "crustal"
