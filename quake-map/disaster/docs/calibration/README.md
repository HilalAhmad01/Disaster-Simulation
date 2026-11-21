# Fragility calibration status

**The shipped fragility curves are UNCALIBRATED v1 placeholders** (`priors/fragility.json`, status field). They are not fitted
to data and not taken from HAZUS or GEM. Every absolute damage, casualty and "people at risk" number inherits that.

## What exists
* `calibration.py` - grouped maximum-likelihood fit of lognormal fragility curves with shaking uncertainty integrated out
  (exact probit convolution, checked against Gauss-Hermite), conditional on damage because the survey lists damaged houses only,
  ward-bootstrap 90% intervals, the plan's rule for choosing the final curves (use the fit where the interval is narrow),
  held-out scoring, ward-level population-weighted ShakeMap PGA, class and grade mapping.
* `tools/get_nepal_calibration.py` - downloads the Gorkha ShakeMap (works) and prints how to obtain the rest.
* `tools/calibrate_fragility.py` - the whole chain from survey CSV + ward polygons + ShakeMap to plots and `--apply`.
* tests/test_calibration.py recovers known curves from simulated survey counts (incl. an end-to-end run of the tool on a
  synthetic survey, ward polygons and a ShakeMap-format file): fitted medians and dispersion within 12% of the truth.

## What is missing (blocked, not skipped)
* The Nepal 2015 building-level survey (NSO microdata catalog 69 / KLL portal) needs registration and was not reachable from
  the machine this was built on. Without it no curve could be fitted, so none was: **no numbers here come from Nepal data.**
* Consequently 3.5 (compare with GEM / HAZUS, document per class) and the held-out-districts validation (3.6.1) are not done.
  GEM vulnerability functions and the Hazus fragility tables were not obtained either (FEMA refuses scripted downloads).
* When the data is in place: `python tools/calibrate_fragility.py ... --apply` writes `data/priors/fragility.json`, which
  overrides the shipped file and also stores bootstrap samples that the Monte Carlo then uses instead of its +-25% assumption.

## Caveats the fit already handles or documents
* Truncation: damaged-only registration means the onset (slight-damage median) is weakly identified; the upper medians and beta
  are the reliable parts (tests/test_calibration.py shows this).
* Nepal stock is not Indian urban stock: mud-mortar stone, adobe, timber and bamboo are not used (`classify_nepal`).
* Grade 4 and 5 both map to "complete"; `--alt-grades` (only grade 5 = complete) is the sensitivity check.
* Column names in `classify_nepal` follow the public derived tables and must be checked against the real data dictionary.
