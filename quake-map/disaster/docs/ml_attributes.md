# Machine-learned building attributes (MASTER_PLAN 1.5)

Data: Overture footprints for 9 Indian cities (Delhi, Mumbai, Bengaluru, Ahmedabad, Chennai, Hyderabad, Kolkata, Pune, Noida; about 5.1 million
buildings) with their sparse labels and Google 2.5D heights (`tools/get_ml_cities.py`). Features (`attrs_ml.py`) are footprint shape and
neighbourhood context; **no coordinates**, so the model must generalise across cities. Model: LightGBM, 250 rounds. Everything below is on
cities or 2 km tiles the model was not trained on.

## Experiment 1: train on mapped floor labels (passed the plan's test, NOT shipped)
Labels: Overture `num_floors` (52,969 labelled buildings). Baseline = the rule-based prior (Google height where usable,
else the area heuristic) with probabilities calibrated on the training cities, computed without any labels (the 99% case).

| held-out evaluation (storeys, log-loss, lower is better) | n | rule | model | gain (90% interval) | cities won |
|---|---|---|---|---|---|
| leave-city-out, all labelled | 52,969 | 1.223 | 1.057 | +0.166 (+0.141 to +0.195) | 9/9 |
| leave-city-out, buildings the height product cannot resolve | 8,276 | 1.344 | 0.927 | +0.417 (+0.384 to +0.448) | 9/9 |
| held-out 2 km tiles, all labelled | 52,969 | 1.161 | 0.908 | +0.252 (+0.232 to +0.273) | 9/9 |
| held-out tiles, unresolved buildings | 8,276 | 1.298 | 0.827 | +0.472 (+0.439 to +0.504) | 9/9 |

By the plan's rule this model should ship (it wins in 9 of 9 cities). Accuracy barely changes (54.1% vs 53.6%): the gain is in probability quality.

**But it is biased.** Mapped `num_floors` labels cluster on apartment blocks. Applied (with its height features hidden) to Noida's ordinary buildings
it predicts **3.0 storeys on average where Google's heights say 2.2**, and it is worse than guessing the true mix (log-loss 1.377 vs 1.201).
Importance weighting by "how typical is this building of the whole population" did not help (3.03 storeys, 1.391), so the bias is not covariate shift:
mapped blocks are taller than unmapped buildings of the same footprint and surroundings. Passing the plan's test on labelled buildings
therefore does not show the model is safe for the unlabelled majority, and it was not shipped.

## Experiment 2: learn from the satellite instead (shipped)
`tools/train_satellite_storeys.py`: train on Google heights where usable (>= 2 m, spread <= 6 m; 360,000 sampled buildings across the 9 cities, a
label-free and population-representative sample) with footprint and neighbourhood features only, then use it for the buildings where the height product
fails. Truth = Google storey bin (height / 2.8 m); baseline = the area heuristic calibrated on the training cities.

| leave-city-out | n | rule | model | gain (90% interval) | cities won |
|---|---|---|---|---|---|
| all usable-height buildings | 360,000 | 1.230 | 1.166 | +0.064 (+0.057 to +0.071) | 8/9 |
| footprints under 60 m2 | 119,684 | 1.150 | 1.113 | +0.037 (+0.024 to +0.050) | 7/9 |

* **Bias: mean storeys +0.03** against Google (rule +0.00); on small footprints +0.06 (rule +0.21).
* On 8,276 mapped-floor buildings whose height is unusable (never used for training) it agrees better with the labels than the rule: log-loss
  1.86 vs 2.73, within one storey bin 54.5% vs 37.8%
  (the labels average 6.2 storeys because of the same mapping bias, so this is a ranking check, not an unbiased level check).

Ship rule, fixed before running: beat the rule on pooled held-out log-loss with a 90% tile-cluster interval above zero, in at least two thirds of cities,
with |mean bias| <= 0.3 storeys and no loss on small footprints. All four hold.

## How it is used
Only for buildings whose storeys would otherwise be guesses (`guess` / `guess_unstable`) and only where Google heights exist; labelled and satellite-measured
storeys are never touched. Output = probabilities over the storey bins: `levels` takes the most likely bin (1, 2, 3, 6 or 10 storeys, capped at 2 for sheds and
factories), and the construction-type probabilities are the mixture over bins (`priors.type_probs(bin_probs=...)`). `levels_src = "satellite_model"`; the popup and
data note say so. The 2.2 MB model is in `priors/models/`; retrain with `tools/get_ml_cities.py` then `tools/train_satellite_storeys.py`.

## Effect and limits
* Noida: 159,778 buildings (33%) now come from the model. It gives 85% of them one storey (mean 1.2, the area rule gave 1.9): plausible for buildings the satellite
  reads as under 2 m, **but this is an extrapolation below the height range it was trained on and is not independently verified for them**. City storey mean 2.07 -> 1.84;
  night population (rescaled to GHS-POP) 4.80 M -> 4.77 M; baseline-box damage counts move < 1% and people at risk +2.7%.
* "Measured" storeys are still only the 66.9% from labels and satellite heights; the model share is reported separately as modelled.
* Labels and Google heights are both imperfect (the height product compresses heights, see docs/validation.md); the model inherits that compression.
* Not built: road-distance features (would need OSM roads for all nine cities), construction-material labels (Overture has none), a use-class model (a
  label-trained one beat the area rule 9/9 in leave-city-out, log-loss 0.587 vs 0.705, but it inherits the same label bias, so it was not wired in).
