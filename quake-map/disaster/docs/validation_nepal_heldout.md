# Held-out Nepal towns (EMS gradings), 2015 Gorkha

**This is NOT the plan's district-level survey validation** (that survey was unreachable). It is a leave-one-town-out test on Copernicus EMS satellite gradings of buildings (EMSR125) joined to the USGS ShakeMap. Visible destruction only; one pooled curve (no building class); non-random mapping (teams mapped where damage was expected); 'Not Applicable' and 'Unknown' buildings excluded. Read the sign of the skill, not the size of the absolute rates.

## Graded buildings used

| town        |   damaged |   destroyed |   none |   median_pga_g |
|:------------|----------:|------------:|-------:|---------------:|
| Bharatpur   |         0 |           6 |  18076 |          0.429 |
| Bidur       |         0 |         120 |     62 |          0.596 |
| Chilime     |         0 |         129 |    239 |          0.563 |
| Kathmandu   |         0 |         183 |    548 |          0.427 |
| Pokhara     |         0 |           8 |  14559 |          0.252 |
| Sundarbazar |        13 |           3 |      7 |          0.492 |

## Outcome: destroyed

Curve fitted to all towns together: median 2.32 g, beta 0.63 (capacity spread; shaking sigma removed).

Leave-one-town-out (skill = 1 - logloss / logloss of the training base rate; > 0 means the curve beats a constant):

| held_out_town   |   theta_g |   beta |   median_pga_g |   observed_rate |   predicted_rate |     n |   events |   log_loss |   log_loss_null |   brier |   brier_null |   skill_logloss |
|:----------------|----------:|-------:|---------------:|----------------:|-----------------:|------:|---------:|-----------:|----------------:|--------:|-------------:|----------------:|
| Bharatpur       |     0.782 |  0     |          0.43  |          0.0003 |           0.1292 | 18082 |        6 |     0.1391 |          0.0295 |  0.0171 |       0.0011 |         -3.7163 |
| Bidur           |     3.956 |  0.87  |          0.595 |          0.6593 |           0.0265 |   182 |      120 |     2.3985 |          3.0569 |  0.6248 |       0.6466 |          0.2154 |
| Chilime         |     5.333 |  1.019 |          0.568 |          0.3505 |           0.0263 |   368 |      129 |     1.2881 |          1.6375 |  0.3325 |       0.344  |          0.2133 |
| Kathmandu       |     1.412 |  0     |          0.423 |          0.2503 |           0.0015 |   731 |      183 |     1.6373 |          1.2145 |  0.2496 |       0.2464 |         -0.3481 |
| Pokhara         |     3.301 |  0.869 |          0.245 |          0.0005 |           0.0055 | 14567 |        8 |     0.0084 |          0.0251 |  0.0006 |       0.001  |          0.6646 |

Calibration of the held-out predictions (all held-out towns pooled):

| predicted_range   |     n |   predicted |   observed |
|:------------------|------:|------------:|-----------:|
| 0.0001-0.001      |    63 |      0.0009 |     0.3492 |
| 0.001-0.01        | 15235 |      0.0053 |     0.0111 |
| 0.01-0.05         |   550 |      0.0264 |     0.4527 |
| 0.05-0.2          | 18082 |      0.1292 |     0.0003 |

Pooled held-out: log-loss 0.1398 vs 0.0700 for a constant base rate (skill -99.8%); Brier 0.02171 vs 0.01297.

## Outcome: damaged or destroyed

Curve fitted to all towns together: median 2.24 g, beta 0.62 (capacity spread; shaking sigma removed).

Leave-one-town-out (skill = 1 - logloss / logloss of the training base rate; > 0 means the curve beats a constant):

| held_out_town   |   theta_g |   beta |   median_pga_g |   observed_rate |   predicted_rate |     n |   events |   log_loss |   log_loss_null |   brier |   brier_null |   skill_logloss |
|:----------------|----------:|-------:|---------------:|----------------:|-----------------:|------:|---------:|-----------:|----------------:|--------:|-------------:|----------------:|
| Bharatpur       |     0.777 |  0     |          0.43  |          0.0003 |           0.1315 | 18082 |        6 |     0.1418 |          0.0303 |  0.0178 |       0.0011 |         -3.6756 |
| Bidur           |     3.661 |  0.837 |          0.595 |          0.6593 |           0.0279 |   182 |      120 |     2.364  |          3.0315 |  0.623  |       0.6461 |          0.2202 |
| Chilime         |     4.809 |  0.977 |          0.568 |          0.3505 |           0.028  |   368 |      129 |     1.267  |          1.6238 |  0.3314 |       0.3437 |          0.2197 |
| Kathmandu       |     1.399 |  0     |          0.423 |          0.2503 |           0.0017 |   731 |      183 |     1.6183 |          1.2029 |  0.2495 |       0.2462 |         -0.3453 |
| Pokhara         |     3.119 |  0.843 |          0.245 |          0.0005 |           0.0055 | 14567 |        8 |     0.0084 |          0.0257 |  0.0006 |       0.0011 |          0.673  |

Calibration of the held-out predictions (all held-out towns pooled):

| predicted_range   |     n |   predicted |   observed |
|:------------------|------:|------------:|-----------:|
| 0.0001-0.001      |    63 |      0.0009 |     0.3492 |
| 0.001-0.01        | 15235 |      0.0053 |     0.0111 |
| 0.01-0.05         |   550 |      0.028  |     0.4527 |
| 0.05-0.2          | 18082 |      0.1315 |     0.0003 |

Pooled held-out: log-loss 0.1405 vs 0.0700 for a constant base rate (skill -100.6%); Brier 0.02202 vs 0.01297.

