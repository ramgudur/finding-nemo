import asyncio
import json

import pytest
from fake_llm import FakeAsyncOpenAI

from app.agents import critic as critic_mod
from app.agents.critic import (
    CriticScheduler,
    _AppTriggerState,
    hotspot_trigger,
    override_trigger,
    run_critic,
    verification_drop_trigger,
)
from app.agents.llm import LlmRouter
from app.core.clock import SimClock
from app.core.config import Settings
from app.core.db import Db
from app.core.models import Hotspot
from app.engine.tick import InMemoryEventSource, TickEngine
from app.tools.context import ToolContext

T0 = 1_800_000_000.0


def _spec(app_id: str = "test-app") -> dict:
    return {
        "app_id": app_id,
        "intent": "find where people gather",
        "grid": {"cell_m": 10, "bbox": {"min_lat": 0.0, "min_lon": 0.0, "max_lat": 0.01, "max_lon": 0.01}},
        "inputs": [
            {
                "id": "panic",
                "kind": "push",
                "shape": "track",
                "tier": "flexible",
                "meaning": "a panic button press starts a GPS stream",
                "operators": [{"op": "density", "weight": 1.0}],
            }
        ],
        "outputs": {"layers": [{"id": "score", "visibility": "public"}, {"id": "panic.density"}]},
    }


def _ticked_ctx(app_id: str = "test-app", agent: str = "critic") -> tuple[ToolContext, TickEngine]:
    from app.core.models import AppSpec

    db = Db(":memory:")
    spec = AppSpec.model_validate({**_spec(app_id), "status": "active"})
    db.upsert_app(spec, t=T0)
    db.create_keys(app_id, t=T0)
    clock = SimClock(start=T0)
    source = InMemoryEventSource()
    source.add(
        app_id,
        "panic",
        [{"user_id": f"u{i}", "t": T0, "lat": 0.005, "lon": 0.005} for i in range(5)],
    )
    engine = TickEngine(source, clock=clock)
    engine.add_app(spec)
    engine.tick_once()
    ctx = ToolContext(app_id=app_id, db=db, engine=engine, agent=agent, clock=clock)
    return ctx, engine


def _fake_router(script) -> LlmRouter:
    fake = FakeAsyncOpenAI(script)
    return LlmRouter(Settings(_env_file=None), clients={"nvidia": fake, "openrouter": fake})


@pytest.fixture(autouse=True)
def _isolated_flywheel(tmp_path, monkeypatch):
    path = tmp_path / "critic.jsonl"
    monkeypatch.setattr(critic_mod, "FLYWHEEL_PATH", path)
    return path


def _flywheel_lines(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


# -- run_critic: full episodes against real tools -----------------------------------


def test_cold_start_episode_reads_the_report_and_changes_nothing(_isolated_flywheel):
    ctx, _ = _ticked_ctx()
    result = asyncio.run(run_critic(ctx, "scheduled check", _fake_router("critic_cold_start")))
    assert result == "Cold start: fewer than two verification methods have data, so no weight changes yet."
    lines = _flywheel_lines(_isolated_flywheel)
    assert len(lines) == 1
    assert lines[0]["app_id"] == "test-app"
    assert lines[0]["accepted"] is False
    assert lines[0]["trigger"] == "scheduled check"


def test_lowering_trust_is_recorded_as_accepted(_isolated_flywheel):
    ctx, _ = _ticked_ctx()
    script = {
        "scenario": "s",
        "description": "d",
        "steps": [
            {
                "response": {
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "name": "set_user_trust",
                            "arguments": {"user_id": "u0", "trust": 0.2, "reason": "duplicate coordinates"},
                        }
                    ]
                }
            },
            {"response": {"content": "Lowered trust for one duplicate account."}},
        ],
    }
    result = asyncio.run(run_critic(ctx, "a new hotspot appeared (1)", _fake_router(script)))
    assert result == "Lowered trust for one duplicate account."
    assert ctx.db.get_trust("test-app", "u0").score == 0.2
    lines = _flywheel_lines(_isolated_flywheel)
    assert lines[0]["accepted"] is True
    assert lines[0]["trigger"] == "a new hotspot appeared (1)"


def test_rate_limited_run_still_appends_a_flywheel_line(_isolated_flywheel):
    ctx, _ = _ticked_ctx()
    router = _fake_router({"scenario": "s", "description": "d", "steps": [{"response": {"content": "unused"}}]})
    router._provider("nvidia").bucket.tokens = 0.0
    result = asyncio.run(run_critic(ctx, "scheduled check", router))
    assert result is None
    lines = _flywheel_lines(_isolated_flywheel)
    assert len(lines) == 1
    assert lines[0]["accepted"] is False and lines[0]["summary"] == ""


# -- triggers -------------------------------------------------------------------------


def _hotspot(id_: str, visibility: str = "authority") -> Hotspot:
    return Hotspot(
        id=id_, lat=1.0, lon=2.0, radius_m=5.0, score=0.9, mean=0.5, area_m2=78.5, n_cells=1,
        persisted_ticks=0, visibility=visibility,
    )  # fmt: skip


class _Result:
    def __init__(self, hotspots=(), override_any=False):
        self.hotspots = list(hotspots)
        self.override = _OverrideArray(override_any)


class _OverrideArray:
    def __init__(self, any_: bool):
        self._any = any_

    def any(self) -> bool:
        return self._any


def test_hotspot_trigger_fires_only_for_new_ids():
    st = _AppTriggerState(last_run_at=0.0)
    assert hotspot_trigger(st, _Result(hotspots=[_hotspot("h1")])) is not None
    assert hotspot_trigger(st, _Result(hotspots=[_hotspot("h1")])) is None  # already seen
    assert hotspot_trigger(st, _Result(hotspots=[_hotspot("h1"), _hotspot("h2")])) is not None  # h2 is new


def test_override_trigger_fires_only_on_the_rising_edge():
    st = _AppTriggerState(last_run_at=0.0)
    assert override_trigger(st, _Result(override_any=False)) is None
    assert override_trigger(st, _Result(override_any=True)) is not None
    assert override_trigger(st, _Result(override_any=True)) is None  # still active, not a new edge
    assert override_trigger(st, _Result(override_any=False)) is None  # clearing isn't a trigger either
    assert override_trigger(st, _Result(override_any=True)) is not None  # active again: a new edge


def test_verification_drop_trigger_needs_two_readings(monkeypatch):
    ctx, _ = _ticked_ctx()
    st = _AppTriggerState(last_run_at=0.0)
    values = [0.8, 0.8, 0.5, 0.5]

    def fake_call_tool(_ctx, _name, _args):
        return {"ok": True, "result": {"methods": {"m": {"has_data": True, "value": values.pop(0)}}}}

    monkeypatch.setattr(critic_mod, "call_tool", fake_call_tool)
    assert verification_drop_trigger(ctx, st) is None  # first reading: nothing to compare yet
    assert verification_drop_trigger(ctx, st) is None  # 0.8 -> 0.8: no drop
    assert verification_drop_trigger(ctx, st) is not None  # 0.8 -> 0.5: a real drop
    assert verification_drop_trigger(ctx, st) is None  # 0.5 -> 0.5: stable again


def test_verification_drop_trigger_handles_a_rejected_tool_call(monkeypatch):
    ctx, _ = _ticked_ctx()
    st = _AppTriggerState(last_run_at=0.0)
    monkeypatch.setattr(critic_mod, "call_tool", lambda *_a: {"ok": False, "error": "not enough ticks cached yet"})
    assert verification_drop_trigger(ctx, st) is None


# -- scheduler --------------------------------------------------------------------------


def test_scheduler_runs_the_critic_on_a_new_hotspot(monkeypatch):
    ctx, engine = _ticked_ctx()
    calls = []

    async def fake_run_critic(ctx, trigger, llm=None):
        calls.append(trigger)
        return None

    monkeypatch.setattr(critic_mod, "run_critic", fake_run_critic)
    scheduler = CriticScheduler(ctx.db, engine)
    engine.apps["test-app"].last.hotspots = [_hotspot("h1")]

    asyncio.run(scheduler.check_once())
    assert calls == ["a new hotspot appeared (1)"]
    asyncio.run(scheduler.check_once())
    assert calls == ["a new hotspot appeared (1)"]  # same hotspot again: no second run


def test_scheduler_falls_back_to_a_scheduled_check_after_min_interval(monkeypatch):
    ctx, engine = _ticked_ctx()
    calls = []

    async def fake_run_critic(ctx, trigger, llm=None):
        calls.append(trigger)
        return None

    monkeypatch.setattr(critic_mod, "run_critic", fake_run_critic)
    monkeypatch.setattr(critic_mod, "MIN_INTERVAL_S", 10.0)
    scheduler = CriticScheduler(ctx.db, engine)

    # the 5 seeded events form a real density hotspot, so the very first check legitimately
    # fires on "new hotspot"; consume that before testing the interval fallback itself.
    asyncio.run(scheduler.check_once())
    calls.clear()

    asyncio.run(scheduler.check_once())
    assert calls == []  # same hotspot, under the interval: nothing yet

    engine.clock.set(T0 + 11.0)
    asyncio.run(scheduler.check_once())
    assert calls == ["scheduled check"]


def test_scheduler_start_and_stop_lifecycle(monkeypatch):
    ctx, engine = _ticked_ctx()
    calls = []

    async def fake_run_critic(ctx, trigger, llm=None):
        calls.append(trigger)
        return None

    monkeypatch.setattr(critic_mod, "run_critic", fake_run_critic)

    async def go():
        scheduler = CriticScheduler(ctx.db, engine, poll_s=0.01)
        scheduler.start()
        await asyncio.sleep(0.03)
        await scheduler.stop()
        return scheduler

    scheduler = asyncio.run(go())
    assert scheduler._task is None
