"""Earthquake hazard: the v1.x shaking + finite-fault + damage steps moved out of Model.run unchanged (baselines must match)."""
import numpy as np
from .base import Hazard
import rupture, gmm
from gmpe import mmi_from_pga

class EarthquakeHazard(Hazard):
    name = "earthquake"
    cell_key = "pga"

    def intensity(self, model, params):
        from simulate import local_xy
        lat, lon, depth_km, mag = params["lat"], params["lon"], params["depth_km"], params["mag"]
        strike, dip, rake, pga_override = params.get("strike"), params.get("dip"), params.get("rake"), params.get("pga_override")
        if pga_override is not None:
            pga = np.asarray(pga_override, float)
            mu = np.log(np.maximum(pga, 1e-9))
            sigma = tau = phi = np.zeros_like(mu)
            rjb = r_eff = np.full(len(mu), np.nan)
            geo = {"method": "external shaking map", "mech": "unspec", "ztor_km": 0.0, "rupture": None}
            gm = {"region": "external", "models": [], "beyond_validity": False}
        else:
            dx, dy = local_xy(lat, lon, model.lat, model.lon)
            r_eff, rjb, geo = rupture.distances(dx, dy, mag, depth_km, strike, dip, rake)
            region = gmm.region_for(lat, lon)
            mu, sigma, tau, phi, gm = gmm.pga(mag, r_eff, model.vs30, geo["mech"], region)
            pga = np.exp(mu)
        haz = {"mu": mu, "sigma": sigma, "tau": tau, "phi": phi, "rjb": rjb, "r_eff": r_eff,
               "scenario": dict(lat=lat, lon=lon, depth_km=depth_km, mag=mag, strike=strike, dip=dip, rake=rake, time_of_day=params.get("time_of_day", "night"))}
        return {"value": pga, "pga": pga, "geo": geo, "gm": gm, "haz": haz}

    def damage(self, model, intensity):
        from simulate import damage_probs
        return damage_probs(intensity["pga"], model.W)

    def table(self, model, intensity):
        pga = intensity["pga"]
        return {"pga": pga.round(3), "mmi": mmi_from_pga(pga).round(1), "vs30": model.vs30.round().astype(int)}

    def summary(self, model, intensity, p, occ):
        import casualty
        from simulate import FRAG, BETA, TYPES
        pga, geo, gm = intensity["pga"], intensity["geo"], intensity["gm"]
        cas_tot, _ = casualty.casualties(pga, model.W, occ, FRAG, BETA, TYPES, *model._cas)
        cas = casualty.summary(cas_tot, casualty.pager_deaths(mmi_from_pga(pga), occ, model._cas[1]), model._cas[1])
        return {"max_pga": round(float(pga.max()), 3), "max_mmi": round(float(mmi_from_pga(pga.max())), 1),
                "peak": [["Peak shaking (g)", round(float(pga.max()), 3)], ["Peak intensity (MMI)", round(float(mmi_from_pga(pga.max())), 1)]],
                "vs30_median": int(np.median(model.vs30)), "soil": model.soil, "distance_method": geo["method"],
                "casualties": cas, "mechanism": geo["mech"], "ztor_km": round(geo["ztor_km"], 1), "gm_region": gm["region"],
                "gm_models": gm["models"], "beyond_validity": gm["beyond_validity"]}

    def origin(self, params):
        return params["lat"], params["lon"]

    def extra_output(self, model, params, intensity):
        return {"rupture": model._rupture_geojson(params["lat"], params["lon"], intensity["geo"])}

    def uncertainty_supported(self):
        return True

    def describe(self):
        return {"name": self.name, "label": "Earthquake", "intensity": "Peak ground acceleration", "unit": "g",
                "inputs": ["lat", "lon", "depth_km", "mag", "time_of_day", "strike", "dip", "rake"],
                "note": "Boore & Atkinson 2008 / Boore et al. 2014 (+ Sharma 2009 in the Himalaya), finite-fault distance, lognormal fragility."}
