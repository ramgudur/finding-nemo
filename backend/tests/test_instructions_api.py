"""api/instructions.py: public, guarded end-user instructions from the caller's own position."""

import math
import re
from datetime import UTC, datetime

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sim import campus_alert as ca
from sim.common import LocalFrame, load_spec

from app.api import instructions as instructions_api
from app.api.instruction_trace import InstructionTraceDedup
from app.core import clock as clock_mod
from app.core.clock import SimClock
from app.core.db import Db
from app.core.models import DestinationPoint, GuardCheck, GuardResult, Instruction
from app.engine.tick import TickEngine
from app.main import DbEventSource, create_app

T0 = 1_800_000_000.0
RAW = load_spec("campus-alert")
FRAME = LocalFrame.from_spec(RAW)
GATHERING = {d["label"] for d in RAW["routing"]["destination_points"]}


def _mount(app):
    if not any(getattr(r, "path", "").endswith("/instruction") for r in app.routes):
        app.include_router(instructions_api.router)


@pytest.fixture(scope="module")
def p():
    clock = SimClock(start=T0)
    prev = clock_mod.set_clock(clock)
    try:
        db = Db(":memory:")
        engine = TickEngine(DbEventSource(db))
        app = create_app(db=db, engine=engine, autostart_engine=False)
        _mount(app)
        c = TestClient(app)
        assert c.post("/v1/apps", json=RAW).status_code == 201
        keys = c.post("/v1/apps/campus-alert/activate").json()
        before_tick = c.post("/v1/apps/campus-alert/instruction", json={"lat": 32.779, "lon": -96.761})
        posts, i = sorted(ca.build(7, duration=40).posts), 0
        for k in range(0, 41, 2):
            clock.set(T0 + k)
            while i < len(posts) and posts[i].sim_t <= k:
                c.post(
                    f"/v1/apps/campus-alert/inputs/{posts[i].input_id}/events",
                    json=posts[i].stamped(T0),
                    headers={"X-Ingest-Key": keys["ingest_key"]},
                )
                i += 1
            engine.tick_once()
        yield {"c": c, "db": db, "engine": engine, "keys": keys, "before_tick": before_tick}
    finally:
        clock_mod.set_clock(prev)


def _ask(p, x, y):
    lat, lon = FRAME.to_latlon(x, y)
    return p["c"].post("/v1/apps/campus-alert/instruction", json={"lat": lat, "lon": lon})


def test_no_map_yet_is_a_503_with_retry(p):
    r = p["before_tick"]
    assert r.status_code == 503 and r.headers["Retry-After"] == "2"


def test_public_caller_gets_a_guarded_route_to_a_gathering_point(p):
    tx, ty = ca.threat_at(40.0)
    r = _ask(p, tx - 60, ty - 60)  # someone near the danger, no key
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    ins = r.json()
    assert ins["guard"]["passed"] is True, ins["guard"]
    assert ins["destination"]["label"] in GATHERING
    assert len(ins["text"]) <= 360 and ins["distance_m"] > 0
    first = FRAME.to_xy(*ins["route"][0])
    assert math.dist(first, (tx - 60, ty - 60)) < 15  # starts where the caller is


def test_route_never_crosses_impassable_cells(p):
    tx, ty = ca.threat_at(40.0)
    ins = _ask(p, tx - 60, ty - 60).json()
    rt = p["engine"].apps["campus-alert"]
    score = p["engine"].latest("campus-alert").score
    lat = np.array([pt[0] for pt in ins["route"]])
    lon = np.array([pt[1] for pt in ins["route"]])
    rows, cols, inside = rt.grid.cell_of(lat, lon)
    assert inside.all()
    assert (score[rows, cols] < RAW["routing"]["impassable_score"]).all()


def test_text_never_carries_coordinates_or_sensitive_labels(p):
    ins = _ask(p, 100.0, 100.0).json()
    assert not re.search(r"-?\d+\.\d{3,}", ins["text"])  # no coordinate-like decimals
    for label in ("shelter-in-place group", "suspected spoof cluster"):
        assert label not in ins["text"].lower()


def test_caller_inside_the_blocked_area_gets_a_route_out(p):
    """#245: the person standing in the highest-score cell is the one the app must help most."""
    rt = p["engine"].apps["campus-alert"]
    score = p["engine"].latest("campus-alert").score
    r0, c0 = np.unravel_index(int(np.argmax(score)), score.shape)
    assert score[r0, c0] >= RAW["routing"]["impassable_score"]
    lat, lon = rt.grid.cell_center(int(r0), int(c0))
    ins = p["c"].post("/v1/apps/campus-alert/instruction", json={"lat": float(lat), "lon": float(lon)}).json()
    assert ins["guard"]["passed"] is True, ins["guard"]
    assert ins["destination"]["label"] in GATHERING
    assert ins["text"].startswith("Leave this area now") and len(ins["text"]) <= 360
    assert not re.search(r"-?\d+\.\d{3,}", ins["text"])


def test_outside_the_area_gets_the_fallback(p):
    r = p["c"].post("/v1/apps/campus-alert/instruction", json={"lat": 32.70, "lon": -96.70})
    assert r.status_code == 200
    ins = r.json()
    assert ins["destination"] is None and ins["route"] == []
    assert ins["text"] == RAW["routing"]["fallback_text"]


def _guard_entries(p):
    return [t for t in p["db"].list_trace("campus-alert") if t.agent == "message_guard"]


def test_a_new_outcome_is_traced_without_position_or_text(p):
    before = len(_guard_entries(p))
    _ask(p, 120.0, 120.0)  # first request from this cell: always a new outcome
    entries = _guard_entries(p)
    assert len(entries) == before + 1
    blob = " ".join(f"{e.saw} {e.tested} {e.result} {e.action} {e.why}" for e in entries)
    assert "32.7" not in blob and "-96." not in blob


def test_repeats_with_the_same_outcome_from_the_same_cell_are_not_retraced(p):
    _ask(p, 130.0, 130.0)
    before = len(_guard_entries(p))
    for _ in range(5):  # a page recomputing on every tick
        assert _ask(p, 130.0, 130.0).status_code == 200
    assert len(_guard_entries(p)) == before


def _ins(passed: bool, label: str | None, failing: tuple[tuple[str, str], ...] = ()) -> Instruction:
    checks = [GuardCheck(name=n, passed=False, detail=d) for n, d in failing] or [
        GuardCheck(name="length", passed=True)
    ]
    return Instruction(
        text="t",
        destination=None if label is None else DestinationPoint(lat=0.0, lon=0.0, label=label),
        route=[],
        distance_m=0.0,
        computed_at=datetime.fromtimestamp(T0, UTC),
        guard=GuardResult(passed=passed, checks=checks),
    )


def test_dedup_traces_on_any_change_of_destination_or_failure_reason():
    d = InstructionTraceDedup()
    key = ("some-app", 3, 4)
    assert d.should_trace(key, _ins(True, "exit a"))
    assert not d.should_trace(key, _ins(True, "exit a"))
    assert d.should_trace(key, _ins(True, "exit b"))  # destination changed
    assert d.should_trace(key, _ins(False, None, (("route_found", "no reachable destination"),)))
    assert not d.should_trace(key, _ins(False, None, (("route_found", "no reachable destination"),)))
    assert d.should_trace(key, _ins(False, None, (("route_found", "start is blocked"),)))  # new reason
    assert d.should_trace(("some-app", 3, 5), _ins(False, None, (("route_found", "start is blocked"),)))  # new cell
    assert d.should_trace(("other-app", 3, 4), _ins(True, "exit a"))  # keys are per app


def test_dedup_memory_is_bounded():
    d = InstructionTraceDedup(max_keys=3)
    for i in range(10):
        d.should_trace(("some-app", i, 0), _ins(True, "exit a"))
    assert len(d) == 3
    assert d.should_trace(("some-app", 0, 0), _ins(True, "exit a"))  # evicted: traced again


def test_unknown_app_and_apps_without_routing_are_404(p):
    assert p["c"].post("/v1/apps/nope/instruction", json={"lat": 1.0, "lon": 1.0}).status_code == 404


def test_bad_body_is_422(p):
    assert p["c"].post("/v1/apps/campus-alert/instruction", json={"lat": 999, "lon": 0}).status_code == 422
    assert p["c"].post("/v1/apps/campus-alert/instruction", json={"user_id": "u-run-001"}).status_code == 422
