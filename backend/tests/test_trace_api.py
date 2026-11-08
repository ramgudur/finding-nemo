"""api/trace.py: authority-only console reads and the dev weight pin."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sim import campus_alert as ca
from sim.common import load_spec

from app.api import trace as trace_api
from app.core import clock as clock_mod
from app.core.clock import SimClock
from app.core.db import Db
from app.core.models import TraceEntry
from app.engine.tick import TickEngine
from app.main import DbEventSource, create_app

T0 = 1_800_000_000.0
RAW = load_spec("campus-alert")


@pytest.fixture(scope="module")
def p():
    clock = SimClock(start=T0)
    prev = clock_mod.set_clock(clock)
    try:
        db = Db(":memory:")
        engine = TickEngine(DbEventSource(db))
        app = create_app(db=db, engine=engine, autostart_engine=False)
        if not any(getattr(r, "path", "").endswith("/weights") for r in app.routes):
            app.include_router(trace_api.router)
        c = TestClient(app)
        assert c.post("/v1/apps", json=RAW).status_code == 201
        keys = c.post("/v1/apps/campus-alert/activate").json()
        posts, i = sorted(ca.build(7, duration=20).posts), 0
        for k in range(0, 21, 2):
            clock.set(T0 + k)
            while i < len(posts) and posts[i].sim_t <= k:
                c.post(
                    f"/v1/apps/campus-alert/inputs/{posts[i].input_id}/events",
                    json=posts[i].stamped(T0),
                    headers={"X-Ingest-Key": keys["ingest_key"]},
                )
                i += 1
            engine.tick_once()
        db.add_trace(
            TraceEntry(
                t=datetime.fromtimestamp(T0 + 20, UTC),
                app_id="campus-alert",
                agent="critic",
                saw="a new hotspot",
                action="none",
                why="cold start",
            )
        )
        yield {"c": c, "auth": {"X-Authority-Key": keys["authority_key"]}, "db": db}
    finally:
        clock_mod.set_clock(prev)


ROUTES = ["trace", "changes", "weights", "verification"]


@pytest.mark.parametrize("route", ROUTES)
def test_every_read_is_authority_only(p, route):
    c = p["c"]
    assert c.get(f"/v1/apps/campus-alert/{route}").status_code == 401
    assert c.get(f"/v1/apps/campus-alert/{route}", headers={"X-Authority-Key": "wrong"}).status_code == 401
    assert c.get(f"/v1/apps/nope/{route}", headers=p["auth"]).status_code == 404
    assert c.get(f"/v1/apps/campus-alert/{route}", headers=p["auth"]).status_code == 200


def test_trace_and_changes(p):
    trace = p["c"].get("/v1/apps/campus-alert/trace", headers=p["auth"]).json()
    assert [t["agent"] for t in trace] == ["critic"] and trace[0]["why"] == "cold start"
    changes = p["c"].get("/v1/apps/campus-alert/changes", headers=p["auth"]).json()
    assert any(ch["kind"] == "spec_confirm" and ch["actor"] == "dev" for ch in changes)
    only = p["c"].get("/v1/apps/campus-alert/changes?kind=trust", headers=p["auth"]).json()
    assert only == []


def test_weights_show_tiers_pins_and_the_flexible_total(p):
    w = p["c"].get("/v1/apps/campus-alert/weights", headers=p["auth"]).json()
    by = {x["layer_id"]: x for x in w["layers"]}
    assert by["panic.flow_source"]["tier"] == "flexible" and by["local_news.event_points"]["tier"] == "stiff"
    assert w["flexible_total"] == pytest.approx(by["panic.flow_source"]["weight"] + by["panic.density"]["weight"])
    assert not any(x["pinned"] for x in w["layers"])


def test_verification_lists_methods_in_priority_order(p):
    v = p["c"].get("/v1/apps/campus-alert/verification", headers=p["auth"]).json()
    assert v["ticks"] >= 2
    assert [m["method"] for m in v["methods"]] == [
        "authority_confirmation",
        "predictive_check",
        "cross_input_agreement",
    ]
    assert [m["priority"] for m in v["methods"]] == [1, 2, 3]
    assert isinstance(v["cold_start"], bool)


def test_dev_pin_goes_through_the_tool_and_is_recorded(p):
    c, auth = p["c"], p["auth"]
    url = "/v1/apps/campus-alert/weights/panic.density/pin"
    assert c.post(url, json={"pinned": True}).status_code == 401
    w = c.post(url, json={"pinned": True, "reason": "hold density while testing"}, headers=auth).json()
    assert {x["layer_id"]: x["pinned"] for x in w["layers"]}["panic.density"] is True
    pins = [
        ch for ch in c.get("/v1/apps/campus-alert/changes?kind=weight", headers=auth).json() if ch["actor"] == "dev"
    ]
    assert pins and pins[-1]["after"]["pinned"] is True and pins[-1]["reason"] == "hold density while testing"
    w = c.post(url, json={"pinned": False}, headers=auth).json()
    assert {x["layer_id"]: x["pinned"] for x in w["layers"]}["panic.density"] is False
    bad = c.post("/v1/apps/campus-alert/weights/nope/pin", json={"pinned": True}, headers=auth)
    assert bad.status_code == 400 and "unknown layer" in bad.json()["detail"]
