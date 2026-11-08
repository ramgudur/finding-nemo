"""Run from the repo root: python -m pytest sim/tests"""

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

from sim import campus_alert as ca
from sim.common import LocalFrame, load_spec

SPEC = load_spec("campus-alert")
FRAME = LocalFrame.from_spec(SPEC)


@pytest.fixture(scope="module")
def sc():
    return ca.build(seed=7)


def _at(sc, input_id):
    return [p for p in sc.posts if p.input_id == input_id]


def test_deterministic_for_a_seed(sc):
    again = ca.build(seed=7)
    assert [(p.sim_t, p.input_id, p.body) for p in again.posts] == [(p.sim_t, p.input_id, p.body) for p in sc.posts]
    other = ca.build(seed=8)
    assert [p.body for p in other.posts] != [p.body for p in sc.posts]


def test_posts_only_to_spec_inputs_with_declared_fields(sc):
    inputs = {i["id"]: i for i in SPEC["inputs"]}
    for p in sc.posts:
        inp = inputs[p.input_id]
        assert inp["kind"] == "push"
        declared = {f["name"] for f in inp.get("fields", [])}
        extra = set(p.body) - {"user_id", "lat", "lon"}
        assert extra <= declared, (p.input_id, extra)
        required = {f["name"] for f in inp.get("fields", []) if f.get("required", True)}
        assert required <= set(p.body)


def test_everything_inside_the_bbox(sc):
    b = SPEC["grid"]["bbox"]
    for p in sc.posts:
        assert b["min_lat"] <= p.body["lat"] <= b["max_lat"]
        assert b["min_lon"] <= p.body["lon"] <= b["max_lon"]


def test_timeline_milestones(sc):
    assert [p.sim_t for p in _at(sc, "room_button")] == [60.0]
    assert [p.sim_t for p in _at(sc, "police_marker")] == [150.0]
    spoof = set(sc.truth["spoof_users"])
    first_spoof = min(p.sim_t for p in sc.posts if p.body.get("user_id") in spoof)
    assert first_spoof == pytest.approx(100.0)
    assert sc.news["sim_t"] == 180.0
    assert max(p.sim_t for p in sc.posts) <= ca.DURATION


def test_threat_keeps_moving(sc):
    path = sc.truth["threat"]
    assert len(path) == int(ca.DURATION) + 1
    steps = [FRAME.to_xy(b["lat"], b["lon"]) for b in (path[0], path[60], path[150], path[-1])]
    for a, b in zip(steps, steps[1:], strict=False):
        assert math.dist(a, b) > 10


def test_runners_flee_and_hiders_stay(sc):
    tx, ty = ca.threat_at(0.0)
    by_user: dict[str, list] = {}
    for p in _at(sc, "panic"):
        by_user.setdefault(p.body["user_id"], []).append(FRAME.to_xy(p.body["lat"], p.body["lon"]))
    gains = [
        math.dist(tr[-1], (tx, ty)) - math.dist(tr[0], (tx, ty))
        for u in sc.truth["runners"]
        if len(tr := by_user[u]) > 3
    ]
    assert len(gains) > 30 and np.median(gains) > 50
    for g in sc.truth["hider_groups"]:
        for u in g["users"]:
            pts = np.array(by_user[u])
            assert np.linalg.norm(pts.std(axis=0)) < 10


def test_spoofers_have_red_flags(sc):
    pts: dict[str, list] = {}
    for p in _at(sc, "panic"):
        if p.body["user_id"] in sc.truth["spoof_users"]:
            pts.setdefault(p.body["user_id"], []).append((p.body["lat"], p.body["lon"]))
    dupes = [u for u in pts if u.startswith("u-d")]
    assert len(dupes) >= 2 and len({pts[u][0] for u in dupes}) == 1  # exact duplicates across accounts
    assert all(len(set(pts[u])) == 1 for u in dupes)


def test_rss_feed_has_article_only_after_news_time(sc):
    assert "<item>" not in ca.rss_xml(sc, 0.0, 179.0)
    xml = ca.rss_xml(sc, 0.0, 180.0)
    assert "[SCRIPTED DEMO]" in xml and "<georss:point>" in xml


def test_ground_truth_never_in_posts(sc):
    lines = ca.ground_truth_lines(sc, 0.0, 7, 1.0)
    assert lines[0]["type"] == "meta" and lines[0]["spoof_users"]
    assert {x["type"] for x in lines[1:]} == {"source"}  # the neutral line type api/debug.py reads
    blob = json.dumps([p.body for p in sc.posts])
    assert "spoof" not in blob and "threat" not in blob


def test_dry_run_prints_jsonl(capsys):
    assert ca.main(["--dry-run", "--duration", "20"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows and all(r["body"]["ts"].endswith("Z") for r in rows)


BACKEND = Path(__file__).resolve().parents[2] / "backend"


def test_flow_source_finds_the_threat_from_sim_events(sc):
    """End to end with the real operator and grid: the first 20 s of panic tracks point back at the threat."""
    sys.path.insert(0, str(BACKEND))
    try:
        from app.engine.grid import Grid
        from app.engine.operators import registry
        from app.engine.operators.base import Events
    except ImportError:
        pytest.skip("backend engine not importable")
    finally:
        sys.path.remove(str(BACKEND))

    class Clock:
        def now(self):
            return 20.0

    rows = [
        {"user_id": p.body["user_id"], "t": p.sim_t, "lat": p.body["lat"], "lon": p.body["lon"]}
        for p in _at(sc, "panic")
        if p.sim_t <= 20.0
    ]
    grid = Grid.from_spec(SPEC["grid"])
    op = next(o for o in SPEC["inputs"][0]["operators"] if o["op"] == "flow_source")
    params = {k: v for k, v in op.items() if k not in ("op", "weight")}
    res = registry.run("flow_source", Events.from_records(rows), params, None, Clock(), grid)
    point = res.extras.get("point")
    assert point is not None and res.extras.get("kind") == "source"
    truth = FRAME.to_xy(*FRAME.to_latlon(*ca.threat_at(10.0)))
    err = math.dist(FRAME.to_xy(point["lat"], point["lon"]), truth)
    assert err < 30, err


def test_input_map_and_spec_adaptation(sc):
    from sim.common import adapt_posts, parse_input_map

    m = parse_input_map("panic=sos, room_button=rooms")
    posts, problems = adapt_posts(sc.posts, m)
    assert not problems
    assert {p.input_id for p in posts} == {"sos", "rooms", "police_marker"}

    # a Builder-made app: panic declares no fields; police_marker is missing; room_button wants an extra required field
    spec = {
        "inputs": [
            {"id": "sos", "kind": "push", "fields": []},
            {
                "id": "rooms",
                "kind": "push",
                "fields": [{"name": "room", "required": True}, {"name": "floor", "required": True}],
            },
        ]
    }
    _, problems = adapt_posts(sc.posts, m, spec)
    assert any("no input 'police_marker'" in p for p in problems)

    spec["inputs"].append({"id": "police_marker", "kind": "push", "fields": [{"name": "cleared", "required": False}]})
    _, problems = adapt_posts(sc.posts, m, spec)
    assert problems == ["input 'rooms' requires fields the sim doesn't send: ['floor'] (make them optional)"]

    spec["inputs"][1]["fields"][1]["required"] = False
    posts, problems = adapt_posts(sc.posts, m, spec)
    assert not problems
    sos = [p for p in posts if p.input_id == "sos"]
    assert sos and all("press_id" not in p.body for p in sos)  # undeclared field dropped, not rejected by ingest
    assert all("room" in p.body for p in posts if p.input_id == "rooms")


def test_bad_input_map_is_an_error():
    from sim.common import parse_input_map

    with pytest.raises(ValueError):
        parse_input_map("panic")
