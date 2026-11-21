# Roads, debris blockage, access, routes and dispatch

## Data
* Streets: OpenStreetMap drive network via OSMnx (`roads.py`), the area box padded by 2 km, cached as `data/roads/<area>.graphml`.
* Free-flow speeds: OSM `maxspeed` when present, otherwise `priors/road_speeds.csv` (**assumed** urban-India values by road class).
* Street widths: OSM `width`, else `lanes x 3.5 m`, else `priors/street_width.csv` (**assumed** defaults by road class).
* Facilities: OSM (Overpass) + Overture places, merged within 50 m with similar or missing names (`facilities.py`).

## Debris blockage (calibrated form, assumed weights)
Rule from Anelli, Mori & Vona (2020), *Fragility Curves of the Urban Road Network Based on the Debris Distributions of Interfering
Buildings*, Applied Sciences 10(4):1289 (equations 2-11), applied per street e of width W and building i:

* A street is **unusable for emergency vehicles when less than 3.5 m of pavement stays free** (their eq. 11). Debris that encroaches by
  at least thr = max(W - 3.5, 0) m blocks it (any encroachment blocks a street narrower than 3.5 m).
* Debris spread beyond the facade: **masonry** w = a(eps - 1) with eps = 1.228 + 0.07869 a/b + 0.05626 h/a (their eqs. 2-4; we take
  a = b = sqrt(footprint area), so w = 0.3067 a + 0.05626 h); largest at the collapse limit state, so P(complete damage) is used.
  **RC frame** w = one storey height (infill panels fall, eq. 7), reached at the life-safety state, so P(extensive or worse) is used.
  Each building's type mix (p_URM from the exposure) weights the two.
* The paper builds three debris graphs per building (all toward the street w, toward both sides w/2, away 0). It gives no weights, so
  **1/3 each is an assumption** (`DIR_W`); the 'away' case contributes nothing.
* Setback s = max(distance(footprint, centreline) - W/2, 0), encroachment = w - s, and
  P(street blocked) = 1 - prod_i (1 - P(i blocks)) over buildings within 30 m.

Consequences and limits (read before relying on it):
* A 3-storey masonry building (a = 10 m) spreads about 3.6 m of debris, so it **can** block a 6 m street from 1 m away. The earlier
  placeholder rule (reach 0.5 x height, 60% of width) could not; the earlier note that low-rise blockage was probably under-estimated was
  right.
* The paper adds up debris from both sides of the street; this implementation checks each building alone, so it **under-states blockage
  on narrow streets** with collapses on both sides.
* `a = sqrt(area)` ignores the footprint's orientation to the street. The equal direction weights are a guess.
* The rule comes from one small Italian town (Amatrice, Italian masonry); nothing here was checked against observed blockages in India.
  Yu & Gardoni (2022, Haiti 2010 data) was not read (paywalled); the Tokyo model of Kishimoto & Osaragi (2018) has the same product form
  P = 1 - prod(1 - f_i) but takes its constants from Japanese sources that were not accessible. Both were consulted for the form only.

## Access
`responders.access_mc`: 50 samples; each street is closed with its blockage probability (both directions together), each hospital
works with probability `functionality = 1 - P(extensive or worse)` of its building. One multi-source Dijkstra per sample
(scipy.sparse). Reported per 500 m cell, weighted by people: typical minutes and the chance of reaching a working hospital within
an hour; overall share within 30 minutes and share with no route. Travel times are free-flow (no congestion, no debris slowing
passable streets).

## Routes and dispatch
Routes use the expected cost `t * (1 + 20 * P(blocked))`, report free-flow time, expected time, the chance every street on the route
is open, and the risky segments (P >= 0.3). Dispatch assigns teams (spread over working facilities of the chosen kind) to the
eight priority cells by linear assignment on travel_time / people_at_risk, i.e. most people reached per minute. Facilities whose
building has a >50% chance of heavy damage are excluded as "may not be working". A planning aid, not an operational order.

## Casualties
`casualty.py`: Hazus structure (4 severities x damage cases, complete damage split into collapse / no collapse). Rates are the
**Hazus 6.1 Earthquake Technical Manual** indoor casualty rates per building type (tables 12-3 to 12-7) and collapse fractions
(table 12-8), in `priors/casualty_rates.csv`; they describe US building stock. Mapping: URM -> URML, RC_LOW -> C3L, RC_MID -> C3M
(a judgement; C3 is a concrete frame with unreinforced masonry infill, the closest match to Indian RC). Outdoor casualties are not
modelled. The USGS PAGER empirical model for India (theta 11.53, beta 0.14; OFR 2009-1136) is shown next to ours and a warning appears
when the two differ by more than a factor of 5. (An earlier draft used 14.05 / 0.185, which are Indonesia's values in the same table.)
