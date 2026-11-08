"""`POST /v1/apps/{app}/simulate` (#268): simulated events through the ingest path. No network,
fixed seeds, SimClock."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.api.simulate import get_sim_rng
from app.core import clock as clock_mod
from app.core.clock import SimClock
from app.core.db import Db
from app.engine import trust as trust_mod
from app.main import DbEventSource, create_app
from app.tools.context import ToolContext
from app.tools.read_tools import WindowArgs, get_suspicious_users

T0 = 1_800_000_000.0
BBOX = {"min_lat": 10.0, "min_lon": 20.0, "max_lat": 10.02, "max_lon": 20.02}
APP = "sim-api-app"
CENTRE = {"lat": 10.01, "lon": 20.01}


def _spec(app_id: str = APP) -> dict:
    return {
        "app_id": app_id,
        "intent": "map where items are reported",
        "grid": {"cell_m": 25, "bbox": BBOX},
        "inputs": [
            {
                "id": "pings",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "a report from our own app",
                "fields": [
                    {"name": "label", "type": "string", "required": True},
                    {"name": "level", "type": "integer", "required": True},
                    {"name": "value", "type": "number", "required": True},
                    {"name": "flag", "type": "boolean", "required": True},
                    {"name": "note", "type": "string", "required": False},
                ],
                "operators": [{"op": "density", "weight": 1.0}],
            },
            {
                "id": "reports",
                "kind": "pull",
                "shape": "document",
                "tier": "stiff",
                "meaning": "public reports of items in the area",
                "operators": [{"op": "event_points", "weight": 0.3}],
            },
        ],
    }


@pytest.fixture()
def p():
    clock = SimClock(start=T0)
    prev = clock_mod.set_clock(clock)
    try:
        db = Db(":memory:")
        app = create_app(db=db, autostart_engine=False)
        rng = np.random.default_rng(7)  # one seeded stream for the whole test, like production's
        app.dependency_overrides[get_sim_rng] = lambda: rng
        c = TestClient(app)
        assert c.post("/v1/apps", json=_spec()).status_code == 201
        keys = c.post(f"/v1/apps/{APP}/activate").json()
        yield {"c": c, "db": db, "app": app, "auth": {"X-Authority-Key": keys["authority_key"]}, "clock": clock}
    finally:
        clock_mod.set_clock(prev)


def _body(**over) -> dict:
    return {"input_id": "pings", "count": 20, **CENTRE, "radius_m": 300, "mode": "normal", **over}


def _post(p, body=None, headers=None, app_id=APP):
    return p["c"].post(
        f"/v1/apps/{app_id}/simulate", json=body or _body(), headers=p["auth"] if headers is None else headers
    )


def test_normal_mode_scatters_distinct_sim_users_with_neutral_defaults(p):
    r = _post(p)
    assert r.status_code == 202
    assert r.json() == {"accepted": 20, "rejected": 0, "user_id_prefix": "sim-"}
    events = p["db"].list_events(APP, "pings")
    assert len(events) == 20
    assert sorted(e.user_id for e in events) == sorted(f"sim-{n}" for n in range(1, 21))
    assert len({(e.lat, e.lon) for e in events}) == 20  # scattered, no duplicates
    for e in events:
        dy = (e.lat - CENTRE["lat"]) * 111_320
        dx = (e.lon - CENTRE["lon"]) * 111_320 * np.cos(np.radians(CENTRE["lat"]))
        assert np.hypot(dx, dy) <= 300 + 1e-6
        assert T0 - 31 <= e.ts.timestamp() <= T0
        assert e.model_extra == {"label": "simulated", "level": 0, "value": 0.0, "flag": False}  # required only


def test_ids_keep_counting_across_requests(p):
    assert _post(p, _body(count=3)).status_code == 202
    assert _post(p, _body(count=2)).status_code == 202
    ids = {e.user_id for e in p["db"].list_events(APP, "pings")}
    assert ids == {"sim-1", "sim-2", "sim-3", "sim-4", "sim-5"}


def test_deterministic_with_a_seed(p):
    _post(p, _body(count=5))
    first = [(e.lat, e.lon) for e in p["db"].list_events(APP, "pings")]
    db2 = Db(":memory:")
    app2 = create_app(db=db2, autostart_engine=False)
    rng2 = np.random.default_rng(7)
    app2.dependency_overrides[get_sim_rng] = lambda: rng2
    c2 = TestClient(app2)
    c2.post("/v1/apps", json=_spec())
    key = c2.post(f"/v1/apps/{APP}/activate").json()["authority_key"]
    c2.post(f"/v1/apps/{APP}/simulate", json=_body(count=5), headers={"X-Authority-Key": key})
    assert [(e.lat, e.lon) for e in db2.list_events(APP, "pings")] == first


def test_points_outside_the_area_are_rejected_not_stored(p):
    edge = {"lat": BBOX["min_lat"] + 0.0001, "lon": BBOX["min_lon"] + 0.0001}  # ~11 m from the corner
    r = _post(p, _body(count=50, radius_m=2000, **edge))
    assert r.status_code == 202
    body = r.json()
    assert body["accepted"] + body["rejected"] == 50 and body["rejected"] > 0
    assert len(p["db"].list_events(APP, "pings")) == body["accepted"]


def test_trace_marks_events_as_simulated(p):
    _post(p, _body(count=4))
    (entry,) = [e for e in p["db"].list_trace(APP) if e.action == "simulate"]
    assert entry.agent == "scout"
    assert "SIMULATED" in entry.why and "sim-1..sim-4" in entry.why
    assert "4 accepted, 0 rejected" in entry.result


def test_spoof_mode_trips_duplicate_coords_and_new_id_burst(p):
    # Background traffic first: an onset wave, then a later normal wave (the burst check skips the
    # first minute of a batch and needs a background rate to compare with).
    assert _post(p, _body(count=20)).status_code == 202
    p["clock"].advance(100)
    assert _post(p, _body(count=20)).status_code == 202
    p["clock"].advance(100)
    r = _post(p, _body(count=30, mode="spoof"))
    assert r.status_code == 202
    assert r.json() == {"accepted": 30, "rejected": 0, "user_id_prefix": "spoof-"}

    spoof = [e for e in p["db"].list_events(APP, "pings") if e.user_id.startswith("spoof-")]
    assert {e.user_id for e in spoof} == {f"spoof-{n}" for n in range(1, 31)}
    assert {(e.lat, e.lon) for e in spoof} == {(CENTRE["lat"], CENTRE["lon"])}
    assert max(e.ts.timestamp() for e in spoof) - min(e.ts.timestamp() for e in spoof) <= 3.0

    # The trust spoof features flag every spoof user, and no sim user, for both checks.
    events = DbEventSource(p["db"]).events(APP, "pings", T0 - 400, T0 + 200)
    flagged = {s.user_id: set(s.flags) for s in trust_mod.suspicious_users(events)}
    for n in range(1, 31):
        assert {"duplicate_coords", "new_id_burst"} <= flagged[f"spoof-{n}"]
    assert not any(u.startswith("sim-") for u in flagged)

    # ...and so does the Critic's get_suspicious_users tool.
    engine = p["app"].state.engine
    ctx = ToolContext(app_id=APP, db=p["db"], engine=engine, actor="agent", agent="critic")
    out = get_suspicious_users(ctx, WindowArgs(window_s=400))
    assert out["by_flag"]["duplicate_coords"] == 30 and out["by_flag"]["new_id_burst"] == 30
    assert all(s["user_id"].startswith("spoof-") for s in out["top"])


def test_missing_or_wrong_key_is_401(p):
    assert _post(p, headers={}).status_code == 401
    assert _post(p, headers={"X-Authority-Key": "wrong"}).status_code == 401
    assert p["db"].list_events(APP) == []


def test_unknown_app_or_input_is_404(p):
    assert _post(p, app_id="no-such-app").status_code == 404
    assert _post(p, _body(input_id="nope")).status_code == 404


def test_inactive_app_is_409(p):
    p["c"].post("/v1/apps", json=_spec("draft-app"))
    # a draft app has no keys yet; give it one to reach the status check
    keys = p["db"].create_keys("draft-app", T0)
    r = _post(p, headers={"X-Authority-Key": keys.authority_key}, app_id="draft-app")
    assert r.status_code == 409


@pytest.mark.parametrize(
    "over",
    [
        {"input_id": "reports"},  # pull input
        {"lat": 0.0, "lon": 0.0},  # centre outside the area
        {"count": 0},
        {"count": 201},
        {"radius_m": 9},
        {"radius_m": 5001},
        {"mode": "other"},
        {"extra": 1},
    ],
)
def test_bad_bodies_are_422(p, over):
    assert _post(p, _body(**over)).status_code == 422
    assert p["db"].list_events(APP) == []
