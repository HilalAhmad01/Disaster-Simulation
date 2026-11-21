"""Casualties by injury severity (MASTER_PLAN 5.5), Hazus indoor rates, plus a USGS PAGER sanity check.

Indoor casualties for a building = occupants x sum over damage cases of P(case) x rate(type, case, severity), with 'complete'
damage split into collapse / no collapse by a per-type fraction. Severity 1-3 are injuries of increasing seriousness,
severity 4 is death. Rates are per building type (priors/casualty_rates.csv: the Hazus 6.1 Technical Manual tables 12-3 to
12-7 for URML, C3L, C3M, mapped to URM, RC_LOW, RC_MID) and the collapse fractions are Table 12-8. They describe US stock; the
type mapping is a judgement. Outdoor casualties are not modelled.
"""
import json
import numpy as np, pandas as pd
from scipy.special import ndtr
import priors

CASES = ("slight", "moderate", "extensive", "complete_no_collapse", "complete_collapse")
SEVERITY = ("injury_1", "injury_2", "injury_3", "deaths")

def load(data_dir=None):
    """(rates, params): rates = {type: (5 damage cases, 4 severities) array of fractions of occupants}."""
    df = pd.read_csv(priors.path("casualty_rates.csv", data_dir))
    cols = [f"severity_{k}" for k in range(1, 5)]
    rates = {t: g.set_index("damage_case").loc[list(CASES), cols].to_numpy(float) for t, g in df.groupby("type")}
    p = json.loads(priors.path("casualty_params.json", data_dir).read_text())
    return rates, p

def casualties(pga, W, occ, frag, beta, types, rates=None, params=None):
    """Expected casualties per severity. pga (n,) g; W (n, 3) type weights (mix or one-hot); occ (n,) people; frag/beta as in
    simulate.damage_probs; types = type names in column order of W. Returns (totals (4,), per-building deaths (n,))."""
    lp = np.log(np.maximum(pga, 1e-9))[:, None]
    E = []
    for j, t in enumerate(types):
        sel = W[:, j] > 0
        if sel.any():
            E.append((j, sel, ndtr((lp[sel] - np.log(np.asarray(frag[t]))) / beta[t])))        # P(DS >= k), k = slight..complete
    return casualties_from(E, W, occ, types, rates, params)

def casualties_from(E, W, occ, types, rates=None, params=None):
    """casualties() from precomputed per-type exceedance: E = [(column j, selector, P(DS >= k) of the selected buildings)]."""
    rates, params = (rates, params) if rates is not None else load()
    per = np.zeros((len(W), 4))                                     # people hurt per severity, per building
    for j, sel, Ex in E:
        t = types[j]
        pm = np.column_stack([Ex[:, 0] - Ex[:, 1], Ex[:, 1] - Ex[:, 2], Ex[:, 2] - Ex[:, 3], Ex[:, 3]])     # P(DS = slight..complete)
        f = params["collapse_fraction_given_complete"][t]
        cases = np.column_stack([pm[:, 0], pm[:, 1], pm[:, 2], pm[:, 3] * (1 - f), pm[:, 3] * f])
        per[sel] += (W[sel, j] * occ[sel])[:, None] * (cases @ rates[t])
    return per.sum(0), per[:, 3]

def totals_one_type(E, typ, occ, types, rates, params):
    """Casualty totals (4,) when each building has one type: E (n, 4) = P(DS >= k) of its type, typ (n,) = column in `types`.
    Same sum as casualties_from with a one-hot W, grouped by type first (people-weighted case sums per type, then x rates)."""
    pm = np.column_stack([E[:, 0] - E[:, 1], E[:, 1] - E[:, 2], E[:, 2] - E[:, 3], E[:, 3]])
    f = np.array([params["collapse_fraction_given_complete"][t] for t in types], float)[typ]
    cases = np.column_stack([pm[:, 0], pm[:, 1], pm[:, 2], pm[:, 3] * (1 - f), pm[:, 3] * f]) * np.asarray(occ, float)[:, None]
    out = np.zeros(4)
    for j, t in enumerate(types):
        sel = typ == j
        if sel.any():
            out += cases[sel].sum(0) @ rates[t]
    return out

def pager_deaths(mmi, people, params=None):
    """USGS PAGER-style fatalities: sum of people x Phi(ln(MMI/theta)/beta) with the India parameters."""
    params = params or load()[1]
    th, be = params["pager_india"]["theta"], params["pager_india"]["beta"]
    return float((np.asarray(people) * ndtr(np.log(np.maximum(mmi, 1.0) / th) / be)).sum())

def summary(totals, pager, params=None):
    """JSON-friendly block for the API/UI. Flags a large disagreement between our deaths and the PAGER estimate."""
    params = params or load()[1]
    deaths = float(totals[3])
    ratio = deaths / pager if pager > 0 else float("inf") if deaths > 0 else 1.0
    lim = params["pager_flag_ratio"]
    return {"injury_1": int(round(totals[0])), "injury_2": int(round(totals[1])), "injury_3": int(round(totals[2])), "deaths": int(round(deaths)),
            "pager_deaths": int(round(pager)), "ratio_to_pager": None if not np.isfinite(ratio) else round(ratio, 2),
            "pager_disagreement": bool(not (1.0 / lim <= ratio <= lim)) if np.isfinite(ratio) else True,
            "verified": bool(params.get("verified")), "caveat": params["note"]}
