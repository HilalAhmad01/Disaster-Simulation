"""GET /api/kindle/feed: the one document the Kindle dashboard polls."""
import json
import pytest
from fastapi.testclient import TestClient
import dronefix
import kindle_feed


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    d = tmp_path_factory.mktemp("kindle")
    city = dronefix.make_city(mp, d)
    import app as appmod
    app = appmod.create_app(d)
    with TestClient(app) as c:
        st = c.app.state.st
        st.model, st.layer = city["model"], city["layer"]
        yield c, st
    mp.undo()


def test_feed_with_no_run_and_no_calls_is_valid_and_empty(env):
    c, st = env
    assert kindle_feed.run_block(None) is None                                    # nothing loaded or run yet
    f = c.get("/api/kindle/feed").json()
    assert f["sos"]["total"] == 0 and f["sos"]["calls"] == [] and f["run"]["magnitude"] == 7.4      # the fixture city has run one quake


def test_feed_after_a_run_has_the_headline_numbers_and_ranked_areas(env):
    c, st = env
    st.model.run(28.5, 77.40, 10.0, 6.8)
    r = c.get("/api/kindle/feed").json()["run"]
    assert r["id"] >= 1 and r["hazard"] == "earthquake" and r["magnitude"] == 6.8
    assert r["people_at_risk"] > 0 and r["expected_collapses"] >= 0 and r["people_total"] > 0
    assert 1 <= len(r["hoods"]) <= 5 and r["hoods"][0]["rank"] == 1
    assert all(h["dir"] in kindle_feed.COMPASS for h in r["hoods"])
    assert [h["risk"] for h in r["hoods"]] == sorted((h["risk"] for h in r["hoods"]), reverse=True)


def test_feed_sos_counts_order_and_privacy(env):
    c, st = env
    c.post("/api/live/simulate", json={"n": 6})
    f = c.get("/api/kindle/feed").json()["sos"]
    assert f["total"] == 6 and f["open"] == 6 and f["new"] == 6 and f["last_id"] >= 6
    ranks = [kindle_feed.LEVEL_RANK[x["level"]] for x in f["calls"]]
    assert ranks == sorted(ranks, reverse=True)                                  # most urgent first
    text = json.dumps(f)
    assert "sim-" not in text and '"device"' not in text and '"lat"' not in text and '"lon"' not in text      # no device id, no exact position
    assert all(x["age_s"] >= 0 for x in f["calls"])
    c.post("/api/live/clear")
    assert c.get("/api/kindle/feed").json()["sos"]["total"] == 0


def test_bearing_points_the_right_way():
    assert kindle_feed.bearing(28.5, 77.4, 28.6, 77.4) == "N"
    assert kindle_feed.bearing(28.5, 77.4, 28.5, 77.5) == "E"
    assert kindle_feed.bearing(28.5, 77.4, 28.4, 77.3) == "SW"
