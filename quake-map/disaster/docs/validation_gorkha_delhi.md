# 2015 Gorkha earthquake in Delhi-NCR: model versus 'Did You Feel It?' (plan 2.3)

M7.8 thrust, epicentre 28.2305N 84.7314E (region used: **himalaya**), USGS finite-fault rupture for distance. 14 DYFI cells of 10 km in lon 76.6-77.8, lat 28.2-29.1 with >= 5 responses (191 responses). Vs30: USGS global Vs30 map.

Rjb to the cells: 687-737 km (beyond the models' validity: **True**). Vs30 224-484 m/s.

| quantity | response-weighted mean |
|---|---|
| DYFI intensity (MMI) | 4.00 (range 3.5-4.9) |
| Mix: PGA (g) | 0.0002 |
| Mix: MMI from median PGA | 1.00 |
| Mix: MMI at median +1 sigma / -1 sigma (sigma_lnPGA 0.67) | 1.20 / 1.00 |
| BA08 alone: PGA (g) / MMI | 0.0000 / 1.00 |
| BSSA14 alone: PGA (g) / MMI | 0.0002 / 1.00 |
| SHARMA09 alone: PGA (g) / MMI | 0.0093 / 3.27 |

**Difference (mix MMI - DYFI MMI) = -3.00 intensity units: outside one intensity unit: the mix under-predicts far-field shaking (see below).**

Mix weights at this distance: BA08 0.00, BSSA14 1.00, SHARMA09 0.00. Beyond 400 km the mix is BSSA14 alone, which has an anelastic term and decays fastest; SHARMA09 has no such term and decays slowest. Selvan & Sinha (2024) also find BSSA14 under-predicting on the Indo-Gangetic Plain.

What this does and does not affect: PGA of a few thousandths of g damages no building, so damage and casualty results for scenarios this far away are unaffected. The PGA/MMI maps for distant epicentres are not reliable; the run summary already flags `beyond_validity` above 400 km. The weights were not re-tuned to this one event (that would be fitting to a single data point); a far-field Himalayan model would be the proper fix.

Caveats: DYFI is self-reported and biased towards people in tall buildings and towards felt events; the conversion PGA -> MMI at PGA of a few thousandths of g is the weakest part of Worden et al. (2012); all models are used far outside their stated distance range; one event only.
