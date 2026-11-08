"""End to end: the Flyway replay -> ingest API -> tick -> /map and /hotspots, plus spoof checks.

The wind input is a pull connector (preset open_meteo) and is exercised once the connector engine
lands; here only the tag tracks flow, through the real API on a manual SimClock.
"""

import math

import pytest
from sim import flyway
from sim.common import LocalFrame, load_spec
from test_e2e_campus_alert import ApiPlatform

from app.core import clock as clock_mod
from app.core.clock import SimClock
from app.engine.trust import suspicious_users_by_input

T0 = 1_800_000_000.0
TICK_S = 60
RAW = load_spec("flyway")
FRAME = LocalFrame.from_spec(RAW)
NEAR_M = 150.0


@pytest.fixture(scope="module")
def run():
    clock = SimClock(start=T0)
    prev = clock_mod.set_clock(clock)
    try:
        p = ApiPlatform(RAW, clock)
        sc = flyway.load_replay(flyway.DEFAULT_REPLAY)
        posts, statuses, i = sc.posts, [], 0
        end = int(flyway.DURATION_S)
        for k in range(0, end + 1, TICK_S):
            clock.set(T0 + k)
            while i < len(posts) and posts[i].sim_t <= k:
                statuses.append(p.post_event(p.app_id, posts[i].input_id, posts[i].stamped(T0), p.ingest_key))
                i += 1
            p.engine.tick_once()
        yield {
            "p": p,
            "sc": sc,
            "statuses": statuses,
            "map": p.get_map(p.app_id, p.authority_key),
            "public_map": p.get_map(p.app_id),
            "hotspots": p.get_hotspots(p.app_id, p.authority_key)["hotspots"],
            "public_hotspots": p.get_hotspots(p.app_id)["hotspots"],
            "end": end,
        }
    finally:
        clock_mod.set_clock(prev)


def _dist(a_lat, a_lon, b_lat, b_lon) -> float:
    return math.dist(FRAME.to_xy(a_lat, a_lon), FRAME.to_xy(b_lat, b_lon))


def test_replay_is_deterministic_and_matches_generate():
    sc = flyway.load_replay(flyway.DEFAULT_REPLAY)
    fresh = flyway.generate(sc.truth["seed"])
    assert [(p.sim_t, p.body) for p in fresh.posts] == [(p.sim_t, p.body) for p in sc.posts]
    b = RAW["grid"]["bbox"]
    assert all(
        b["min_lat"] <= p.body["lat"] <= b["max_lat"] and b["min_lon"] <= p.body["lon"] <= b["max_lon"]
        for p in sc.posts
    )


def test_every_ping_is_accepted(run):
    assert run["statuses"] and set(run["statuses"]) == {202}


def test_real_birds_are_not_flagged_as_spoofers(run):
    """Fast, straight, steady flight is normal here: the spec's plausibility says so."""
    p = run["p"]
    inputs = [i for i in p.runtime.spec.inputs if i.kind == "push"]
    events = {i.id: p.engine.source.events(p.app_id, i.id, T0 - 1, T0 + run["end"]) for i in inputs}
    flagged = [s.user_id for s in suspicious_users_by_input(events, {i.id: i.plausibility for i in inputs}) if s.flags]
    assert flagged == []


def test_hotspots_sit_on_the_occupied_stopovers(run):
    sc, end = run["sc"], run["end"]
    occupied = {b["to"] for b in sc.truth["birds"] if b["arrive_s"] <= end - 600}
    occupied |= {b["from"] for b in sc.truth["birds"] if b["leave_s"] >= end}
    sites = {s["name"]: s for s in sc.truth["stopovers"]}
    hs = run["hotspots"]
    assert hs
    for name in occupied:
        s = sites[name]
        assert min(_dist(h["lat"], h["lon"], s["lat"], s["lon"]) for h in hs) < NEAR_M, name
    for h in hs:  # no hotspot away from every stopover
        assert min(_dist(h["lat"], h["lon"], s["lat"], s["lon"]) for s in sites.values()) < NEAR_M


def test_public_sees_only_the_vector_field(run):
    public = run["public_map"]["layers"]
    assert set(public) == {"wind.field"}  # score derives from private tag layers, so it is authority too
    assert public["wind.field"]["features"] == []  # no wind data until the connector engine lands
    assert {"score", "tags.dwell", "tags.density"} <= set(run["map"]["layers"])
    assert run["public_hotspots"] == []  # hotspots built from authority-only tag layers stay authority
