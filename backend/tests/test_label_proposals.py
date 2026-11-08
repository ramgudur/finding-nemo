"""Label proposals: the POI Labeler's `propose_label` tool guards and the authority-only console routes
that let the dev accept (into the active spec's label_set, live) or dismiss them. Neutral examples."""

import json

import pytest
from fastapi.testclient import TestClient

from app.core import clock as clock_mod
from app.core.clock import SimClock
from app.core.db import Db
from app.core.models import Candidate
from app.engine.tick import TickEngine
from app.main import DbEventSource, create_app
from app.tools import registry
from app.tools.context import CandidateStore, ToolContext

T0 = 1_800_000_000.0
APP = "label-app"
BBOX = {"min_lat": 45.0, "min_lon": 10.0, "max_lat": 45.0045, "max_lon": 10.00635}


def spec_json(mode="auto"):
    return {
        "app_id": APP,
        "intent": "find places worth naming",
        "status": "pending_confirmation",
        "grid": {"cell_m": 10, "bbox": BBOX},
        "inputs": [
            {
                "id": "pings",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "a report",
                "operators": [{"op": "density", "weight": 1.0}],
            }
        ],
        "outputs": {
            "layers": [{"id": "pings.density", "visibility": "public"}, {"id": "score", "visibility": "public"}],
            "poi_labels": {
                "mode": mode,
                "min_confidence": 0.6,
                "label_set": [{"label": "busy area"}, {"label": "private group", "sensitive": True}],
            },
        },
    }


@pytest.fixture
def env():
    def build(mode="auto"):
        clock = SimClock(start=T0)
        prev = clock_mod.set_clock(clock)
        db = Db(":memory:")
        engine = TickEngine(DbEventSource(db), clock=clock)
        c = TestClient(create_app(db=db, engine=engine, autostart_engine=False))
        assert c.post("/v1/apps", json=spec_json(mode)).status_code == 201
        keys = c.post(f"/v1/apps/{APP}/activate").json()
        engine.tick_once()
        rt = engine.apps[APP]
        rt.last.candidates = [
            Candidate(
                id="c1",
                lat=45.002,
                lon=10.003,
                radius_m=20,
                source="dwell_clusters",
                contributing_layers=["pings.density"],
                persisted_ticks=4,
            )  # fmt: skip
        ]

        def ctx():
            return ToolContext(APP, db, engine, agent="poi_labeler", clock=clock, candidates=CandidateStore())

        cleanup.append(prev)
        return {"c": c, "db": db, "engine": engine, "ctx": ctx, "auth": {"X-Authority-Key": keys["authority_key"]}}

    cleanup = []
    yield build
    for prev in cleanup:
        clock_mod.set_clock(prev)


def propose(ctx, **args):
    base = {"label": "quiet corner", "why": "a stable cluster no label in the set describes"}
    return registry.call_tool(ctx, "propose_label", {**base, **args})


# -- tool guards ------------------------------------------------------------------


def test_propose_label_stores_proposal_and_traces(env):
    e = env()
    r = propose(e["ctx"]())
    assert r["ok"], r
    props = e["db"].list_label_proposals(APP)
    assert [(p.label, p.status, p.sensitive, p.destination) for p in props] == [("quiet corner", "open", False, False)]
    assert props[0].id == r["result"]["proposal_id"]
    trace = [t for t in e["db"].list_trace(APP) if t.action == "propose_label"]
    assert len(trace) == 1 and trace[0].agent == "poi_labeler" and "no label" in trace[0].why
    # the spec is unchanged until the dev accepts
    assert [lb.label for lb in e["engine"].apps[APP].spec.outputs.poi_labels.label_set] == [
        "busy area",
        "private group",
    ]


def test_propose_label_rejections(env):
    e = env()
    ctx = e["ctx"]
    r = propose(ctx(), sensitive=True, destination=True)
    assert not r["ok"] and "both sensitive and a destination" in r["error"]
    r = propose(ctx(), label="Busy  AREA")
    assert not r["ok"] and "already in this app's label_set" in r["error"]
    assert propose(ctx())["ok"]
    r = propose(ctx(), label="QUIET corner")
    assert not r["ok"] and "open proposal" in r["error"]
    assert not propose(ctx(), label="")["ok"]
    assert not propose(ctx(), label="   ")["ok"]
    assert not propose(ctx(), label="x" * 61)["ok"]
    assert propose(ctx(), label="x" * 60)["ok"]
    assert not propose(ctx(), label="long why", why="w" * 501)["ok"]
    assert not propose(ctx(), label="extra", lat=1.0)["ok"]  # extra keys rejected
    for i in range(3):
        assert propose(ctx(), label=f"kind {i}")["ok"]
    r = propose(ctx(), label="one too many")
    assert not r["ok"] and "already open" in r["error"]
    assert len(e["db"].list_label_proposals(APP, status="open")) == 5
    # only the POI Labeler may propose; the dev-only tools are not open to agents
    critic = ToolContext(APP, e["db"], e["engine"], agent="critic", candidates=CandidateStore())
    assert not propose(critic, label="other")["ok"]
    pid = e["db"].list_label_proposals(APP)[0].id
    assert not registry.call_tool(ctx(), "accept_label_proposal", {"proposal_id": pid})["ok"]
    assert not registry.call_tool(ctx(), "dismiss_label_proposal", {"proposal_id": pid})["ok"]


def test_propose_label_blocked_when_mode_off(env):
    e = env(mode="off")
    r = propose(e["ctx"]())
    assert not r["ok"] and "off" in r["error"]
    assert e["db"].list_label_proposals(APP) == []


# -- console routes -------------------------------------------------------------------


def test_routes_need_authority_key(env):
    e = env()
    c = e["c"]
    pid = propose(e["ctx"]())["result"]["proposal_id"]
    base = f"/v1/apps/{APP}/label-proposals"
    for method, path in (("get", base), ("post", f"{base}/{pid}/accept"), ("post", f"{base}/{pid}/dismiss")):
        assert getattr(c, method)(path).status_code == 401
        assert getattr(c, method)(path, headers={"X-Authority-Key": "wrong"}).status_code == 401
    assert c.get("/v1/apps/nope/label-proposals", headers=e["auth"]).status_code == 404
    assert e["db"].get_label_proposal(APP, pid).status == "open"
    # no public route leaks the agent's text
    for path in ("map?format=geojson", "hotspots", "pois"):
        assert "no label in the set" not in c.get(f"/v1/apps/{APP}/{path}").text


def test_list_returns_proposals(env):
    e = env()
    pid = propose(e["ctx"]())["result"]["proposal_id"]
    r = e["c"].get(f"/v1/apps/{APP}/label-proposals", headers=e["auth"])
    assert r.status_code == 200
    body = r.json()
    assert [p["id"] for p in body] == [pid]
    assert set(body[0]) == {"id", "label", "sensitive", "destination", "why", "status", "t"}


def test_accept_updates_db_and_running_engine_then_create_poi_succeeds(env):
    e = env()
    ctx = e["ctx"]
    r = registry.call_tool(ctx(), "create_poi", {"candidate_id": "c1", "label": "quiet corner", "confidence": 0.8})
    assert not r["ok"]
    pid = propose(ctx(), destination=True)["result"]["proposal_id"]
    r = e["c"].post(f"/v1/apps/{APP}/label-proposals/{pid}/accept", headers=e["auth"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["proposal"]["status"] == "accepted"
    new = {"label": "quiet corner", "sensitive": False, "destination": True}
    assert body["label_set"][-1] == new
    assert e["db"].get_app(APP).outputs.poi_labels.label_set[-1].model_dump() == new
    assert e["engine"].apps[APP].spec.outputs.poi_labels.label_set[-1].model_dump() == new
    assert e["db"].get_label_proposal(APP, pid).status == "accepted"
    ch = e["db"].list_changes(APP, kind="spec_edit")
    assert len(ch) == 1 and ch[0].actor == "dev" and ch[0].target == "outputs.poi_labels.label_set"
    assert [lb["label"] for lb in ch[0].before] == ["busy area", "private group"]
    assert [lb["label"] for lb in ch[0].after] == ["busy area", "private group", "quiet corner"]
    assert any(t.action == "label proposal accepted by dev" for t in e["db"].list_trace(APP))
    # no restart needed: the labeler can use the label now
    r = registry.call_tool(ctx(), "create_poi", {"candidate_id": "c1", "label": "quiet corner", "confidence": 0.8})
    assert r["ok"], r
    assert r["result"]["visibility"] == "public"  # destination label
    # accepted again -> 409; the label now exists, so proposing it again is rejected
    assert e["c"].post(f"/v1/apps/{APP}/label-proposals/{pid}/accept", headers=e["auth"]).status_code == 409
    assert not propose(ctx())["ok"]


def test_accept_with_flag_override(env):
    e = env()
    pid = propose(e["ctx"](), destination=True)["result"]["proposal_id"]
    r = e["c"].post(
        f"/v1/apps/{APP}/label-proposals/{pid}/accept",
        headers=e["auth"],
        json={"destination": False, "sensitive": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["label_set"][-1] == {"label": "quiet corner", "sensitive": True, "destination": False}
    r = registry.call_tool(e["ctx"](), "create_poi", {"candidate_id": "c1", "label": "quiet corner", "confidence": 0.8})
    assert r["ok"] and r["result"]["visibility"] == "authority"  # sensitive


def test_accept_sensitive_destination_override_is_422(env):
    e = env()
    pid = propose(e["ctx"](), destination=True)["result"]["proposal_id"]
    r = e["c"].post(f"/v1/apps/{APP}/label-proposals/{pid}/accept", headers=e["auth"], json={"sensitive": True})
    assert r.status_code == 422
    assert "sensitive" in json.dumps(r.json())
    assert e["db"].get_label_proposal(APP, pid).status == "open"
    assert len(e["engine"].apps[APP].spec.outputs.poi_labels.label_set) == 2
    assert e["db"].list_changes(APP, kind="spec_edit") == []
    r = e["c"].post(f"/v1/apps/{APP}/label-proposals/{pid}/accept", headers=e["auth"], json={"bogus": 1})
    assert r.status_code == 422


def test_accept_unknown_and_inactive(env):
    e = env()
    c, auth = e["c"], e["auth"]
    assert c.post(f"/v1/apps/{APP}/label-proposals/lp-missing/accept", headers=auth).status_code == 404
    assert c.post(f"/v1/apps/{APP}/label-proposals/lp-missing/dismiss", headers=auth).status_code == 404
    pid = propose(e["ctx"]())["result"]["proposal_id"]
    e["engine"].remove_app(APP)  # the app no longer runs
    assert c.post(f"/v1/apps/{APP}/label-proposals/{pid}/accept", headers=auth).status_code == 409


def test_dismiss(env):
    e = env()
    pid = propose(e["ctx"]())["result"]["proposal_id"]
    r = e["c"].post(f"/v1/apps/{APP}/label-proposals/{pid}/dismiss", headers=e["auth"])
    assert r.status_code == 200 and r.json()["status"] == "dismissed"
    assert e["db"].get_label_proposal(APP, pid).status == "dismissed"
    assert len(e["engine"].apps[APP].spec.outputs.poi_labels.label_set) == 2
    assert any(t.action == "label proposal dismissed by dev" for t in e["db"].list_trace(APP))
    assert e["c"].post(f"/v1/apps/{APP}/label-proposals/{pid}/dismiss", headers=e["auth"]).status_code == 409
    assert e["c"].post(f"/v1/apps/{APP}/label-proposals/{pid}/accept", headers=e["auth"]).status_code == 409
    # a dismissed proposal no longer counts as open: the same label can be proposed again
    assert propose(e["ctx"]())["ok"]
