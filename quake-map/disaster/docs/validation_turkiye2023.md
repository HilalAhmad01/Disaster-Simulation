# Replay: Kahramanmaras (Turkiye) 2023-02-06, M7.8

Shaking: USGS ShakeMap us6000jllz and our model (finite fault: strike 227.61, dip 89.18, rake -1.29, depth 10.0 km, M7.8). Damage: Copernicus EMS EMSR648 satellite grading. Buildings: Overture.

Limits: grading is visible damage on imagery (not engineering damage states) and not a random sample of buildings; the storeys/types of these buildings are guesses outside Google's coverage; the fragility curves are uncalibrated placeholders. Only ranking skill is meaningful; absolute counts are not compared.

## Areas

| area              |   buildings in footprint data |   graded points matched |   damaged+destroyed share |   ShakeMap PGA median g |   our model PGA median g |   log ratio our/ShakeMap (median) |
|:------------------|------------------------------:|------------------------:|--------------------------:|------------------------:|-------------------------:|----------------------------------:|
| Elbistan (AOI20)  |                         12269 |                     225 |                     0.04  |                   0.105 |                    0.131 |                             0.213 |
| Gaziantep (AOI01) |                        182055 |                   10786 |                     0.001 |                   0.238 |                    0.21  |                            -0.112 |
| Kirikhan (AOI18)  |                         15674 |                    1340 |                     0.714 |                   0.757 |                    0.268 |                            -1.038 |

## Ranking skill (AUC: 0.5 = no skill, 1 = perfect)

| shaking           | outcome                                       | ranked by                 |     n |   damaged |    AUC | AUC 90% CI                        | area              |
|:------------------|:----------------------------------------------|:--------------------------|------:|----------:|-------:|:----------------------------------|:------------------|
| ShakeMap PGA      | damaged or destroyed vs no visible damage     | full model                |   155 |         9 |  0.699 | 0.530-0.871                       | Elbistan (AOI20)  |
| ShakeMap PGA      | damaged or destroyed vs no visible damage     | PGA only                  |   155 |         9 |  0.691 | 0.447-0.916                       | Elbistan (AOI20)  |
| ShakeMap PGA      | destroyed vs no visible damage                | full model                |   155 |         9 |  0.736 | 0.550-0.896                       | Elbistan (AOI20)  |
| ShakeMap PGA      | destroyed vs no visible damage                | PGA only                  |   155 |         9 |  0.691 | 0.447-0.916                       | Elbistan (AOI20)  |
| our shaking model | damaged or destroyed vs no visible damage     | full model                |   155 |         9 |  0.597 | 0.468-0.716                       | Elbistan (AOI20)  |
| our shaking model | damaged or destroyed vs no visible damage     | PGA only                  |   155 |         9 |  0.712 | 0.573-0.872                       | Elbistan (AOI20)  |
| our shaking model | destroyed vs no visible damage                | full model                |   155 |         9 |  0.58  | 0.484-0.689                       | Elbistan (AOI20)  |
| our shaking model | destroyed vs no visible damage                | PGA only                  |   155 |         9 |  0.712 | 0.573-0.872                       | Elbistan (AOI20)  |
| ShakeMap PGA      | damaged or destroyed vs no visible damage     | full model                | 10775 |         7 |  0.863 | 0.795-0.958                       | Gaziantep (AOI01) |
| ShakeMap PGA      | damaged or destroyed vs no visible damage     | PGA only                  | 10775 |         7 |  0.8   | 0.705-0.936                       | Gaziantep (AOI01) |
| ShakeMap PGA      | damaged or destroyed vs no visible damage     | cells (500 m): full model | 10022 |       200 |  0.203 | top-10% hit 0.20 (Spearman shown) | Gaziantep (AOI01) |
| ShakeMap PGA      | destroyed vs no visible damage                | full model                | 10773 |         5 |  0.876 | 0.808-0.960                       | Gaziantep (AOI01) |
| ShakeMap PGA      | destroyed vs no visible damage                | PGA only                  | 10773 |         5 |  0.797 | 0.691-0.915                       | Gaziantep (AOI01) |
| ShakeMap PGA      | destroyed vs no visible damage                | cells (500 m): full model | 10021 |       200 |  0.201 | top-10% hit 0.20 (Spearman shown) | Gaziantep (AOI01) |
| our shaking model | damaged or destroyed vs no visible damage     | full model                | 10775 |         7 |  0.748 | 0.427-0.897                       | Gaziantep (AOI01) |
| our shaking model | damaged or destroyed vs no visible damage     | PGA only                  | 10775 |         7 |  0.722 | 0.435-0.860                       | Gaziantep (AOI01) |
| our shaking model | damaged or destroyed vs no visible damage     | cells (500 m): full model | 10022 |       200 |  0.133 | top-10% hit 0.20 (Spearman shown) | Gaziantep (AOI01) |
| our shaking model | destroyed vs no visible damage                | full model                | 10773 |         5 |  0.787 | 0.489-0.974                       | Gaziantep (AOI01) |
| our shaking model | destroyed vs no visible damage                | PGA only                  | 10773 |         5 |  0.75  | 0.517-0.866                       | Gaziantep (AOI01) |
| our shaking model | destroyed vs no visible damage                | cells (500 m): full model | 10021 |       200 |  0.132 | top-10% hit 0.20 (Spearman shown) | Gaziantep (AOI01) |
| ShakeMap PGA      | destroyed vs damaged (severity among damaged) | full model                |   957 |       354 |  0.578 | 0.535-0.619                       | Kirikhan (AOI18)  |
| ShakeMap PGA      | destroyed vs damaged (severity among damaged) | PGA only                  |   957 |       354 |  0.557 | 0.495-0.612                       | Kirikhan (AOI18)  |
| ShakeMap PGA      | destroyed vs damaged (severity among damaged) | cells (500 m): full model |   840 |        28 |  0.13  | top-10% hit 0.50 (Spearman shown) | Kirikhan (AOI18)  |
| our shaking model | destroyed vs damaged (severity among damaged) | full model                |   957 |       354 |  0.499 | 0.457-0.551                       | Kirikhan (AOI18)  |
| our shaking model | destroyed vs damaged (severity among damaged) | PGA only                  |   957 |       354 |  0.432 | 0.384-0.495                       | Kirikhan (AOI18)  |
| our shaking model | destroyed vs damaged (severity among damaged) | cells (500 m): full model |   840 |        28 | -0.094 | top-10% hit 0.00 (Spearman shown) | Kirikhan (AOI18)  |
