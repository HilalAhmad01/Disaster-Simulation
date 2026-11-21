"""Hazard plug-in interface (MASTER_PLAN 7.1). A hazard turns scenario parameters into one intensity value per building and
the chance of each damage state; everything else (buildings, people, aggregation, responder layer, UI) is shared."""
import numpy as np

class Hazard:
    name = "base"
    cell_key = "intensity"              # name of the cell-level intensity property in the map data

    def intensity(self, model, params):
        """Per-building intensity. Returns a dict that must hold 'value' (n,) = the primary intensity (g, m of water, m/s ...);
        any other keys are the hazard's own (kept for damage/table/summary)."""
        raise NotImplementedError

    def damage(self, model, intensity):
        """(n, 4) array: P(damage >= slight / moderate / extensive / complete) per building."""
        raise NotImplementedError

    def describe(self):
        """What the UI needs: label, unit, the scenario inputs and how to read the result."""
        raise NotImplementedError

    # ---- optional hooks with neutral defaults ----
    def table(self, model, intensity):
        """Extra columns (name -> (n,) array) for the per-building table shown in popups."""
        return {"intensity": np.round(intensity["value"], 3)}

    def summary(self, model, intensity, p, occ):
        """Extra items for the summary dict (e.g. casualties). Must not use keys the shared code sets."""
        return {}

    def origin(self, params):
        """(lat, lon) the 'distance from' column of priority cells is measured from, or None."""
        return None

    def extra_output(self, model, params, intensity):
        """Extra top-level items of the response (e.g. a rupture outline)."""
        return {}

    def uncertainty_supported(self):
        return False
