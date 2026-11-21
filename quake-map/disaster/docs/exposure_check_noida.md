# Exposure check: noida

Buildings with a `num_floors` label: 994 (of 483,358). Google height usable (>= 2 m, spread <= 6 m): 762.

## Storey height scan

|   storey_m |   bias_storeys |   exact |   within_1 |
|-----------:|---------------:|--------:|-----------:|
|        2.8 |         -0.454 |   0.272 |      0.688 |
|        2.9 |         -0.614 |   0.247 |      0.68  |
|        3   |         -0.776 |   0.224 |      0.661 |
|        3.1 |         -0.94  |   0.206 |      0.64  |
|        3.2 |         -1.081 |   0.19  |      0.604 |
|        3.3 |         -1.218 |   0.173 |      0.577 |
|        3.4 |         -1.325 |   0.168 |      0.542 |
|        3.5 |         -1.448 |   0.159 |      0.504 |

### Bias (estimated - labelled storeys) by labelled height group

|   storey_m |   1-2 floors |   3-4 floors |   5-6 floors |   7-40 floors |
|-----------:|-------------:|-------------:|-------------:|--------------:|
|        2.6 |         1.68 |         0.23 |        -0.39 |         -0.65 |
|        2.8 |         1.44 |        -0.03 |        -0.74 |         -1.49 |
|        3   |         1.29 |        -0.28 |        -1.02 |         -2.15 |
|        3.2 |         1.07 |        -0.52 |        -1.28 |         -2.71 |

Median Google height per labelled group (n, metres): 1-2 floors: n=68, 8.5 m, 3-4 floors: n=236, 10.2 m, 5-6 floors: n=327, 12.3 m, 7-40 floors: n=131, 22.7 m. The height barely rises between 3 and 6 labelled floors, so the residual bias is the height product compressing heights (low-rise read high, tall read low), not something a single storey height can remove.

Smallest absolute bias at **2.8 m per storey** (bias -0.454 storeys, exact 27.2%, within +/-1 68.8%).

## Confusion matrix at the configured 2.8 m

| labelled storeys   |   [1, 2) |   [2, 3) |   [3, 4) |   [4, 5) |   [5, 6) |   [6, 8) |   [8, 12) |   [12, 41) |
|:-------------------|---------:|---------:|---------:|---------:|---------:|---------:|----------:|-----------:|
| [1, 2)             |        4 |        7 |        5 |        3 |        1 |        0 |         0 |          1 |
| [2, 3)             |        5 |        9 |       16 |       10 |        4 |        3 |         0 |          0 |
| [3, 4)             |        2 |        7 |       29 |       44 |        9 |        1 |         0 |          0 |
| [4, 5)             |       13 |       29 |       20 |       50 |       22 |        8 |         1 |          1 |
| [5, 6)             |        4 |       10 |       27 |      111 |       84 |       11 |         9 |          0 |
| [6, 8)             |        1 |        1 |        7 |       21 |       26 |       21 |        11 |          0 |
| [8, 12)            |        0 |        0 |        1 |        5 |        1 |       19 |        48 |          8 |
| [12, 41)           |        2 |        1 |        3 |        0 |        0 |        0 |         5 |         21 |

Exact 27.2%; within +/-1 storey 68.8%; bias -0.454 storeys.

Labelled buildings lean towards tall apartment blocks (median floors 5), so these numbers describe the labelled subset, not the unlabelled majority.

## Population

Night population in the exposure: 4,799,029; GHS-POP total for the area: 4,905,499 (-2.2%). Typical-occupancy capacity (v1 method): 8,514,503 (1.74x GHS-POP).

1 km cells: 681 (118 used the neighbourhood median factor).

## Where storeys come from

|                |   buildings |
|:---------------|------------:|
| google         |      322555 |
| guess_unstable |      159778 |
| label          |         999 |
| label_height   |          26 |
