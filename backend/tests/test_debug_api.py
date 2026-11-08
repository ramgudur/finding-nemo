"""api/debug.py: DEMO_MODE-only error chart and clock controls (rule 9: only reader of ground truth)."""

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sim import campus_alert as ca
from sim.common import load_spec

from app.api import debug
from app.core import clock as clock_mod
from app.core.clock import SimClock, WallClock
from app.core.db import Db
from app.engine.tick import TickEngine
from app.main import DbEventSource, create_app

T0 = 1_800_000_000.0
RAW = load_spec("campus-alert")


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).isoformat().replace("+00:00", "Z")


@pytest.fixture
def platform(tmp_path):
    """Real app, campus-alert active, DEMO_MODE debug installed with a ground-truth file in tmp_path."""
    clock = SimClock(start=T0)
    prev = clock_mod.set_clock(clock)
    try:
        db = Db(":memory:")
        engine = TickEngine(DbEventSource(db))
        app = create_app(db=db, engine=engine, autostart_engine=False)
        debug.install(app, engine, truth_dir=tmp_path)
        client = TestClient(app)
        assert client.post("/v1/apps", json=RAW).status_code == 201
        keys = client.post("/v1/apps/campus-alert/activate").json()
        sc = ca.build(7, duration=40)
        truth = tmp_path / "campus-alert-20300101T000000Z.jsonl"
        truth.write_text("\n".join(json.dumps(x) for x in ca.ground_truth_lines(sc, T0, 7, 1.0)), encoding="utf-8")
        yield {"client": client, "engine": engine, "clock": clock, "keys": keys, "scenario": sc, "db": db}
    finally:
        clock_mod.set_clock(prev)


def _auth(p):
    return {"X-Authority-Key": p["keys"]["authority_key"]}


def _run(p, until: int) -> None:
    posts = sorted(p["scenario"].posts)
    i = 0
    for k in range(0, until + 1, 2):
        p["clock"].set(T0 + k)
        while i < len(posts) and posts[i].sim_t <= k:
            r = p["client"].post(
                f"/v1/apps/campus-alert/inputs/{posts[i].input_id}/events",
                json=posts[i].stamped(T0),
                headers={"X-Ingest-Key": p["keys"]["ingest_key"]},
            )
            assert r.status_code == 202
            i += 1
        p["engine"].tick_once()


def test_debug_routes_404_without_demo_mode():
    client = TestClient(
        create_app(db=Db(":memory:"), engine=TickEngine(DbEventSource(Db(":memory:"))), autostart_engine=False)
    )
    assert client.get("/v1/debug/clock").status_code == 404
    assert client.get("/v1/debug/apps/campus-alert/error").status_code == 404
    assert client.post("/v1/debug/apps/campus-alert/clock", json={"paused": True}).status_code == 404


def test_error_series_needs_the_authority_key(platform):
    c = platform["client"]
    assert c.get("/v1/debug/apps/campus-alert/error").status_code == 401
    assert c.get("/v1/debug/apps/campus-alert/error", headers={"X-Authority-Key": "wrong"}).status_code == 401
    assert c.get("/v1/debug/apps/nope/error", headers=_auth(platform)).status_code == 404


def test_error_series_tracks_the_true_source(platform):
    _run(platform, 20)
    r = platform["client"].get("/v1/debug/apps/campus-alert/error", headers=_auth(platform))
    assert r.status_code == 200
    body = r.json()
    assert body["truth_file"] == "campus-alert-20300101T000000Z.jsonl"
    assert len(body["series"]) == 11  # one point per recorded tick (0..20 s every 2 s) inside the truth window
    late = [s for s in body["series"] if s["hotspot_error_m"] is not None]
    assert late and late[-1]["hotspot_error_m"] < 60
    pts = [s["point_error_m"] for s in body["series"] if s["point_error_m"] is not None]
    assert pts and min(pts) < 30  # flow_source's point estimate lands near the source
    assert body["changes"] == []


def test_applied_changes_become_markers(platform):
    from app.core.models import Change

    _run(platform, 10)
    platform["db"].add_change(
        Change(
            id="chg-1",
            app_id="campus-alert",
            kind="trust",
            target="u-d00",
            before={"trust": 1.0},
            after={"trust": 0.2},
            reason="duplicate coordinates",
            actor="agent",
            t=_iso(T0 + 8),
        )
    )
    body = platform["client"].get("/v1/debug/apps/campus-alert/error", headers=_auth(platform)).json()
    assert [(m["id"], m["kind"], m["target"]) for m in body["changes"]] == [("chg-1", "trust", "u-d00")]
    # activation's spec_confirm Change is not a marker
    assert all(m["kind"] in ("weight", "trust") for m in body["changes"])


def test_missing_ground_truth_gives_an_empty_series(platform, tmp_path):
    for f in tmp_path.glob("*.jsonl"):
        f.unlink()
    _run(platform, 6)
    body = platform["client"].get("/v1/debug/apps/campus-alert/error", headers=_auth(platform)).json()
    assert body["truth_file"] is None and body["series"] == []


def test_clock_controls_pause_resume_and_speed(platform):
    c = platform["client"]
    state = c.get("/v1/debug/clock").json()
    assert state["controllable"] is True and state["now_s"] == T0 and state["paused"] is True  # manual = paused

    assert c.post("/v1/debug/apps/campus-alert/clock", json={"paused": True}).status_code == 401
    still = c.post("/v1/debug/apps/campus-alert/clock", json={"speed": 4}, headers=_auth(platform)).json()
    assert still["paused"] is True  # a speed change alone doesn't resume
    r = c.post("/v1/debug/apps/campus-alert/clock", json={"paused": False}, headers=_auth(platform))
    assert r.status_code == 200 and r.json()["speed"] == 4 and r.json()["paused"] is False

    paused = c.post("/v1/debug/apps/campus-alert/clock", json={"paused": True}, headers=_auth(platform)).json()
    assert paused["paused"] is True and paused["speed"] is None
    frozen = c.get("/v1/debug/clock").json()["now_s"]
    assert c.get("/v1/debug/clock").json()["now_s"] == frozen

    resumed = c.post("/v1/debug/apps/campus-alert/clock", json={"paused": False}, headers=_auth(platform)).json()
    assert resumed["speed"] == 4  # resumes at the last chosen speed
    assert c.post("/v1/debug/apps/campus-alert/clock", json={"speed": 0}, headers=_auth(platform)).status_code == 422


def test_first_control_takes_over_from_wall_time(platform):
    clock_mod.set_clock(WallClock())
    c = platform["client"]
    assert c.get("/v1/debug/clock").json()["controllable"] is False
    r = c.post("/v1/debug/apps/campus-alert/clock", json={"paused": True}, headers=_auth(platform)).json()
    assert r["controllable"] is True and r["paused"] is True
    assert isinstance(clock_mod.get_clock(), SimClock)


def test_ground_truth_never_reaches_the_engine(platform):
    """Rule 9: the debug state reads engine outputs; the engine never sees truth files."""
    _run(platform, 4)
    st = platform["client"].app.state.debug
    assert st.record in platform["engine"].listeners
    assert not hasattr(platform["engine"], "truth_dir")
