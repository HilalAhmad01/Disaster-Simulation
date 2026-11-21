# Fragility calibration status (plan 3.1 - 3.6)

**Result in one line: the fit was run on the real Nepal 2015 survey and produced no curve that is usable, so the shipped curves
are still the UNCALIBRATED v1 placeholders.** They have now been compared with GEM (see below), and the reasons the Nepal fit is
rejected are recorded here. Every absolute damage, casualty and "people at risk" number still inherits the placeholder status.

## What was run (2026-11-21)
* **Data** (`tools/get_nepal_calibration.py`, `tools/prepare_nepal_survey.py`)
  * Building table of the Nepal 2015 Household Registration for Housing Reconstruction survey: 762,094 buildings with a damage grade in
    the 11 complete-census districts (Okhaldhunga, Sindhupalchok, Dolakha, Ramechhap, Sindhuli, Kavrepalanchok, Dhading, Rasuwa, Nuwakot,
    Gorkha, Makwanpur by code 12-36). Source is an **open mirror** of the derived table; the official file is the NSO microdata catalog 69
    (registration). The mirror's terms are non-commercial research and education with attribution to the Government of Nepal (NPC/CBS) and
    Kathmandu Living Labs.
  * HRRP federal-structure ward polygons (HDX, CC0). The survey's `ward_id` and the polygons' `DDGNWW` use the same coding, so the join is
    exact: 900 of 945 survey wards have a polygon, covering 94.9% of buildings.
  * USGS ShakeMap for Gorkha (PGA and its standard deviation) -> population-unweighted ward mean of ln PGA and spread.
* **Two corrections to the plan's assumptions, found in the data**
  * The table is *not* damaged-only: 61,050 buildings are recorded as "Not damaged" (carried as grade 1, like slight damage). They are now
    counted as damage state 0 and the likelihood is **untruncated** (`--undamaged-col condition_post_eq --undamaged-value "Not damaged"
    --untruncated`). The earlier truncated runs in this project had mislabelled them as slight damage.
  * Grades 4 and 5 both map to "complete"; grade 5 includes rubble cleared or new building built (`condition_post_eq`).
* **Classes**: URM = cement-mortar brick without RC, <= 2 floors (38,941 buildings); RC_LOW = RC (engineered or not), <= 4 floors (40,920);
  RC_MID = RC, >= 5 floors (1,558). Mud-mortar stone, adobe, timber and bamboo (about 90% of the survey) are not used: they are much weaker
  than Indian urban stock.

## Fit results (maximum likelihood with shaking uncertainty integrated out, 200 ward-bootstrap samples)
| class | buildings | median PGA (g): slight / moderate / extensive / complete | beta | verdict |
|---|---|---|---|---|
| URM | 35,657 | 0.31 / 0.38 / 0.94 / 2.05 (90% CI for the complete median 1.4 - 3.9) | 0.97 | fitted, **rejected**: held-out skill 0.03 (needs 0.10) and a complete median of 2 g lies outside the 0.2-0.7 g that the data span |
| RC_LOW | 27,520 | no finite solution (beta 1136, medians 0 / infinity) | - | **degenerate**: damage of RC buildings does not rise with ward PGA across districts (see below) |
| RC_MID | 842 | 0.48 / 0.60 / 0.88 / 1.26 | 0.0 | **degenerate** (beta on its boundary): too few buildings |

Held-out districts (plan 3.6.1: districts 22, 29 and 36 left out of the fit): URM log-loss skill 0.03 over the null (1,575 buildings),
RC_MID 0.03 (304), RC_LOW -5.98 (6,841; the diverged fit predicts nothing sensible).

## Why the survey cannot pin down curves here
* Ward-level rank correlation between ShakeMap PGA and mean damage grade is moderate for brick (+0.53, 193 wards) and weak for RC (+0.22,
  192 wards). The ShakeMap is smooth and model-based; the damage differences between neighbouring wards are driven by other things (local
  ground, slope, construction quality, age).
* The 11 districts span only about **0.23 - 0.65 g** (district means). Curve tails below 0.2 g and above 0.7 g are extrapolations; the
  median of "complete damage" for URM comes out at 2 g for that reason.
* District effects dominate: for RC buildings, 73% are grade 1 in one district and 62% grade 5 in another at similar PGA (code 24 vs 29).
* The fits also pool all Nepal brick and RC building quality into one curve; Indian urban stock differs.

## Safeguards added so such fits cannot be applied
* `summarize` flags a fit as `degenerate` (non-finite medians, medians outside 0.01 - 10 g, beta outside 0.2 - 1.5, optimiser failure)
  and a degenerate fit never counts as "narrow".
* `tools/calibrate_fragility.py` rejects a class whose held-out skill is below 0.10 (`MIN_HELDOUT_SKILL`) when held-out districts are given.
* `--apply` therefore wrote nothing but a status note, and `priors/fragility.json` is unchanged in its numbers.

## Comparison with GEM (plan 3.5): `gem_comparison.md`, `gem_comparison.png`
GEM Global Vulnerability Model v2026.0.0 (Nepal and India, CC BY-NC-SA 4.0; decision D5 is still open, so the XML stays in the
git-ignored `data/external/gem/`). Mean structural damage ratio against PGA, our curves turned into a damage ratio with assumed
0.02 / 0.10 / 0.50 / 1.00 per state:
* **URM (clay-brick masonry, 1-2 storeys):** ours is much more fragile than GEM at low shaking (at 0.2 g: ours 0.17 versus GEM
  median 0.00 over 8 functions; at 0.3 g 0.35 versus 0.01) and the curves cross at about 0.6 g; above that ours is less fragile
  (0.84 versus 0.94 at 0.8 g).
* **RC_LOW (infilled RC frame, 1-2 storeys):** ours is more fragile than the GEM median at every PGA up to 1.2 g (0.34 versus 0.07 at
  0.5 g; 0.80 versus 0.69 at 1.2 g), but GEM's 11 functions span a wide range (0.03 - 0.51 at 0.5 g), so the placeholder lies inside it
  at 0.5 g and above.
* The Nepal data (slight-damage median about 0.3 g; 61,050 undamaged of 762,094 buildings) sit between the two for brick.
* Not compared: RC_MID (GEM mid-rise functions use SA(1.0)). Hazus structural fragility is in spectral displacement and needs capacity
  spectra; no PGA-based Hazus table exists for buildings, so no Hazus comparison is made.
* Decision: **keep the placeholders and keep them labelled**. Swapping in GEM would replace one unvalidated model with another (and brings
  the non-commercial licence into the product). If you want GEM-based curves, that is a deliberate decision on D5.

## What is still missing
* A fit that is trustworthy needs the official microdata with building location (GPS) rather than ward averages, or a damage survey with
  measured shaking nearby. The official catalog 69 is behind registration and the Nepal government hosts do not answer from this machine.
* Population-weighted ward PGA (needs GHS-POP for Nepal: `tools/get_ghs_pop.py`); ward means here are unweighted ShakeMap grid points.

## Code that exists (all tested on synthetic data and now run on real data)
`calibration.py`, `tools/calibrate_fragility.py` (flags above), `tools/get_nepal_calibration.py`, `tools/prepare_nepal_survey.py`,
`tools/compare_gem.py`; `tests/test_calibration.py` recovers known curves from simulated surveys (medians and dispersion within 12% of truth).
