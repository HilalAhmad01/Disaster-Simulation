"""Aftershock: a second earthquake on top of the damage the first one left (used by Model.run_aftershock, not listed as a scenario).

Buildings that are already damaged are weaker. The aftershock's shaking is scaled up by 1 + WEAKENING x (expected damage state / 4) of
the building, then its damage chances are combined with the earlier ones as independent exceedances, 1 - (1 - p_before)(1 - p_after),
which keeps every building's damage chances in order (slight >= moderate >= extensive >= complete). The weakening factor is an
assumption, not a calibrated value: it makes a damaged building about 60% more shaken at the most, and nothing if it was unharmed."""
import numpy as np
from .earthquake import EarthquakeHazard

WEAKENING = 0.6

class AftershockHazard(EarthquakeHazard):
    name = "earthquake"                      # the page, phones and reports treat it as an earthquake

    def __init__(self, prior_p, weakening=WEAKENING):
        self.prior = np.asarray(prior_p, float)
        self.weak = 1.0 + weakening * self.prior.sum(1) / 4.0

    def intensity(self, model, params):
        inten = super().intensity(model, params)
        pga = inten["pga"] * self.weak       # effective shaking on the already weakened buildings
        inten["pga"] = inten["value"] = pga
        return inten

    def damage(self, model, intensity):
        p2 = super().damage(model, intensity)
        return 1.0 - (1.0 - self.prior) * (1.0 - p2)
