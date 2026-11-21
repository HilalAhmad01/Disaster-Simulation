"""Casualties by injury severity (MASTER_PLAN 5.5), Hazus-style, plus a USGS PAGER sanity check.

Indoor casualties for a building = occupants x sum over damage cases of P(case) x rate(case, severity), with 'complete'
damage split into collapse / no collapse by a per-type fraction. Severity 1-3 are injuries of increasing seriousness,
severity 4 is death. The rates and fractions in priors/casualty_rates.csv and casualty_params.json are UNVERIFIED
placeholders (see their 'source' fields); outdoor casualties are not modelled. The result is only as good as those.
"""
import json
import numpy as np, pandas as pd
from scipy.special import ndtr
import priors

CASES = ("slight", "moderate", "extensive", "complete_no_collapse", "complete_collapse")
SEVERITY = ("injury_1", "injury_2", "injury_3", "deaths")

def load(data_dir=None):
    r = pd.read_csv(priors.path("casualty_rates.csv", data_dir)).set_index("damage_state").loc[list(CASES)]
    p = json.loads(priors.path("casualty_params.json", data_dir).read_text())
    return r[[f"severity_{k}" for k in range(1, 5)]].to_numpy(float), p

def casualties(pga, W, occ, frag, beta, types, rates=None, params=None):
    """Expected casualties per severity. pga (n,) g; W (n, 3) type weights (mix or one-hot); occ (n,) people; frag/beta as in
    simulate.damage_probs; types = type names in column order of W. Returns (totals (4,), per-building deaths (n,))."""
    rates, params = (rates, params) if rates is not None else load()
    lp = np.log(np.maximum(pga, 1e-9))[:, None]
    per = np.zeros((len(pga), 4))                                   # people hurt per severity, per building
    for j, t in enumerate(types):
        sel = W[:, j] > 0
        if not sel.any():
            continue
        E = ndtr((lp[sel] - np.log(np.asarray(frag[t]))) / beta[t])        # P(DS >= k), k = slight..complete
        pm = np.column_stack([E[:, 0] - E[:, 1], E[:, 1] - E[:, 2], E[:, 2] - E[:, 3], E[:, 3]])     # P(DS = slight..complete)
        f = params["collapse_fraction_given_complete"][t]
        cases = np.column_stack([pm[:, 0], pm[:, 1], pm[:, 2], pm[:, 3] * (1 - f), pm[:, 3] * f])
        per[sel] += (W[sel, j] * occ[sel])[:, None] * (cases @ rates)
    return per.sum(0), per[:, 3]

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
