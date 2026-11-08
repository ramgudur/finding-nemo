import json

import numpy as np
import pytest

from app.core.clock import SimClock
from app.core.db import Db
from app.core.models import AppSpec, Candidate
from app.engine.tick import InMemoryEventSource, TickEngine
from app.tools import geo_tools, registry
from app.tools.context import CandidateStore, ToolContext

T0 = 1_000_000.0
BBOX = {"min_lat": 45.0, "min_lon": 10.0, "max_lat": 45.0045, "max_lon": 10.00635}

# A recorded-style Overpass response (shape of the real API; no network in tests).
OVERPASS_RESPONSE = {
    "elements": [
        {"type": "node", "id": 1, "lat": 45.00201, "lon": 10.00301, "tags": {"name": "North Pond", "natural": "water"}},
        {"type": "way", "id": 2, "center": {"lat": 45.0030, "lon": 10.0040}, "tags": {"leisure": "park"}},
        {"type": "node", "id": 3, "lat": 45.0020, "lon": 10.0030, "tags": {"note": "untracked key only"}},
    ]
}


def make_spec(mode="auto", public_layers=()):
    return AppSpec.model_validate(
        {
            "app_id": "poi-app",
            "intent": "find places worth naming",
            "status": "active",
            "grid": {"cell_m": 10, "bbox": BBOX},
            "inputs": [
                {
                    "id": "pings",
                    "kind": "push",
                    "shape": "point",
                    "tier": "flexible",
                    "meaning": "a report",
                    "fields": [{"name": "level", "type": "number", "required": False}],
                    "operators": [{"op": "density", "weight": 1.0, "half_life_s": 60, "sigma_m": 15}],
                }
            ],
            "outputs": {
                "layers": [{"id": lid, "visibility": "public"} for lid in public_layers],
                "poi_labels": {
                    "mode": mode,
                    "min_confidence": 0.6,
                    "label_set": [
                        {"label": "gathering spot", "destination": True},
                        {"label": "busy area"},
                        {"label": "private group", "sensitive": True},
                    ],
                },
            },
        }
    )


@pytest.fixture
def env(monkeypatch):
    calls = []

    def fake_fetch(query):
        calls.append(query)
        return OVERPASS_RESPONSE

    monkeypatch.setattr(geo_tools, "OVERPASS", geo_tools.OverpassClient(fetch=fake_fetch, min_interval_s=0))
    state = {}

    def build(spec=None):
        clock = SimClock(T0)
        db = Db(":memory:")
        src = InMemoryEventSource()
        eng = TickEngine(src, clock=clock)
        rt = eng.add_app(spec or make_spec())
        lat, lon = rt.grid.to_latlon(240.0, 222.0)
        recs = [
            {"user_id": f"u{i}", "t": T0 - 30 + i, "lat": float(lat), "lon": float(lon), "level": 2.0 + i}
            for i in range(12)
        ]
        src.add("poi-app", "pings", recs)
        eng.tick_once()
        cand = Candidate(
            id="c1",
            lat=float(lat),
            lon=float(lon),
            radius_m=20,
            source="dwell_clusters",
            stats={"members": 12.0},
            contributing_layers=["pings.density"],
            persisted_ticks=4,
        )
        rt.last.candidates = [cand]
        state.update(clock=clock, db=db, eng=eng, rt=rt, src=src)

        def ctx(agent="poi_labeler"):
            return ToolContext("poi-app", db, eng, agent=agent, clock=clock, candidates=CandidateStore())

        return ctx

    state["fetch_calls"] = calls
    return build, state


def call(ctx, name, **args):
    return registry.call_tool(ctx, name, args)


def test_get_candidates_and_label_set(env):
    build, _ = env
    ctx = build()
    r = call(ctx(), "get_poi_candidates")
    assert r["ok"], r
    res = r["result"]
    assert [c["id"] for c in res["candidates"]] == ["c1"]
    assert {lb["label"] for lb in res["label_set"]} == {"gathering spot", "busy area", "private group"}
    assert res["mode"] == "auto" and res["candidates"][0]["label"] is None


def test_create_poi_copies_position_from_candidate_and_writes_change(env):
    build, st = env
    ctx = build()
    r = call(ctx(), "create_poi", candidate_id="c1", label="gathering spot", confidence=0.8,
             evidence=[{"kind": "layer_stats", "text": "dense and persistent"}])  # fmt: skip
    assert r["ok"], r
    poi = st["db"].get_poi("poi-app", r["result"]["poi_id"])
    cand = st["rt"].last.candidates[0]
    assert (poi.lat, poi.lon, poi.radius_m) == (cand.lat, cand.lon, cand.radius_m)
    assert poi.visibility == "public"  # destination label: the public route already reveals it
    changes = st["db"].list_changes("poi-app", kind="poi")
    assert len(changes) == 1 and changes[0].before is None and changes[0].after["label"] == "gathering spot"
    # coordinates can't be passed in at all
    bad = call(ctx(), "create_poi", candidate_id="c1", label="gathering spot", confidence=0.8, lat=1.0, lon=2.0)
    assert not bad["ok"] and "invalid arguments" in bad["error"]


def test_visibility_public_only_for_non_sensitive_with_public_layers(env):
    build, st = env
    ctx = build(make_spec(public_layers=["pings.density"]))
    r = call(ctx(), "create_poi", candidate_id="c1", label="gathering spot", confidence=0.8)
    assert r["result"]["visibility"] == "public"
    call(ctx(), "retire_poi", poi_id=r["result"]["poi_id"], reason="moved")
    r = call(ctx(), "create_poi", candidate_id="c1", label="private group", confidence=0.8)
    assert r["result"]["visibility"] == "authority"  # sensitive is always authority


def test_create_poi_guards(env):
    build, _ = env
    ctx = build()
    r = call(ctx(), "create_poi", candidate_id="made-up", label="gathering spot", confidence=0.9)
    assert not r["ok"] and "code-made candidates" in r["error"]
    r = call(ctx(), "create_poi", candidate_id="c1", label="new kind of place", confidence=0.9)
    assert not r["ok"] and "annotate" in r["error"]
    r = call(ctx(), "create_poi", candidate_id="c1", label="gathering spot", confidence=0.3)
    assert not r["ok"] and "min_confidence" in r["error"]
    assert call(ctx(), "create_poi", candidate_id="c1", label="gathering spot", confidence=0.9)["ok"]
    r = call(ctx(), "create_poi", candidate_id="c1", label="gathering spot", confidence=0.9)
    assert not r["ok"] and "update_poi" in r["error"]
    assert not call(ctx(agent="critic"), "create_poi", candidate_id="c1", label="gathering spot", confidence=0.9)["ok"]


def test_mode_off_blocks_writes(env):
    build, _ = env
    ctx = build(make_spec(mode="off"))
    r = call(ctx(), "create_poi", candidate_id="c1", label="gathering spot", confidence=0.9)
    assert not r["ok"] and "off" in r["error"]


def test_update_and_retire(env):
    build, st = env
    ctx = build(make_spec(public_layers=["pings.density"]))
    pid = call(ctx(), "create_poi", candidate_id="c1", label="gathering spot", confidence=0.8)["result"]["poi_id"]
    st["clock"].advance(5)
    # the candidate moved: the POI follows it; relabeling to a sensitive label makes it authority
    moved = st["rt"].last.candidates[0].model_copy(update={"lat": st["rt"].last.candidates[0].lat + 0.0001})
    st["rt"].last.candidates = [moved]
    r = call(ctx(), "update_poi", poi_id=pid, label="private group")
    assert r["ok"] and r["result"]["visibility"] == "authority"
    assert st["db"].get_poi("poi-app", pid).lat == moved.lat
    assert not call(ctx(), "update_poi", poi_id=pid, label="nonsense")["ok"]
    assert not call(ctx(), "retire_poi", poi_id=pid, reason="")["ok"]
    assert call(ctx(), "retire_poi", poi_id=pid, reason="the group left")["ok"]
    assert "retired" in call(ctx(), "update_poi", poi_id=pid, confidence=0.9)["error"]
    kinds = [c.after["status"] for c in st["db"].list_changes("poi-app", kind="poi")]
    assert kinds == ["active", "active", "retired"]


def test_osm_features_cached_compact_and_capped(env):
    build, st = env
    ctx = build()
    r1 = call(ctx(), "osm_features_near", candidate_id="c1", radius_m=50)
    r2 = call(ctx(), "osm_features_near", candidate_id="c1", radius_m=50)
    assert r1["ok"] and r1 == r2
    assert len(st["fetch_calls"]) == 1  # cached
    feats = r1["result"]["features"]
    assert [f["tags"] for f in feats] == [{"name": "North Pond", "natural": "water"}, {"leisure": "park"}]
    assert feats[0]["distance_m"] <= feats[1]["distance_m"]
    assert "id" not in json.dumps(feats)
    assert not call(ctx(), "osm_features_near", candidate_id="c1", radius_m=5000)["ok"]
    assert not call(ctx(), "osm_features_near", candidate_id="nope")["ok"]


def test_input_values_and_time_pattern_are_aggregates(env):
    build, _ = env
    ctx = build()
    r = call(ctx(), "input_values_at", candidate_id="c1", window_s=600)
    assert r["ok"], r
    p = r["result"]["inputs"]["pings"]
    assert p["events"] == 12 and p["distinct_sources"] == 12
    assert p["fields"]["level"] == {"mean": 7.5, "min": 2.0, "max": 13.0, "n": 12}
    t = call(ctx(), "get_time_pattern", candidate_id="c1", window_s=120)["result"]["inputs"]["pings"]
    assert sum(t["counts"]) == 12 and len(t["counts"]) == 12
    text = json.dumps([r, t])
    assert '"u1"' not in text and "user" not in text  # no user ids


def test_layer_stats_at(env):
    build, _ = env
    ctx = build()
    r = call(ctx(), "get_layer_stats_at", candidate_id="c1")
    assert r["ok"], r
    assert r["result"]["layers"]["pings.density"]["max"] > 0


def test_pois_never_feed_back_into_scores(env):
    build, st = env
    ctx = build()
    before = st["eng"].tick_once()[0].score.copy()
    call(ctx(), "create_poi", candidate_id="c1", label="gathering spot", confidence=0.9)
    after = st["eng"].tick_once()[0].score
    np.testing.assert_array_equal(before, after)


def test_destination_labels_are_public_even_from_private_layers(env):
    """Lead decision (#113): a destination POI is public; the public route already reveals it."""
    build, st = env
    ctx = build()  # no public layers at all
    r = call(ctx(), "create_poi", candidate_id="c1", label="gathering spot", confidence=0.8)
    assert r["result"]["visibility"] == "public"
    pid = r["result"]["poi_id"]
    st["rt"].last.candidates = []  # candidate gone: relabel is judged by the label alone
    r = call(ctx(), "update_poi", poi_id=pid, label="busy area")
    assert r["ok"] and r["result"]["visibility"] == "authority"
    r = call(ctx(), "update_poi", poi_id=pid, label="gathering spot")
    assert r["result"]["visibility"] == "public"
    r = call(ctx(), "update_poi", poi_id=pid, label="private group")
    assert r["result"]["visibility"] == "authority"


def test_non_destination_label_follows_layer_rule(env):
    build, _ = env
    r = call(build()(), "create_poi", candidate_id="c1", label="busy area", confidence=0.8)
    assert r["result"]["visibility"] == "authority"
    ctx = build(make_spec(public_layers=["pings.density"]))
    r = call(ctx(), "create_poi", candidate_id="c1", label="busy area", confidence=0.8)
    assert r["result"]["visibility"] == "public"


def test_overpass_failure_is_cached_briefly_per_place():
    """#169: a failed Overpass lookup (e.g. a timeout) is not retried for FAILURE_TTL_S at the same
    rounded place, so the Labeler doesn't spend another ~8 s step on it; other places still fetch."""
    from app.tools import geo_tools

    now = [100.0]
    calls = []

    def flaky(query):
        calls.append(query)
        if len(calls) == 1:
            raise TimeoutError("read timed out")
        return {"elements": []}

    c = geo_tools.OverpassClient(fetch=flaky, min_interval_s=0, failure_ttl_s=60, monotonic=lambda: now[0])
    with pytest.raises(TimeoutError):
        c.features_near(45.0, 10.0, 50)
    with pytest.raises(geo_tools.OverpassUnavailable, match="not retried"):
        c.features_near(45.00001, 10.00001, 50)  # same rounded key
    assert len(calls) == 1
    assert c.features_near(45.01, 10.0, 50) == []  # another place is unaffected
    now[0] += 61
    assert c.features_near(45.0, 10.0, 50) == []  # expired: tried again, and success is cached
    assert len(calls) == 3
    assert geo_tools.FETCH_TIMEOUT_S <= 8.0
    assert f"[timeout:{int(geo_tools.FETCH_TIMEOUT_S) - 1}]" in calls[-1]
