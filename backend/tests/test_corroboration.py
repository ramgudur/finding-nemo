"""Corroboration by reference inputs: the Critic's read-only `get_corroboration` tool, the
reference-data trigger, the "run an agent now" route, how lowered trust fades a crowd's cells
(and re-normalization lifts the rest), and rule 8 for all of it. No network, no live LLM,
SimClock, fixed seeds."""

import asyncio

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.agents import critic as critic_mod
from app.agents import poi_labeler as poi_mod
from app.agents.critic import CriticScheduler, reference_trigger
from app.core import clock as clock_mod
from app.core.clock import SimClock
from app.core.db import Db
from app.core.models import AppSpec, Event
from app.engine.tick import InMemoryEventSource, TickEngine
from app.main import DbEventSource, create_app
from app.tools import registry
from app.tools.context import CandidateStore, ToolContext

T0 = 1_800_000_000.0
BBOX = {"min_lat": 45.0, "min_lon": 10.0, "max_lat": 45.0045, "max_lon": 10.00635}  # ~500 x 500 m
APP = "corr-app"


def _spec_dict(status="active") -> dict:
    return {
        "app_id": APP,
        "intent": "map where items are reported",
        "status": status,
        "grid": {"cell_m": 10, "bbox": BBOX},
        "inputs": [
            {
                "id": "crowd",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "a report from our own app",
                "operators": [{"op": "density", "weight": 1.0, "half_life_s": 120, "sigma_m": 15}],
            },
            {
                "id": "ref",
                "kind": "push",
                "shape": "document",
                "tier": "stiff",
                "meaning": "a trusted report of where the item is",
                "fields": [{"name": "confidence", "type": "number", "required": False}],
                "operators": [{"op": "event_points", "weight": 0.3}],
            },
        ],
        "verification": [
            {"method": "predictive_check", "priority": 1},
            {"method": "cross_input_agreement", "priority": 2},
        ],
        "outputs": {"layers": [{"id": "score", "visibility": "authority"}]},
    }


@pytest.fixture
def env():
    spec = AppSpec.model_validate(_spec_dict())
    clock = SimClock(T0)
    db = Db(":memory:")
    db.upsert_app(spec, t=T0)
    src = InMemoryEventSource()
    eng = TickEngine(src, clock=clock, trust=db.list_trust)
    eng.add_app(spec)
    grid = eng.apps[APP].grid

    def ctx():
        return ToolContext(APP, db, eng, agent="critic", clock=clock, candidates=CandidateStore())

    return clock, db, eng, src, grid, ctx


CLUSTERS = {"a": (100.0, 400.0), "b": (250.0, 250.0), "c": (400.0, 100.0), "d": (420.0, 60.0)}  # (x, y) meters


def _crowd(src, grid, t, n=12, seed=0):
    """n reporters per cluster, user ids '<cluster>-<i>'."""
    rng = np.random.default_rng(seed)
    for name, (x, y) in CLUSTERS.items():
        lat, lon = grid.to_latlon(rng.normal(x, 6, n), rng.normal(y, 6, n))
        src.add(
            APP,
            "crowd",
            [
                {"user_id": f"{name}-{i}", "t": t, "lat": float(a), "lon": float(o)}
                for i, (a, o) in enumerate(zip(lat, lon, strict=True))
            ],
        )


def _ref(src, grid, t, x, y):
    lat, lon = grid.to_latlon(np.array([x]), np.array([y]))
    src.add(
        APP, "ref", [{"user_id": "source-1", "t": t, "lat": float(lat[0]), "lon": float(lon[0]), "confidence": 0.9}]
    )


def _call(ctx, name, **args):
    return registry.call_tool(ctx, name, args)


# -- get_corroboration ------------------------------------------------------------------


def test_get_corroboration_scores_users_near_the_reference(env):
    clock, db, eng, src, grid, ctx = env
    _crowd(src, grid, T0 - 30)
    _ref(src, grid, T0 - 5, *CLUSTERS["a"])  # the reference lands on cluster a
    db.set_trust(APP, "b-0", 0.4, t=T0 - 100)
    r = _call(ctx(), "get_corroboration")
    assert r["ok"], r
    out = r["result"]
    assert out["radius_m"] == 30.0  # 3 x cell_m
    assert out["reference_events"] == 1
    assert out["reference_inputs"] == [{"input_id": "ref", "tier": "stiff", "events": 1}]
    share = {u["user_id"]: u["corroborated_share"] for u in out["users"]}
    assert len(share) == 48 and all(u["input_id"] == "crowd" for u in out["users"])
    assert sum(1 for u, s in share.items() if u.startswith("a-") and s == 1.0) >= 11  # within ~2 sigma of 30 m
    assert all(s == 0.0 for u, s in share.items() if not u.startswith("a-"))
    shares = [u["corroborated_share"] for u in out["users"]]
    assert shares == sorted(shares, reverse=True)  # sorted by share
    assert out["summary"]["users_scored"] == 48
    assert out["summary"]["users_uncorroborated"] == 36
    assert out["summary"]["users_corroborated"] >= 11
    assert all(u["trust"] == 1.0 for u in out["users"] if u["user_id"] != "b-0")
    assert next(u["trust"] for u in out["users"] if u["user_id"] == "b-0") == 0.4


def test_get_corroboration_without_reference_events_says_so(env):
    clock, db, eng, src, grid, ctx = env
    _crowd(src, grid, T0 - 30)
    out = _call(ctx(), "get_corroboration")["result"]
    assert out["reference_events"] == 0 and out["users"] == []
    assert "no reference events" in out["note"]
    _ref(src, grid, T0 - 700, *CLUSTERS["a"])  # older than the 10 min window: still nothing
    assert _call(ctx(), "get_corroboration")["result"]["reference_events"] == 0


def test_get_corroboration_is_read_only_and_critic_only(env):
    clock, db, eng, src, grid, ctx = env
    _crowd(src, grid, T0 - 30)
    _ref(src, grid, T0 - 5, *CLUSTERS["a"])
    assert not registry.TOOLS["get_corroboration"].changes_state
    assert registry.TOOLS["get_corroboration"].agents == frozenset({"critic"})
    for agent in ("compiler", "scout", "poi_labeler"):
        c = ToolContext(APP, db, eng, agent=agent, clock=clock)
        assert not registry.call_tool(c, "get_corroboration", {})["ok"]
    before = (db.list_trust(APP), db.list_changes(APP), len(db.list_trace(APP)))
    _call(ctx(), "get_corroboration")
    assert (db.list_trust(APP), db.list_changes(APP), len(db.list_trace(APP))) == before
    assert not _call(ctx(), "get_corroboration", window_s=601)["ok"]
    assert not _call(ctx(), "get_corroboration", extra=1)["ok"]


def test_lowering_uncorroborated_users_uses_the_existing_guarded_tools(env):
    """The flow the prompt asks for goes through the unchanged guards: lowering is free with a
    reason, raising a user already at 1.0 is rejected."""
    clock, db, eng, src, grid, ctx = env
    _crowd(src, grid, T0 - 30)
    _ref(src, grid, T0 - 5, *CLUSTERS["a"])
    users = _call(ctx(), "get_corroboration")["result"]["users"]
    zero = [u["user_id"] for u in users if u["corroborated_share"] == 0][:3]
    for u in zero:
        r = _call(ctx(), "set_user_trust", user_id=u, trust=0.3, reason="reports not corroborated by ref")
        assert r["ok"], r
    assert {db.get_trust(APP, u).score for u in zero} == {0.3}
    r = _call(ctx(), "set_user_trust", user_id=zero[0], trust=0.5, reason="raise")
    assert not r["ok"]  # set_user_trust only lowers
    trust_changes = [c for c in db.list_changes(APP, kind="trust")]
    assert len(trust_changes) == 3 and all(c.actor == "agent" and c.before["trust"] == 1.0 for c in trust_changes)


# -- lowered trust fades a cluster; re-normalization lifts the others --------------------


def test_lowering_trust_fades_those_cells_and_lifts_the_top_cells_normalized_value(env):
    clock, db, eng, src, grid, ctx = env
    _crowd(src, grid, T0 - 5, n=40)
    (before,) = eng.tick_once()
    li = before.layer_ids.index("crowd.density")
    norm_before = before.normalized[li]
    ra, ca = int(CLUSTERS["a"][1] // 10), int(CLUSTERS["a"][0] // 10)  # top-left cluster's cell
    rc, cc = int(CLUSTERS["c"][1] // 10), int(CLUSTERS["c"][0] // 10)  # a lower-right cluster's cell

    # Lower every user of the lower-right clusters (b, c, d) to 0.3, as the Critic would.
    for name in ("b", "c", "d"):
        for i in range(40):
            r = _call(ctx(), "set_user_trust", user_id=f"{name}-{i}", trust=0.3, reason="not corroborated")
            assert r["ok"]
    (after,) = eng.tick_once()
    norm_after = after.normalized[li]
    # The faded clusters' own cells lose weight relative to the top cluster...
    assert norm_after[rc, cc] / norm_after[ra, ca] < 0.5 * norm_before[rc, cc] / norm_before[ra, ca]
    # ...and because p99 fell (it was set by the busy cells), the top cluster's normalized value rises,
    # so its neighbourhood reads darker too.
    assert norm_after[ra, ca] > 1.2 * norm_before[ra, ca]
    ring = norm_after[ra - 2 : ra + 3, ca - 2 : ca + 3].mean()
    assert ring > norm_before[ra - 2 : ra + 3, ca - 2 : ca + 3].mean()
    assert after.score[ra, ca] >= before.score[ra, ca]


# -- the reference trigger ---------------------------------------------------------------


def _db_env():
    spec = AppSpec.model_validate(_spec_dict())
    clock = SimClock(T0)
    db = Db(":memory:")
    db.upsert_app(spec, t=T0)
    db.create_keys(APP, t=T0)
    eng = TickEngine(DbEventSource(db), clock=clock, trust=db.list_trust)
    eng.add_app(spec)
    return clock, db, eng


def _insert(db, input_id, t, user="u", lat=45.002, lon=10.003, **extra):
    from datetime import UTC, datetime

    db.insert_event(
        APP, input_id, Event(user_id=user, lat=lat, lon=lon, ts=datetime.fromtimestamp(t, UTC), **extra), received_at=t
    )


def test_new_events_on_a_stiff_input_trigger_the_critic(monkeypatch):
    clock, db, eng = _db_env()
    eng.tick_once()
    calls = []

    async def fake_run(ctx, trigger, llm=None):
        calls.append(trigger)

    monkeypatch.setattr(critic_mod, "run_critic", fake_run)
    monkeypatch.setattr(critic_mod, "MIN_INTERVAL_S", 10_000.0)
    sched = CriticScheduler(db, eng)
    asyncio.run(sched.check_once())
    assert calls == []  # nothing yet
    _insert(db, "crowd", T0, user="c1")  # flexible input: no reference trigger
    asyncio.run(sched.check_once())
    assert calls == []
    _insert(db, "ref", T0, user="source-1", confidence=0.9)
    asyncio.run(sched.check_once())
    assert calls == ["new reference data on ref"]
    asyncio.run(sched.check_once())
    assert calls == ["new reference data on ref"]  # same data: no second run

    # More reference data inside the 15 s cooldown is kept pending, then runs once it ends.
    clock.advance(5)
    _insert(db, "ref", T0 + 5, user="source-1", confidence=0.9, lat=45.003)
    asyncio.run(sched.check_once())
    assert len(calls) == 1
    clock.advance(11)
    asyncio.run(sched.check_once())
    assert calls == ["new reference data on ref", "new reference data on ref"]


def test_first_sight_of_old_reference_data_does_not_trigger():
    clock, db, eng = _db_env()
    _insert(db, "ref", T0 - 3600, user="source-1")
    st = critic_mod._AppTriggerState(last_run_at=T0)
    assert reference_trigger(db, eng.apps[APP].spec, st, T0) is None
    _insert(db, "ref", T0, user="source-1", lat=45.003)
    assert reference_trigger(db, eng.apps[APP].spec, st, T0) == "new reference data on ref"


def test_the_task_prompt_points_a_reference_run_at_get_corroboration():
    assert "get_corroboration" in critic_mod._task_prompt("new reference data on ref")
    assert "get_corroboration" not in critic_mod._task_prompt("a new hotspot appeared (1)")


# -- POST /v1/apps/{app}/agents/{agent}/run ----------------------------------------------


@pytest.fixture()
def api():
    clock = SimClock(start=T0)
    prev = clock_mod.set_clock(clock)
    try:
        db = Db(":memory:")
        app = create_app(db=db, autostart_engine=False)
        c = TestClient(app)
        body = _spec_dict(status="draft")
        body["outputs"]["poi_labels"] = {"mode": "auto", "label_set": [{"label": "spot"}]}
        assert c.post("/v1/apps", json=body).status_code == 201
        key = c.post(f"/v1/apps/{APP}/activate").json()["authority_key"]
        yield {"c": c, "db": db, "app": app, "auth": {"X-Authority-Key": key}, "clock": clock}
    finally:
        clock_mod.set_clock(prev)


def _run(api, agent="critic", headers=None, app_id=APP):
    return api["c"].post(f"/v1/apps/{app_id}/agents/{agent}/run", headers=api["auth"] if headers is None else headers)


def test_run_now_queues_a_triggered_run_then_429_inside_the_cooldown(api, monkeypatch):
    r = _run(api)
    assert r.status_code == 202 and r.json() == {"agent": "critic", "queued": True}
    sched = api["app"].state.critic_scheduler
    assert sched._state[APP].pending_trigger == critic_mod.RUN_NOW_TRIGGER

    # The scheduler runs it through the normal trigger path (cooldown and budget as usual).
    calls = []

    async def fake_run(ctx, trigger, llm=None):
        calls.append(trigger)

    monkeypatch.setattr(critic_mod, "run_critic", fake_run)
    api["app"].state.engine.tick_once()
    asyncio.run(sched.check_once())
    assert calls == [critic_mod.RUN_NOW_TRIGGER]

    r = _run(api)
    assert r.status_code == 429 and 1 <= int(r.headers["Retry-After"]) <= 15
    api["clock"].advance(16)
    assert _run(api).status_code == 202


def test_run_now_for_the_poi_labeler(api, monkeypatch):
    r = _run(api, "poi_labeler")
    assert r.status_code == 202 and r.json() == {"agent": "poi_labeler", "queued": True}
    calls = []

    async def fake_run(ctx, trigger, llm=None):
        calls.append(trigger)

    monkeypatch.setattr(poi_mod, "run_poi_labeler", fake_run)
    api["app"].state.engine.tick_once()
    asyncio.run(api["app"].state.poi_labeler_scheduler.check_once())
    assert calls == [poi_mod.RUN_NOW_TRIGGER]  # runs even with no candidates yet
    assert _run(api, "poi_labeler").status_code == 429


def test_run_now_needs_the_key_an_active_app_and_a_known_agent(api):
    assert _run(api, headers={}).status_code == 401
    assert _run(api, headers={"X-Authority-Key": "wrong"}).status_code == 401
    assert _run(api, app_id="nope").status_code == 404
    assert _run(api, "scout").status_code == 422
    body = _spec_dict(status="draft")
    body["app_id"] = "draft-app"
    api["c"].post("/v1/apps", json=body)
    keys = api["db"].create_keys("draft-app", T0)
    r = _run(api, headers={"X-Authority-Key": keys.authority_key}, app_id="draft-app")
    assert r.status_code == 409


# -- rule 8: none of it reaches a public request -----------------------------------------


def test_nothing_from_corroboration_or_line_simulation_leaks_to_public_rest_or_ws(api):
    c, db = api["c"], api["db"]
    body = {
        "input_id": "crowd",
        "mode": "line",
        "from_point": {"lat": 45.0005, "lon": 10.0005},
        "to_point": {"lat": 45.004, "lon": 10.006},
        "clusters": 4,
        "count": 3,
        "radius_m": 20,
        "duration_s": 0,
    }
    assert c.post(f"/v1/apps/{APP}/simulate", json=body, headers=api["auth"]).status_code == 202
    _insert(db, "ref", T0, user="source-1", confidence=0.9)
    api["app"].state.engine.tick_once()
    assert _run(api).status_code == 202

    # Public REST: no raw events, no trace, no simulation or run details.
    assert c.get(f"/v1/apps/{APP}/inputs/crowd/raw").status_code == 401
    assert c.get(f"/v1/apps/{APP}/trace").status_code == 401
    assert c.get(f"/v1/apps/{APP}/sources/status").status_code == 401
    pub = [
        c.get(f"/v1/apps/{APP}/map?format=geojson"),
        c.get(f"/v1/apps/{APP}/hotspots"),
        c.get(f"/v1/apps/{APP}/pois"),
    ]
    for r in pub:
        assert r.status_code == 200
        text = r.text
        assert "line-" not in text and "source-1" not in text and "corroborat" not in text
    assert pub[0].json()["layers"] == {}  # every layer is authority here

    # Public WebSocket: the tick frame carries public layers only.
    with c.websocket_connect(f"/v1/apps/{APP}/stream") as ws:
        api["app"].state.engine.tick_once()
        frames = []
        for _ in range(3):
            try:
                frames.append(ws.receive_json(mode="text"))
            except Exception:  # noqa: BLE001
                break
            if frames[-1].get("type") == "tick":
                break
        for f in frames:
            s = str(f)
            assert "line-" not in s and "source-1" not in s and "corroborat" not in s
            if f.get("type") == "tick":
                assert f["map"]["layers"] == {}
