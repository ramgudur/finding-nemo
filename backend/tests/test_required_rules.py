"""Required-tests audit: gaps found against CLAUDE.md "Required tests" and the testable rules.

Each test here closes a "missing" or "weak" item from the audit table in the PR body; the items
already covered elsewhere are not repeated. Neutral examples only (rule 13): entities, pings,
generic labels. No network, fixed seeds.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import numpy as np
import pytest
from fastapi.testclient import TestClient
from synthetic import FixedClock, LocalGrid, events_from_xy

from app.core import clock as clock_mod
from app.core.clock import SimClock
from app.core.db import Db
from app.core.models import POI, AppSpec, Backtest, TraceEntry
from app.engine import backtest as bt_mod
from app.engine.combine import normalize
from app.engine.operators import registry as op_registry
from app.engine.tick import InMemoryEventSource, TickEngine
from app.main import DbEventSource, create_app
from app.tools import registry
from app.tools.context import CandidateStore, ToolContext

T0 = 1_800_000_000.0
BBOX = {"min_lat": 0.0, "min_lon": 0.0, "max_lat": 0.01, "max_lon": 0.01}
ALL = ("authority_confirmation", "predictive_check", "cross_input_agreement")


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _call(ctx, name, **args):
    return registry.call_tool(ctx, name, args)


# -- combine: sparse layer and all-zero layer, through a real operator and a real tick -------------


def test_one_event_through_density_stays_under_its_noise_floor_scaled_value():
    """The combine test uses a hand-made blob; this one chains the real operator and its own
    registry default noise floor, as the tick does."""
    grid = LocalGrid()
    params = op_registry.parse_params("density", {})
    events = events_from_xy(grid, [("e1", 100.0, 262.5, 262.5)])
    res = op_registry.run("density", events, params, None, FixedClock(100.0), grid)
    norm = normalize(res.layer, noise_floor=params.noise_floor)
    assert res.layer.max() > 0
    assert norm.max() == pytest.approx(res.layer.max() / params.noise_floor)
    assert norm.max() < 1.0


def test_tick_with_no_events_gives_an_all_zero_finite_score():
    spec = AppSpec.model_validate(
        {
            "app_id": "empty-app",
            "intent": "test",
            "status": "active",
            "grid": {"cell_m": 10, "bbox": BBOX},
            "inputs": [
                {
                    "id": "pings",
                    "kind": "push",
                    "shape": "point",
                    "tier": "flexible",
                    "meaning": "a ping",
                    "operators": [{"op": "density", "weight": 1.0}],
                }
            ],
        }
    )
    eng = TickEngine(InMemoryEventSource(), clock=SimClock(T0))
    eng.add_app(spec)
    eng.tick_once()
    res = eng.latest("empty-app")
    assert np.isfinite(res.score).all() and not res.score.any()
    assert np.isfinite(res.normalized).all() and not res.normalized.any()


# -- scoring tools: stiff hourly cap across applied steps, real failed holdout, CPU determinism ----


def _scoring_spec() -> AppSpec:
    def inp(id_, tier, weight):
        return {
            "id": id_,
            "kind": "push",
            "shape": "point",
            "tier": tier,
            "meaning": "a report",
            "operators": [{"op": "density", "weight": weight, "half_life_s": 60, "sigma_m": 15}],
        }

    return AppSpec.model_validate(
        {
            "app_id": "rules-app",
            "intent": "test",
            "status": "active",
            "grid": {"cell_m": 10, "bbox": {"min_lat": 45.0, "min_lon": 10.0, "max_lat": 45.0045, "max_lon": 10.00635}},
            "inputs": [
                inp("crowd_a", "flexible", 0.5),
                inp("crowd_b", "flexible", 0.5),
                inp("auth", "locked", 1.0),
                inp("docs", "stiff", 0.5),
            ],
            "verification": [{"method": m, "priority": i + 1} for i, m in enumerate(ALL)],
        }
    )


@pytest.fixture
def env():
    clock = SimClock(T0)
    db = Db(":memory:")
    eng = TickEngine(InMemoryEventSource(), clock=clock)
    eng.add_app(_scoring_spec())
    store = CandidateStore()

    def ctx(actor="agent", agent="critic"):
        return ToolContext("rules-app", db, eng, actor=actor, agent=agent, clock=clock, candidates=store)

    return clock, db, eng, ctx


@pytest.fixture
def authority_decides(monkeypatch):
    """Every backtest passes with authority_confirmation deciding (so only the tier rules decide)."""

    def fake(spec, events, *, now, weights, trust=None, candidate_weights=None, candidate_trust=None, **kw):
        scores = {m: 0.5 for m in ALL}
        bt = Backtest(
            id=f"bt{np.random.default_rng().integers(1 << 40)}",
            kind="single",
            candidates_tested=1,
            candidates_valid=1,
            train_scores=scores,
            holdout_scores=scores,
            passed=True,
            device="cpu",
            elapsed_ms=1.0,
            t=datetime.fromtimestamp(now, UTC),
        )
        return bt_mod.BacktestOutcome(
            bt, "scripted", baseline_train=scores, baseline_holdout=scores, deciding_method="authority_confirmation"
        )

    monkeypatch.setattr("app.tools.scoring_tools.bt_mod.run_backtest", fake)


def _move(ctx, layer, to):
    r = _call(ctx(), "run_backtest", weights={layer: to}, reason="authority agrees")
    if not r["ok"]:
        return r
    return _call(ctx(), "apply_changes", backtest_id=r["result"]["backtest_id"], reason="holdout improved")


def test_stiff_cap_is_per_hour_across_applied_steps_not_per_step(env, authority_decides):
    """tiers.py checks the hour window on hand-built history; this checks the tools feed it the
    real applied history, so two small stiff steps can't add up past +-10% in an hour."""
    clock, db, eng, ctx = env
    assert _move(ctx, "docs.density", 0.54)["ok"]  # +8%
    clock.advance(600)
    r = _move(ctx, "docs.density", 0.58)  # +7% on the step, +16% on the hour
    assert not r["ok"], r
    assert eng.apps["rules-app"].weights["docs.density"] == pytest.approx(0.54)
    clock.advance(3600)
    assert _move(ctx, "docs.density", 0.58)["ok"]  # an hour on, the reference has moved


def test_flexible_step_over_25_percent_is_rejected_by_the_tool(env, authority_decides):
    _, db, eng, ctx = env
    # bounds hold on the renormalized weight: 0.5 -> 1.5 renormalizes to 0.75 (+50%)
    r = _call(ctx(), "run_backtest", weights={"crowd_a.density": 1.5}, reason="try")
    assert not r["ok"] and "out of bounds" in r["error"]
    assert eng.apps["rules-app"].weights["crowd_a.density"] == 0.5
    assert db.list_backtests("rules-app") == []
    # an accepted step moves every flexible weight by at most 25%, and their sum holds
    r = _call(ctx(), "run_backtest", weights={"crowd_a.density": 0.7}, reason="try")
    assert r["ok"], r
    tested = r["result"]["tested_weights"]
    assert all(abs(w - 0.5) <= 0.25 * 0.5 + 1e-9 for w in tested.values())
    assert sum(tested.values()) == pytest.approx(1.0)


def test_apply_changes_rejects_a_real_backtest_whose_holdout_failed(env):
    """No scripting: with no events the real backtest has insufficient holdout data, so it fails,
    and apply_changes must refuse it and leave trust alone."""
    _, db, _, ctx = env
    assert _call(ctx(), "set_user_trust", user_id="e1", trust=0.3, reason="duplicate coords")["ok"]
    r = _call(ctx(), "run_backtest", trust={"e1": 0.35}, reason="recovering")
    assert r["ok"], r
    assert r["result"]["passed"] is False
    a = _call(ctx(), "apply_changes", backtest_id=r["result"]["backtest_id"], reason="apply anyway")
    assert not a["ok"]
    assert db.get_trust("rules-app", "e1").score == 0.3


def test_batch_cpu_results_are_deterministic_with_a_fixed_seed():
    """The existing determinism test uses whichever backend is present; pin it to numpy (CPU)."""
    from test_backtest_batch import _run

    a, b = _run(seed=11, xp=np), _run(seed=11, xp=np)
    assert a.backtest.device == b.backtest.device == "cpu"
    assert a.backtest.candidates_valid == b.backtest.candidates_valid
    assert [c.weights for c in a.candidates] == [c.weights for c in b.candidates]
    assert [c.train for c in a.candidates] == [c.train for c in b.candidates]
    assert a.backtest.train_scores == b.backtest.train_scores
    assert a.backtest.holdout_scores == b.backtest.holdout_scores


# -- rule 4: locked stays locked after confirmation -------------------------------------------------


def _active_spec_dict() -> dict:
    return {
        "app_id": "locked-app",
        "intent": "find where entities gather",
        "status": "active",
        "grid": {"cell_m": 10, "bbox": BBOX},
        "inputs": [
            {
                "id": "pings",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "a ping",
                "operators": [{"op": "density", "weight": 1.0}],
                "plausibility": {"max_speed_mps": 5},
            },
            {
                "id": "markers",
                "kind": "push",
                "shape": "fixed_point",
                "tier": "locked",
                "meaning": "an authority marker",
                "operators": [{"op": "override", "weight": 1.0}],
            },
            {
                "id": "docs",
                "kind": "pull",
                "shape": "document",
                "tier": "stiff",
                "meaning": "documents about the area",
                "connector": {"type": "rss", "url": "https://feeds.example.org/a.xml", "poll_s": 300},
                "operators": [{"op": "event_points", "weight": 0.5}],
            },
        ],
        "verification": [{"method": "authority_confirmation", "priority": 1}],
    }


def test_no_agent_tool_can_change_tiers_or_verification_after_confirmation(monkeypatch):
    from app.connectors import url_guard

    monkeypatch.setattr(url_guard, "check_url", lambda url, **kw: url)
    db = Db(":memory:")
    db.upsert_app(AppSpec.model_validate(_active_spec_dict()), t=1.0)

    def snapshot():
        s = db.get_app("locked-app")
        return s.status, [(i.id, i.tier) for i in s.inputs], [(v.method, v.priority) for v in s.verification]

    before = snapshot()
    compiler = ToolContext(app_id="locked-app", db=db, engine=object(), agent="compiler")
    # finalize_app with every tier flipped to flexible and verification swapped
    proposal = _active_spec_dict()
    proposal["status"] = "pending_confirmation"
    for i in proposal["inputs"]:
        i["tier"] = "flexible"
    proposal["verification"] = [{"method": "predictive_check", "priority": 1}]
    assert not _call(compiler, "finalize_app", spec=proposal)["ok"]
    r = _call(
        compiler,
        "create_input",
        id="extra",
        kind="push",
        shape="point",
        tier="locked",
        meaning="x",
        operators=[{"op": "density", "weight": 1.0}],
    )
    assert not r["ok"]
    # the Scout may reconfigure a pull input's connector on an active app, but nothing else moves
    scout = ToolContext(app_id="locked-app", db=db, engine=object(), agent="scout")
    r = _call(scout, "create_connector", input_id="docs", type="rss", url="https://feeds.example.org/b.xml", poll_s=600)
    assert r["ok"], r
    assert snapshot() == before
    # and no tool offered to a post-confirmation agent takes a tier or verification argument
    for agent in ("critic", "scout", "poi_labeler"):
        for t in registry.tool_schemas(agent):
            props = set(t["function"]["parameters"].get("properties", {}))
            assert not props & {"tier", "verification", "status"}, (agent, t["function"]["name"])


# -- spec isn't active (or live) without dev confirmation -----------------------------------------


def _ping_spec(app_id: str = "confirm-app", **extra) -> dict:
    return {
        "app_id": app_id,
        "intent": "find where entities gather",
        "grid": {"cell_m": 10, "bbox": BBOX},
        "inputs": [
            {
                "id": "pings",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "a ping means an entity is here",
                "operators": [{"op": "density", "weight": 1.0}],
            }
        ],
        "outputs": {"layers": [{"id": "score", "visibility": "public"}, {"id": "pings.density"}]},
        **extra,
    }


def test_spec_is_not_live_until_the_dev_confirms_it():
    db = Db(":memory:")
    engine = TickEngine(DbEventSource(db), clock=SimClock(T0))
    app = create_app(db=db, engine=engine, autostart_engine=False)
    with TestClient(app) as c:
        assert c.post("/v1/apps", json={**_ping_spec(), "status": "pending_confirmation"}).status_code == 201
        engine.tick_once()
        assert "confirm-app" not in engine.apps
        assert c.get("/v1/apps/confirm-app/map").status_code == 404
        assert db.list_changes("confirm-app", kind="spec_confirm") == []
        assert db.get_app("confirm-app").status == "pending_confirmation"

        keys = c.post("/v1/apps/confirm-app/activate").json()
        assert db.get_app("confirm-app").status == "active" and "confirm-app" in engine.apps
        (chg,) = db.list_changes("confirm-app", kind="spec_confirm")
        assert chg.actor == "dev" and chg.before == "pending_confirmation" and chg.after == "active"

        # ingest is refused for any non-active spec, even with a valid ingest key
        db.upsert_app(db.get_app("confirm-app").model_copy(update={"status": "pending_confirmation"}), t=T0)
        r = c.post(
            "/v1/apps/confirm-app/inputs/pings/events",
            json={"user_id": "e1", "lat": 0.005, "lon": 0.005, "ts": _iso(T0)},
            headers={"X-Ingest-Key": keys["ingest_key"]},
        )
        assert r.status_code == 403


# -- rule 8: public REST and WS never carry raw events, the trace or authority POIs -----------------

SECRET_IDS = [f"entity-zq{k}" for k in range(6)]
SECRET_LAT, SECRET_LON = 0.0051234, 0.0047891
SECRET_LABEL = "restricted-label-q7"
SECRET_WHY = "trace-only-reason-k3"


def _poi(poi_id, label, visibility, lat=0.005, lon=0.005):
    now = datetime.fromtimestamp(T0, UTC)
    return POI(
        id=poi_id,
        candidate_id=f"c-{poi_id}",
        lat=lat,
        lon=lon,
        radius_m=10.0,
        label=label,
        confidence=0.8,
        visibility=visibility,
        status="active",
        created_at=now,
        updated_at=now,
    )


def test_public_rest_and_ws_never_carry_raw_events_trace_or_authority_pois():
    db = Db(":memory:")
    engine = TickEngine(DbEventSource(db), clock=SimClock(T0))
    app = create_app(db=db, engine=engine, autostart_engine=False)
    with TestClient(app) as c:
        c.post("/v1/apps", json=_ping_spec("vis-app"))
        keys = c.post("/v1/apps/vis-app/activate").json()
        for uid in SECRET_IDS:
            r = c.post(
                "/v1/apps/vis-app/inputs/pings/events",
                json={"user_id": uid, "lat": SECRET_LAT, "lon": SECRET_LON, "ts": _iso(T0)},
                headers={"X-Ingest-Key": keys["ingest_key"]},
            )
            assert r.status_code == 202
        engine.tick_once()
        db.upsert_poi("vis-app", _poi("p-auth", SECRET_LABEL, "authority"), t=T0)
        db.add_trace(
            TraceEntry(t=datetime.fromtimestamp(T0, UTC), app_id="vis-app", agent="critic", saw="x", why=SECRET_WHY)
        )

        public_bodies = [
            c.get("/v1/apps/vis-app/map").text,
            c.get("/v1/apps/vis-app/hotspots").text,
            c.get("/v1/apps/vis-app/pois").text,
        ]
        assert c.get("/v1/apps/vis-app/inputs/pings/raw").status_code == 401
        assert c.get("/v1/apps/vis-app/trace").status_code == 401
        with c.websocket_connect("/v1/apps/vis-app/stream") as ws:
            frame = ws.receive_json()
        assert set(frame) == {"type", "t", "map", "hotspots"}
        public_bodies.append(json.dumps(frame))

        for body in public_bodies:
            for uid in SECRET_IDS:
                assert uid not in body
            assert str(SECRET_LAT) not in body and str(SECRET_LON) not in body
            assert SECRET_LABEL not in body and SECRET_WHY not in body

        # sanity: the same data is there for the authority view, so the checks above aren't vacuous
        auth = {"X-Authority-Key": keys["authority_key"]}
        raw = c.get("/v1/apps/vis-app/inputs/pings/raw", headers=auth).text
        assert all(uid in raw for uid in SECRET_IDS)
        assert SECRET_LABEL in c.get("/v1/apps/vis-app/pois", headers=auth).text
        assert SECRET_WHY in c.get("/v1/apps/vis-app/trace", headers=auth).text


# -- instructions: the whole public response, at the API, with other entities and a sensitive POI --


def test_instruction_response_never_carries_other_entities_or_sensitive_labels():
    spec = _ping_spec(
        "route-app",
        outputs={
            "layers": [{"id": "score", "visibility": "public"}, {"id": "pings.density"}],
            "poi_labels": {
                "label_set": [
                    {"label": "meeting area", "destination": True},
                    {"label": SECRET_LABEL, "sensitive": True},
                ]
            },
        },
        routing={
            "enabled": True,
            "destination_points": [{"lat": 0.0095, "lon": 0.0095, "label": "meeting area"}],
            "impassable_score": 0.8,
            "alpha": 4.0,
        },
    )
    clock = SimClock(T0)
    prev = clock_mod.set_clock(clock)
    try:
        db = Db(":memory:")
        engine = TickEngine(DbEventSource(db))
        app = create_app(db=db, engine=engine, autostart_engine=False)
        with TestClient(app) as c:
            assert c.post("/v1/apps", json=spec).status_code == 201
            keys = c.post("/v1/apps/route-app/activate").json()
            for uid in SECRET_IDS:
                c.post(
                    "/v1/apps/route-app/inputs/pings/events",
                    json={"user_id": uid, "lat": SECRET_LAT, "lon": SECRET_LON, "ts": _iso(T0)},
                    headers={"X-Ingest-Key": keys["ingest_key"]},
                )
            db.upsert_poi("route-app", _poi("p-s", SECRET_LABEL, "authority", lat=0.003, lon=0.003), t=T0)
            engine.tick_once()
            r = c.post("/v1/apps/route-app/instruction", json={"lat": 0.0015, "lon": 0.0015})
            assert r.status_code == 200
            ins = r.json()
            assert ins["guard"]["passed"] is True, ins["guard"]
            assert ins["destination"]["label"] == "meeting area"
            body = r.text
            for uid in SECRET_IDS:
                assert uid not in body
            assert str(SECRET_LAT) not in body and str(SECRET_LON) not in body
            assert SECRET_LABEL.lower() not in body.lower()
            assert len(ins["text"]) <= 360
    finally:
        clock_mod.set_clock(prev)
