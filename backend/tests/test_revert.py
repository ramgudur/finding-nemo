"""Dev revert of a Change: POST /v1/apps/{app}/changes/{id}/revert and tools/revert_tools.py."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.api import trace as trace_api
from app.core import clock as clock_mod
from app.core.clock import SimClock
from app.core.db import Db
from app.core.models import AppSpec, Candidate, Change
from app.engine.tick import InMemoryEventSource, TickEngine
from app.main import DbEventSource, create_app
from app.tools import guards, registry, revert_tools
from app.tools.context import CandidateStore, ToolContext
from app.tools.guards import GuardError
from app.tools.revert_tools import RevertNotFound, RevertStale

T0 = 1_000_000.0
BBOX = {"min_lat": 45.0, "min_lon": 10.0, "max_lat": 45.0045, "max_lon": 10.00635}
ALL = ("authority_confirmation", "predictive_check", "cross_input_agreement")
FEED_A = "http://93.184.216.34/a.xml"  # IP literals: the URL guard needs no DNS lookup
FEED_B = "http://93.184.216.34/b.xml"


def _input(id_, tier, weight, **extra):
    return {
        "id": id_,
        "kind": "push",
        "shape": "point",
        "tier": tier,
        "meaning": "a report",
        "operators": [{"op": "density", "weight": weight, "half_life_s": 60, "sigma_m": 15}],
        **extra,
    }


def spec_dict(app_id="rev-app", status="active"):
    return {
        "app_id": app_id,
        "intent": "test",
        "status": status,
        "grid": {"cell_m": 10, "bbox": BBOX},
        "inputs": [
            _input("crowd_a", "flexible", 0.5),
            _input("crowd_b", "flexible", 0.5),
            _input("auth", "locked", 1.0),
            _input("docs", "stiff", 0.5, kind="pull", connector={"type": "rss", "url": FEED_A, "poll_s": 60}),
        ],
        "verification": [{"method": m, "priority": i + 1} for i, m in enumerate(ALL)],
        "outputs": {
            "poi_labels": {
                "mode": "auto",
                "min_confidence": 0.5,
                "label_set": [{"label": "spot a"}, {"label": "spot b"}],
            }
        },
    }


@pytest.fixture
def env():
    clock = SimClock(T0)
    db = Db(":memory:")
    eng = TickEngine(InMemoryEventSource(), clock=clock)
    spec = AppSpec.model_validate(spec_dict())
    db.upsert_app(spec, t=T0)
    rt = eng.add_app(spec)
    eng.tick_once()
    rt.last.candidates = [
        Candidate(
            id="c1",
            lat=45.002,
            lon=10.003,
            radius_m=20,
            source="dwell_clusters",
            stats={},
            contributing_layers=["crowd_a.density"],
            persisted_ticks=4,
        )
    ]
    store = CandidateStore()

    def ctx(actor="agent", agent="critic"):
        return ToolContext("rev-app", db, eng, actor=actor, agent=agent, clock=clock, candidates=store)

    return clock, db, rt, ctx


def dev(ctx):
    return ctx(actor="dev", agent=None)


def agent_weights(ctx, new, bt="bt-1"):
    c = ctx()
    final = guards.check_weights(c, new, methods_with_data=3, authority_evidence=False)
    return guards.apply_weights(c, final, reason="agent tuned weights", backtest_id=bt)


# -- weights -----------------------------------------------------------------------


def test_weight_revert_restores_the_whole_vector_and_links_back(env):
    clock, db, rt, ctx = env
    changes = agent_weights(ctx, {"crowd_a.density": 0.6})
    assert {c.target for c in changes} == {"crowd_a.density", "crowd_b.density"}
    target = next(c for c in changes if c.target == "crowd_a.density")
    clock.advance(5)
    new = revert_tools.revert_change(dev(ctx), target.id)
    assert isinstance(new, Change) and new.reverts == target.id and new.actor == "dev"
    assert new.target == "crowd_a.density" and new.after == {"weight": pytest.approx(0.5)}
    assert rt.weights["crowd_a.density"] == pytest.approx(0.5) and rt.weights["crowd_b.density"] == pytest.approx(0.5)
    sib = next(c for c in changes if c.target == "crowd_b.density")
    assert any(c.reverts == sib.id for c in db.list_changes("rev-app", kind="weight"))
    assert guards.flexible_total(ctx()) == pytest.approx(1.0)
    assert rt.weights["auth.density"] == 1.0 and rt.weights["docs.density"] == 0.5  # untouched

    # reverting the revert re-applies the agent's vector
    clock.advance(5)
    again = revert_tools.revert_change(dev(ctx), new.id)
    assert again.reverts == new.id
    assert {c.target: rt.weights[c.target] for c in changes} == {
        c.target: pytest.approx(c.after["weight"]) for c in changes
    }


def test_weight_revert_is_stale_after_a_later_change(env):
    clock, _, rt, ctx = env
    first = agent_weights(ctx, {"crowd_a.density": 0.6})[0]
    clock.advance(5)
    agent_weights(ctx, {"crowd_a.density": 0.55}, bt="bt-2")
    snap = dict(rt.weights)
    with pytest.raises(RevertStale):
        revert_tools.revert_change(dev(ctx), first.id)
    assert rt.weights == snap  # never silently overwritten
    c = agent_weights(ctx, {"crowd_a.density": 0.5}, bt="bt-3")[0]
    revert_tools.revert_change(dev(ctx), c.id)
    with pytest.raises(RevertStale):  # an already-reverted change is stale too
        revert_tools.revert_change(dev(ctx), c.id)


def test_weight_revert_that_breaks_a_guard_is_rejected(env):
    clock, _, rt, ctx = env
    changes = agent_weights(ctx, {"crowd_a.density": 0.6})
    registry.call_tool(dev(ctx), "pin_weight", {"layer_id": "crowd_b.density", "pinned": True, "reason": "hold"})
    snap = dict(rt.weights)
    with pytest.raises(GuardError, match="pinned"):
        revert_tools.revert_change(dev(ctx), changes[0].id)
    assert rt.weights == snap


def test_pin_change_reverts_by_unpinning(env):
    _, _, _, ctx = env
    out = registry.call_tool(dev(ctx), "pin_weight", {"layer_id": "crowd_a.density", "reason": "hold"})
    new = revert_tools.revert_change(dev(ctx), out["result"]["change_id"])
    assert new.reverts == out["result"]["change_id"] and new.after["pinned"] is False
    assert "crowd_a.density" not in guards.pinned_layers(ctx())


def test_agents_cannot_revert(env):
    _, _, _, ctx = env
    c = agent_weights(ctx, {"crowd_a.density": 0.6})[0]
    with pytest.raises(GuardError, match="dev-only"):
        revert_tools.revert_change(ctx(), c.id)


# -- trust -------------------------------------------------------------------------


def _lower(ctx, user, to):
    r = registry.call_tool(ctx(), "set_user_trust", {"user_id": user, "trust": to, "reason": "flagged"})
    assert r["ok"], r
    return r["result"]["change_id"]


def test_trust_revert_within_the_cap_and_back_again(env):
    clock, _, _, ctx = env
    cid = _lower(ctx, "u1", 0.95)
    new = revert_tools.revert_change(dev(ctx), cid)
    assert new.reverts == cid and new.after == {"trust": pytest.approx(1.0)}
    assert guards.current_trust(ctx(), "u1")[0] == pytest.approx(1.0)
    back = revert_tools.revert_change(dev(ctx), new.id)  # lowering again is always allowed
    assert back.reverts == new.id and guards.current_trust(ctx(), "u1")[0] == pytest.approx(0.95)


def test_trust_revert_past_the_increase_cap_is_rejected(env):
    _, _, _, ctx = env
    cid = _lower(ctx, "u2", 0.3)
    with pytest.raises(GuardError, match="exceeds"):
        revert_tools.revert_change(dev(ctx), cid)
    assert guards.current_trust(ctx(), "u2")[0] == pytest.approx(0.3)


# -- POIs --------------------------------------------------------------------------


def _poi_call(ctx, name, **args):
    r = registry.call_tool(ctx(agent="poi_labeler"), name, args)
    assert r["ok"], r
    return r["result"]


def test_poi_create_retire_and_update_reverts(env):
    _, db, rt, ctx = env
    made = _poi_call(ctx, "create_poi", candidate_id="c1", label="spot a", confidence=0.8)
    pid = made["poi_id"]
    undo = revert_tools.revert_change(dev(ctx), made["change_id"])
    assert undo.reverts == made["change_id"] and db.get_poi("rev-app", pid).status == "retired"
    redo = revert_tools.revert_change(dev(ctx), undo.id)  # revert of the revert: reactivated from candidate c1
    assert redo.reverts == undo.id and db.get_poi("rev-app", pid).status == "active"

    upd = _poi_call(ctx, "update_poi", poi_id=pid, label="spot b", confidence=0.9)
    r = revert_tools.revert_change(dev(ctx), upd["change_id"])
    p = db.get_poi("rev-app", pid)
    assert r.reverts == upd["change_id"] and (p.label, p.confidence) == ("spot a", 0.8)

    ret = _poi_call(ctx, "retire_poi", poi_id=pid, reason="gone")
    rt.last.candidates = []  # the candidate is gone: positions only come from current candidates
    with pytest.raises(GuardError, match="no longer a current candidate"):
        revert_tools.revert_change(dev(ctx), ret["change_id"])
    assert db.get_poi("rev-app", pid).status == "retired"


# -- connectors, specs, unknowns ---------------------------------------------------


def test_connector_revert_restores_the_previous_config(env):
    _, db, _, ctx = env
    r = registry.call_tool(ctx(agent="scout"), "create_connector", {"input_id": "docs", "type": "rss", "url": FEED_B})
    assert r["ok"], r
    cid = next(c.id for c in db.list_changes("rev-app", kind="connector") if c.after["url"] == FEED_B)
    new = revert_tools.revert_change(dev(ctx), cid)
    assert new.reverts == cid and db.get_app("rev-app").inputs[3].connector.url == FEED_A
    with pytest.raises(RevertStale):
        revert_tools.revert_change(dev(ctx), cid)


def test_spec_changes_and_unknown_ids(env):
    _, db, _, ctx = env
    for kind in ("spec_confirm", "spec_edit"):
        c = Change(
            id=f"x-{kind}",
            app_id="rev-app",
            kind=kind,
            target="rev-app",
            before="pending_confirmation",
            after="active",
            reason="dev",
            actor="dev",
            t=datetime.fromtimestamp(T0, UTC),
        )
        db.add_change(c)
        with pytest.raises(GuardError, match="PUT"):
            revert_tools.revert_change(dev(ctx), c.id)
    with pytest.raises(RevertNotFound):
        revert_tools.revert_change(dev(ctx), "nope")


# -- the route ---------------------------------------------------------------------


@pytest.fixture
def api():
    clock = SimClock(start=T0)
    prev = clock_mod.set_clock(clock)
    try:
        db = Db(":memory:")
        engine = TickEngine(DbEventSource(db))
        app = create_app(db=db, engine=engine, autostart_engine=False)
        if not any(getattr(r, "path", "").endswith("/revert") for r in app.routes):
            app.include_router(trace_api.router)
        c = TestClient(app)
        assert c.post("/v1/apps", json=spec_dict("rev-api", status="draft")).status_code == 201
        keys = c.post("/v1/apps/rev-api/activate").json()
        ctx = ToolContext("rev-api", db, engine, actor="agent", agent="critic")
        final = guards.check_weights(ctx, {"crowd_a.density": 0.6}, methods_with_data=3, authority_evidence=False)
        changes = guards.apply_weights(ctx, final, reason="agent tuned weights", backtest_id="bt-1")
        yield {"c": c, "auth": {"X-Authority-Key": keys["authority_key"]}, "db": db, "changes": changes}
    finally:
        clock_mod.set_clock(prev)


def test_route_statuses(api):
    c, auth, db = api["c"], api["auth"], api["db"]
    cid = api["changes"][0].id
    url = f"/v1/apps/rev-api/changes/{cid}/revert"
    assert c.post(url).status_code == 401
    assert c.post(url, headers={"X-Authority-Key": "wrong"}).status_code == 401
    assert c.post(f"/v1/apps/nope/changes/{cid}/revert", headers=auth).status_code == 404
    assert c.post("/v1/apps/rev-api/changes/nope/revert", headers=auth).status_code == 404
    confirm = next(ch for ch in db.list_changes("rev-api") if ch.kind == "spec_confirm")
    r = c.post(f"/v1/apps/rev-api/changes/{confirm.id}/revert", headers=auth)
    assert r.status_code == 422 and "PUT" in r.json()["detail"]

    r = c.post(url, headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reverts"] == cid and body["actor"] == "dev" and body["kind"] == "weight"
    assert c.post(url, headers=auth).status_code == 409  # already reverted: stale


def test_route_guard_break_is_422(api):
    c, auth = api["c"], api["auth"]
    c.post("/v1/apps/rev-api/weights/crowd_b.density/pin", json={"pinned": True}, headers=auth)
    r = c.post(f"/v1/apps/rev-api/changes/{api['changes'][0].id}/revert", headers=auth)
    assert r.status_code == 422 and "pinned" in r.json()["detail"]
