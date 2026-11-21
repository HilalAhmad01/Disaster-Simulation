# Roads, debris blockage, access, routes and dispatch

## Data
* Streets: OpenStreetMap drive network via OSMnx (`roads.py`), the area box padded by 2 km, cached as `data/roads/<area>.graphml`.
* Free-flow speeds: OSM `maxspeed` when present, otherwise `priors/road_speeds.csv` (**assumed** urban-India values by road class).
* Street widths: OSM `width`, else `lanes x 3.5 m`, else `priors/street_width.csv` (**assumed** defaults by road class).
* Facilities: OSM (Overpass) + Overture places, merged within 50 m with similar or missing names (`facilities.py`).

## Debris blockage (placeholders, NOT calibrated)
Form from MASTER_PLAN 5.3. For a street of width W and a building i with height H_i = storeys x 3 m:

    D_i  = 0.5 * H_i                         debris reach
    s_i  = max(distance(footprint, centreline) - W/2, 0)
    P(i blocks) = P_collapse_i * [D_i >= s_i + 0.6 W]  +  0.1 * P_extensive_i * [D_i/2 >= s_i + 0.6 W]
    P(street blocked) = 1 - prod_i (1 - P(i blocks))          buildings within 30 m of the street

The constants (reach 0.5, 0.6 of the width, partial factor 0.1) are placeholders. The literature the plan points to for calibrating
them (Yu & Gardoni 2022 on Haiti 2010; the 2020 Applied Sciences remaining-free-width model; the 2018 ISPRS Tokyo model) was not
read in full, so no constant here comes from those papers. Consequence of the form: a 3-storey house cannot block a 6 m street
(reach 4.5 m < 6.6 m needed), while a 6-storey block can; real collapse debris often spreads farther, so this is probably
optimistic about low-rise blockage. The API and UI mark the constants as uncalibrated.

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
`casualty.py`: Hazus-style structure (4 severities x damage cases, complete damage split into collapse / no collapse), rates in
`priors/casualty_rates.csv` and `casualty_params.json`. **All values are UNVERIFIED placeholders** (the Hazus manual could not be
downloaded). The USGS PAGER empirical model for India (theta 14.05, beta 0.185 from memory, also unverified) is shown next to ours and
a warning appears when the two differ by more than a factor of 5.
