"""One-click scenarios: real earthquakes replayed at their real epicentre or placed on the loaded city, and a few demonstration scenarios.
Source parameters are rounded values from USGS catalogues and finite-fault solutions (see docs/validation_*.md). Observed outcomes are
given only as context: a transplanted quake on another city is NOT expected to reproduce them."""

PRESETS = [
    {"id": "gorkha-city", "label": "Gorkha 2015 quake on this city", "group": "Real earthquakes, placed on your city",
     "where": "city", "magnitude": 7.8, "depth_km": 8, "strike": 295, "dip": 10, "rake": 108, "time_of_day": "night",
     "blurb": "M7.8 Himalayan thrust, the size and style of the 2015 Nepal earthquake, centred on the loaded city. Shows what a quake that large would do here.",
     "observed": "Observed in Nepal (not this city): about 9,000 deaths and 22,000 injured."},
    {"id": "turkey-city", "label": "Türkiye 2023 quake on this city", "group": "Real earthquakes, placed on your city",
     "where": "city", "magnitude": 7.8, "depth_km": 10, "strike": 228, "dip": 89, "rake": -1, "time_of_day": "night",
     "blurb": "M7.8 strike-slip rupture like the Kahramanmaraş earthquake, with the same fault direction, centred on the loaded city.",
     "observed": "Observed in Türkiye (not this city): more than 50,000 deaths, mostly collapsed older concrete buildings."},
    {"id": "bhuj-city", "label": "Bhuj 2001 quake on this city", "group": "Real earthquakes, placed on your city",
     "where": "city", "magnitude": 7.7, "depth_km": 16, "time_of_day": "day",
     "blurb": "M7.7 intraplate earthquake like Bhuj, Gujarat, on the loaded city, during the working day.",
     "observed": "Observed around Bhuj (not this city): about 20,000 deaths."},
    {"id": "gorkha-real", "label": "Gorkha 2015, at its real epicentre", "group": "Replay at the real place",
     "where": "real", "lat": 28.2305, "lon": 84.7314, "magnitude": 7.8, "depth_km": 8, "strike": 295, "dip": 10, "rake": 108, "time_of_day": "day",
     "blurb": "The real epicentre in Nepal. A city in Delhi-NCR sits about 700 km away and feels it weakly: a check that distance does not create false alarms.",
     "observed": "Delhi-NCR felt about intensity IV; no significant damage. See docs/validation_gorkha_delhi.md."},
    {"id": "himalaya-m8", "label": "Great Himalayan M8.0 (scenario)", "group": "Demonstration scenarios",
     "where": "real", "lat": 30.4, "lon": 79.2, "magnitude": 8.0, "depth_km": 15, "strike": 295, "dip": 10, "rake": 90, "time_of_day": "night",
     "blurb": "A scenario in the central Himalayan seismic gap. Built to stress-test a city far away. Not a forecast.", "observed": None},
    {"id": "local-m60", "label": "Moderate local quake (M6.0)", "group": "Demonstration scenarios",
     "where": "city", "magnitude": 6.0, "depth_km": 10, "time_of_day": "night",
     "blurb": "A smaller earthquake under the city: damage concentrated in weak older buildings.", "observed": None},
]

def public():
    return PRESETS
