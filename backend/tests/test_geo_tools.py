"""Overpass / Nominatim client behaviour found in the live check (#219). No network: injected fetchers,
plus Nominatim responses recorded live (tests/fixtures/geo_live, labelled in their _meta)."""

import json
from pathlib import Path

import pytest

from app.tools import geo_tools

FIX = Path(__file__).resolve().parent / "fixtures" / "geo_live"
BBOX = (7.437, 46.941, 7.458, 46.955)


def _recorded(name: str) -> list[dict]:
    d = json.loads((FIX / name).read_text())
    assert d["_meta"]["recorded"].startswith("live")
    return d["results"]


# -- Overpass: the public instance has outages -----------------------------------------------------


def test_overpass_skips_the_whole_service_after_failures_in_a_row():
    """Live, every lookup timed out during an outage; per-place caching alone still made each NEW place
    wait out its own timeout. After HOST_FAILS_TO_OPEN failures in a row, all places fail fast."""
    now = [0.0]
    calls = []
    down = [True]

    def fetch(query):
        calls.append(query)
        if down[0]:
            raise TimeoutError("read timed out")
        return {"elements": []}

    c = geo_tools.OverpassClient(fetch=fetch, min_interval_s=0, failure_ttl_s=60, monotonic=lambda: now[0])
    for k in range(geo_tools.HOST_FAILS_TO_OPEN):
        with pytest.raises(TimeoutError):
            c.features_near(10.0 + k, 20.0, 50)
    with pytest.raises(geo_tools.OverpassUnavailable, match="failures in a row"):
        c.features_near(30.0, 40.0, 50)  # a new place, but no request is made
    assert len(calls) == geo_tools.HOST_FAILS_TO_OPEN
    now[0] += 61
    down[0] = False
    assert c.features_near(30.0, 40.0, 50) == []  # service retried after the pause, and it's back
    with pytest.raises(TimeoutError):  # one later failure doesn't reopen the breaker: success reset it
        down[0] = True
        c.features_near(50.0, 60.0, 50)
    down[0] = False
    assert c.features_near(51.0, 60.0, 50) == []


def test_overpass_timeout_budget_covers_connect_plus_read():
    """httpx applies `timeout=` per phase, so 8 s meant up to ~9.7 s live. The budget is now split."""
    assert geo_tools.CONNECT_TIMEOUT_S < geo_tools.FETCH_TIMEOUT_S <= 8.0


# -- Nominatim: a bounded search returns anything whose text matches ---------------------------------


def test_a_shop_named_after_a_far_away_city_is_not_that_city():
    """Live: 'Copenhagen' bounded to a box in another city returned only shops ('Flying Tiger
    Copenhagen', ...). The old code took the first result and placed a document on that shop."""
    results = _recorded("nominatim_copenhagen_in_bern_box.json")
    assert results and all(r["category"] == "shop" for r in results)
    assert geo_tools.best_match(results, "Copenhagen") is None
    c = geo_tools.NominatimClient(search=lambda p: results, min_interval_s=0)
    assert c.geocode("Copenhagen", BBOX) is None


def test_exact_name_wins_and_real_places_still_resolve():
    square = _recorded("nominatim_bundesplatz.json")
    c = geo_tools.NominatimClient(search=lambda p: square, min_interval_s=0)
    hit = c.geocode("Bundesplatz", BBOX)
    assert hit is not None and hit["lat"] == pytest.approx(46.947, abs=1e-3)
    shop = {"name": "Bundesplatz Kiosk", "category": "shop", "lat": "1", "lon": "2"}
    # an exact name match is preferred over an earlier partial one
    assert geo_tools.best_match([shop, *square], "Bundesplatz, Bern")["name"] == "Bundesplatz"
    # with no exact name, the first non-commercial result is taken
    road = {"name": "Bundesgasse", "category": "highway", "lat": "3", "lon": "4"}
    assert geo_tools.best_match([shop, road], "Bundes area")["name"] == "Bundesgasse"


def test_geocode_asks_for_several_candidates():
    seen = []
    c = geo_tools.NominatimClient(search=lambda p: seen.append(p) or [], min_interval_s=0)
    c.geocode("Somewhere", BBOX)
    assert seen[0]["limit"] == str(geo_tools.NOMINATIM_LIMIT) and int(seen[0]["limit"]) > 1
    assert seen[0]["bounded"] == "1"
