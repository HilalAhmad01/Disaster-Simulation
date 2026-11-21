# Replay: Gorkha (Nepal) 2015-04-25, M7.8

Shaking: USGS ShakeMap us20002926 and our model (finite fault: strike 290.0, dip 7.0, rake 101.0, depth 8.22 km, M7.8). Damage: Copernicus EMS EMSR125 satellite grading. Buildings: Overture.

Limits: grading is visible damage on imagery (not engineering damage states) and not a random sample of buildings; the storeys/types of these buildings are guesses outside Google's coverage; the fragility curves are uncalibrated placeholders. Only ranking skill is meaningful; absolute counts are not compared.

## Areas

| area           |   buildings in footprint data |   graded points matched |   damaged+destroyed share |   ShakeMap PGA median g |   our model PGA median g |   log ratio our/ShakeMap (median) |
|:---------------|------------------------------:|------------------------:|--------------------------:|------------------------:|-------------------------:|----------------------------------:|
| Kathmandu (01) |                        153928 |                     626 |                     0.256 |                   0.426 |                    0.272 |                            -0.456 |

## Ranking skill (AUC: 0.5 = no skill, 1 = perfect)

| shaking           | outcome                                   | ranked by                 |   n |   damaged |    AUC | AUC 90% CI                        | area           |
|:------------------|:------------------------------------------|:--------------------------|----:|----------:|-------:|:----------------------------------|:---------------|
| ShakeMap PGA      | damaged or destroyed vs no visible damage | full model                | 626 |       160 |  0.432 | 0.385-0.497                       | Kathmandu (01) |
| ShakeMap PGA      | damaged or destroyed vs no visible damage | PGA only                  | 626 |       160 |  0.434 | 0.378-0.504                       | Kathmandu (01) |
| ShakeMap PGA      | damaged or destroyed vs no visible damage | cells (500 m): full model | 407 |        14 | -0.609 | top-10% hit 0.00 (Spearman shown) | Kathmandu (01) |
| ShakeMap PGA      | destroyed vs no visible damage            | full model                | 626 |       160 |  0.431 | 0.381-0.497                       | Kathmandu (01) |
| ShakeMap PGA      | destroyed vs no visible damage            | PGA only                  | 626 |       160 |  0.434 | 0.378-0.504                       | Kathmandu (01) |
| ShakeMap PGA      | destroyed vs no visible damage            | cells (500 m): full model | 407 |        14 | -0.607 | top-10% hit 0.00 (Spearman shown) | Kathmandu (01) |
| our shaking model | damaged or destroyed vs no visible damage | full model                | 626 |       160 |  0.499 | 0.434-0.543                       | Kathmandu (01) |
| our shaking model | damaged or destroyed vs no visible damage | PGA only                  | 626 |       160 |  0.522 | 0.457-0.576                       | Kathmandu (01) |
| our shaking model | damaged or destroyed vs no visible damage | cells (500 m): full model | 407 |        14 |  0.363 | top-10% hit 0.00 (Spearman shown) | Kathmandu (01) |
| our shaking model | destroyed vs no visible damage            | full model                | 626 |       160 |  0.501 | 0.432-0.545                       | Kathmandu (01) |
| our shaking model | destroyed vs no visible damage            | PGA only                  | 626 |       160 |  0.522 | 0.457-0.576                       | Kathmandu (01) |
| our shaking model | destroyed vs no visible damage            | cells (500 m): full model | 407 |        14 |  0.383 | top-10% hit 0.00 (Spearman shown) | Kathmandu (01) |
