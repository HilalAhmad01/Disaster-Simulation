"""Aftershock, scenario snapshots, knock-on failures, team optimiser, situation report and assistant, and their API routes."""
import numpy as np, pytest
from synthetic import synthetic_exposure
import cascade, insights, live
from test_live import client  # noqa: F401  (fixture)
from test_responder_layer import layer, shapely_t  # noqa: F401  (fixture)

@pytest.fixture()
def model(tmp_path, monkeypatch):
    monkeypatch.setenv("VS30_TIF", str(tmp_path / "none.tif"))
    from simulate import Model
    return Model(synthetic_exposure(600), data_dir=tmp_path)

def _centre(m):
    return float(m.lat.mean()), float(m.lon.mean())

# ---------------------------------------------------------------- aftershock and snapshots

def test_aftershock_adds_damage_and_keeps_damage_states_ordered(model):
    lat, lon = _centre(model)
    first = model.run(lat, lon, 10, 6.8)["summary"]
    out = model.run_aftershock(lat + 0.02, lon, 10, 5.6)
    s = out["summary"]
    assert s["people_at_risk"] > first["people_at_risk"] and s["expected_collapses"] >= first["expected_collapses"]
    assert s["casualties"]["deaths"] >= first["casualties"]["deaths"]
    assert out["aftershock"]["added_collapses"] == s["expected_collapses"] - first["expected_collapses"]
    p = model.last[["p_slight", "p_moderate", "p_extensive", "p_collapse"]].to_numpy()
    assert (np.diff(p, axis=1) <= 1e-9).all()                       # P(slight) >= P(moderate) >= P(extensive) >= P(complete)
    again = model.run_aftershock(lat, lon + 0.02, 10, 5.4)["summary"]
    assert again["people_at_risk"] >= s["people_at_risk"]            # a second aftershock keeps accumulating

def test_aftershock_needs_an_earthquake_first(model):
    lat, lon = _centre(model)
    with pytest.raises(ValueError, match="Run an earthquake first"):
        model.run_aftershock(lat, lon, 10, 5.5)

def test_snapshot_and_restore_put_the_run_back(model):
    lat, lon = _centre(model)
    model.run(lat, lon, 10, 6.5)
    snap, rid, pr = model.snapshot_state(), model.run_id, model.summary["people_at_risk"]
    model.run(lat, lon, 10, 7.5)
    assert model.run_id != rid
    model.restore_state(snap)
    assert model.run_id == rid and model.summary["people_at_risk"] == pr and model.last is snap["last"]
    fresh = type(model)(synthetic_exposure(50), data_dir=None)
    s0 = fresh.snapshot_state()
    fresh.run(lat, lon, 10, 6.0)
    fresh.restore_state(s0)
    assert fresh.last is None and fresh.run_id == 0

# ---------------------------------------------------------------- knock-on failures

def test_cascade_is_repeatable_and_scales_with_damage(model):
    lat, lon = _centre(model)
    model.run(lat, lon, 8, 7.8)
    br = [{"lon": lon, "lat": lat, "name": "Mid bridge"}, {"lon": lon + 5, "lat": lat, "name": "Far away"}]
    a, b = cascade.compute(model, br), cascade.compute(model, br)
    assert a["supported"] and a == b                                  # seeded by the run number
    s = a["summary"]
    assert s["people_without_power"] > 0 and 0 <= s["share_without_power"] <= 1
    assert s["threatened"]["15"]["buildings"] <= s["threatened"]["120"]["buildings"]
    assert a["bridges"][0]["name"] == "Mid bridge" and a["bridges"][0]["p"] > 0 and a["bridges"][-1]["p"] == 0   # far bridge: outside the area
    model.run(lat, lon, 10, 5.0)
    assert cascade.compute(model)["summary"]["people_without_power"] < s["people_without_power"]

def test_cascade_not_supported_without_a_run_or_for_other_hazards(model):
    assert cascade.compute(model)["supported"] is False

# ---------------------------------------------------------------- team optimiser

def test_solve_kind_beats_one_by_one_when_the_oldest_call_grabs_the_wrong_unit():
    # call 0 (oldest, low priority) is near unit 0 and far-ish from unit 1; call 1 (critical) can only use unit 0 cheaply
    T = np.array([[1.0, 5.0], [2.0, 100.0]])
    w = np.array([1.0, 4.0])
    best, naive = live.solve_kind(T, w, np.array([0, 1]))
    assert best == {0: 1, 1: 0} and naive == {0: 0, 1: 1}
    cost = lambda pl: sum(w[i] * T[i, j] for i, j in pl.items())
    assert cost(best) < cost(naive)

def test_solve_kind_serves_the_urgent_call_when_units_are_short():
    T = np.array([[1.0], [30.0]])                                    # one unit, two calls
    best, naive = live.solve_kind(T, np.array([1.0, 4.0]), np.array([0, 1]))
    assert best == {1: 0} and naive == {0: 0}                        # the critical call gets the unit, not the oldest

def test_solve_kind_unreachable_calls_stay_unserved():
    best, naive = live.solve_kind(np.array([[np.inf], [3.0]]), np.array([4.0, 1.0]), np.array([0, 1]))
    assert best == {1: 0} and naive == {1: 0}

def test_plan_teams_on_the_toy_city(layer):
    L, m, (lon0, lat0) = layer
    m.run(lat0 + 0.004, lon0 + 0.004, 8, 7.0)
    calls = [{"id": i, "lat": lat0 + 0.0005 * i, "lon": lon0 + 0.0004 * i, "level": lv, "trapped": tr, "injured": inj, "created": float(i)}
             for i, (lv, tr, inj) in enumerate([("low", False, False), ("critical", True, True), ("high", True, False), ("medium", False, True)])]
    free = {0: 3, 1: 3, 2: 1, 3: 2}                                  # hospitals can send ambulances; one fire unit for two rescue calls
    res = live.plan_teams(L, calls, free)
    assert {x["sos"] for x in res["plan"]} | set(res["unserved"]) == {0, 1, 2, 3}
    rescue = [x for x in res["plan"] if x["kind"] == "fire_station"]
    assert len(rescue) == 1 and rescue[0]["sos"] == 1                # the single fire unit goes to the critical call
    assert 2 in res["unserved"]
    st = res["stats"]
    assert st["weighted_best"] <= st["weighted_naive"] and st["calls"] == 4 and st["served_best"] == 3
    assert all(x["eta_min"] > 0 for x in res["plan"])

def test_team_options_skip_stations_without_a_free_unit(layer):
    L, m, (lon0, lat0) = layer
    both = live.team_options(L, lat0, lon0, per_kind=2)
    only_h2 = live.team_options(L, lat0, lon0, per_kind=2, avail={1, 2, 3})        # hospital 0 has no unit left
    assert any(o["facility_id"] == 0 for o in both) and all(o["facility_id"] != 0 for o in only_h2)
    with pytest.raises(ValueError, match="no free unit"):
        live.team_route(L, "ambulance", lat0, lon0, facility_id=0, avail={1, 2, 3})

# ---------------------------------------------------------------- situation report and assistant (built-in writer)

FACTS = {"run": 3, "area": "Test City", "hazard": "earthquake", "scenario": {"mag": 7.0, "time_of_day": "night"},
         "headline": {"buildings": 1000, "people_in_area": 50000, "people_at_risk": 12000, "buildings_heavily_damaged": 300, "expected_collapses": 80,
                      "deaths": 900, "injured_serious": 2500, "injured_total": 6000, "peak_mmi": 8.1, "peak_pga_g": 0.4},
         "ranges_10_to_90_percent": {"people_at_risk": [8000, 17000], "deaths": [500, 1500]},
         "priorities": [{"rank": 1, "place": "Sector 5", "lon": 77.4, "lat": 28.5, "people_at_risk": 3000, "buildings": 200, "heavily_damaged": 90, "severe": 30, "km_from_epicentre": 2.0},
                        {"rank": 2, "place": "Old Town", "lon": 77.41, "lat": 28.51, "people_at_risk": 2000, "buildings": 150, "heavily_damaged": 60, "severe": 20, "km_from_epicentre": 3.0}],
         "hospitals": {"total": 4, "may_not_work": 1, "beds_working": 800, "beds_assumed": True, "serious_injuries_expected": 2500, "beds_per_serious_injury": 0.32,
                       "most_at_risk": [{"name": "City Hospital", "chance_building_heavily_damaged": 0.7, "may_not_work": True, "beds": 100, "lon": 77.39, "lat": 28.49}]},
         "access": {"share_within_30min": 0.6, "share_no_access": 0.1, "median_minutes": 22.0, "streets_likely_blocked": 120, "streets": 900},
         "live": {"phones_online": 5, "phones_marked_safe": 2, "sos_open": 7, "sos_critical_open": 3, "sos_teams_on_the_way": 1, "sos_closed": 0, "trapped_open": 4},
         "cascade": {"ignitions": 12, "gas_leaks": 30, "threatened": {"120": {"buildings": 400, "people": 3000}}, "people_without_power": 20000, "share_without_power": 0.4, "bridges": 3, "bridges_likely_closed": 1},
         "data_quality": {"note": "Fragility curves are uncalibrated placeholders."}}

def test_builtin_brief_uses_only_the_facts_and_lists_actions():
    t = insights.template_brief(FACTS)
    for needle in ("Sector 5", "Old Town", "City Hospital", "12,000", "8,000 to 17,000", "7 SOS open", "Do next", "uncalibrated"):
        assert needle in t
    assert t.index("Sector 5") < t.index("Old Town")
    assert len(insights.actions(FACTS)) >= 4

@pytest.mark.parametrize("q,needle", [("Which hospital is most at risk?", "City Hospital"), ("Where do I send the first team?", "Sector 5"),
                                      ("how many deaths", "900"), ("Which roads are blocked?", "120"), ("how many SOS calls are open?", "7 SOS"),
                                      ("will fires start?", "12 fires"), ("how many beds", "800"), ("can I trust this?", "uncalibrated")])
def test_builtin_assistant_answers_from_the_facts(q, needle, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = insights.answer(FACTS, q)
    assert r["source"] == "builtin" and needle in r["answer"]
    if "hospital" in q or "first team" in q:
        assert r["focus"] and r["focus"]["lon"] in (77.39, 77.4)

def test_assistant_suggests_questions_when_it_does_not_understand(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert "Which hospital" in insights.answer(FACTS, "zzz qqq")["answer"]

def test_claude_is_not_called_without_a_key(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert insights.ai_status()["claude"] is False
    assert insights.brief(FACTS)["source"] == "builtin"

def test_claude_failure_falls_back_to_the_builtin_text(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(insights, "ai_status", lambda: {"claude": True, "key": True, "package": True, "model": "x"})
    class Boom:
        def __init__(self, **k): pass
        class messages:
            @staticmethod
            def create(**k): raise RuntimeError("offline")
    import sys, types
    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Boom))
    r = insights.brief(FACTS)
    assert r["source"] == "builtin" and "RuntimeError" in r["note"]

def test_claude_text_is_used_when_the_call_works(monkeypatch):
    monkeypatch.setattr(insights, "ai_status", lambda: {"claude": True, "key": True, "package": True, "model": "x"})
    seen = {}
    class Msg:
        stop_reason = "end_turn"
        content = [type("B", (), {"type": "text", "text": "  Claude says hi  "})()]
    class Fake:
        def __init__(self, **k): pass
        class messages:
            @staticmethod
            def create(**k):
                seen.update(k)
                return Msg()
    import sys, types
    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Fake))
    r = insights.answer(FACTS, "Which hospital is most at risk?")
    assert r["source"] == "claude" and r["answer"] == "Claude says hi" and r["focus"]       # map focus still comes from the built-in matcher
    assert "City Hospital" in seen["messages"][0]["content"] and seen["output_config"] == {"effort": "low"}

# ---------------------------------------------------------------- API

def _run(client, **kw):
    m = client.app.state.st.model
    lat, lon = float(m.lat.mean()), float(m.lon.mean())
    body = {"lat": lat, "lon": lon, "depth_km": 8, "magnitude": 7.0, "uncertainty": False, **kw}
    r = client.post("/api/simulate", json=body)
    assert r.status_code == 200, r.text
    return r.json()

def test_presets_and_ai_status(client, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    ps = client.get("/api/presets").json()["presets"]
    assert {"gorkha-city", "turkey-city", "gorkha-real"} <= {p["id"] for p in ps}
    assert all(p["where"] in ("city", "real") and 3 <= p["magnitude"] <= 9.5 for p in ps)
    assert all("lat" in p and "lon" in p for p in ps if p["where"] == "real")
    assert client.get("/api/ai/status").json()["claude"] is False

def test_brief_and_assistant_routes(client, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert client.post("/api/brief/1").status_code == 409                      # nothing run yet
    run = _run(client)["run"]
    b = client.post(f"/api/brief/{run}").json()
    assert b["source"] == "builtin" and "Situation report" in b["text"] and "Do next" in b["text"]
    a = client.post(f"/api/assistant/{run}", json={"question": "How many people are at risk?"}).json()
    assert a["source"] == "builtin" and "people" in a["answer"]
    assert client.post(f"/api/assistant/{run + 5}", json={"question": "hello"}).status_code == 409

def test_aftershock_route_is_cumulative_and_needs_a_run(client):
    assert client.post("/api/aftershock", json={}).status_code == 409
    first = _run(client)
    out = client.post("/api/aftershock", json={}).json()
    assert out["run"] != first["run"] and out["summary"]["people_at_risk"] >= first["summary"]["people_at_risk"]
    assert out["aftershock"]["magnitude"] == 5.8 and out["summary"]["aftershock"] is True
    assert client.post("/api/aftershock", json={"magnitude": 5.0, "lat": 28.57, "lon": 77.33}).status_code == 200

def test_compare_leaves_the_map_run_untouched(client):
    first = _run(client)
    m = client.app.state.st.model
    rid = m.run_id
    lat, lon = float(m.lat.mean()), float(m.lon.mean())
    base = {"lat": lat, "lon": lon, "depth_km": 8, "time_of_day": "night"}
    r = client.post("/api/compare", json={"a": {**base, "magnitude": 6.0, "label": "M6"}, "b": {**base, "magnitude": 7.5, "label": "M7.5"}}).json()
    assert r["a"]["label"] == "M6" and r["b"]["summary"]["people_at_risk"] > r["a"]["summary"]["people_at_risk"]
    assert r["a"]["heat"]["features"] and len(r["bounds"]) == 4
    assert m.run_id == rid and m.summary["people_at_risk"] == first["summary"]["people_at_risk"]
    day = client.post("/api/compare", json={"a": {**base, "magnitude": 6.5, "time_of_day": "night"}, "b": {**base, "magnitude": 6.5, "time_of_day": "day"}}).json()
    assert day["a"]["scenario"]["time_of_day"] == "night" and day["b"]["scenario"]["time_of_day"] == "day"   # (the toy city has one occupancy for all times)

def test_cascade_and_confidence_routes(client):
    run = _run(client)["run"]
    c = client.get(f"/api/cascade/{run}").json()
    assert c["supported"] and c["summary"]["people_without_power"] > 0 and c["marks"] == [15, 30, 60, 120]
    assert client.get(f"/api/cascade/{run + 1}").status_code == 409
    assert client.get(f"/api/confmap/{run}").status_code == 409                # Monte Carlo not finished
    m = client.app.state.st.model
    n = len(m.g)
    rng = np.random.default_rng(1)
    mean = rng.random(n) * 0.3
    m.mc = {"run": run, "p_extensive_mean": mean, "p_extensive_p90": mean + rng.random(n) * 0.2, "p_collapse_mean": mean}
    g = client.get(f"/api/confmap/{run}").json()
    assert g["features"] and all(0 <= f["properties"]["u"] <= 1 for f in g["features"]) and g["features"][0]["geometry"]["type"] == "Polygon"

def test_live_speed_resources_and_optimise_routes(client):
    assert client.get("/api/live/resources").json()["loaded"] is False         # no responder layer in this toy app
    assert client.post("/api/live/speed", json={"speed": 10}).json() == {"speed": 10.0}
    assert client.post("/api/live/speed", json={"speed": 0}).status_code == 422
    assert client.post("/api/live/optimise", json={}).status_code == 409
    with client.websocket_connect("/ws/live?role=dashboard") as dash:
        assert dash.receive_json()["speed"] == 10.0
    assert client.post("/api/live/refuel").json() == {"ok": True}
