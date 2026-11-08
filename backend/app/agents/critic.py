"""`agents/critic.py`: the Critic -- tunes layer weights and per-user trust under guards.

`run_critic` is one episode: build the task prompt from a trigger reason, run the
generic tool-calling loop (`agents.loop.run_agent`), then append a line to
`data/flywheel/critic.jsonl` regardless of outcome (a skipped or no-op run still
records that nothing happened).

`CriticScheduler` decides *when* to call it: on a new hotspot, a newly active authority
override, a drop in the deciding verification method, or at least every `MIN_INTERVAL_S`.
It runs as its own asyncio task, reading only cached/latest tick state, so a Critic run
(async, LLM-bound) never blocks the 2 s tick (rule 2).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.agents.llm import LlmRouter
from app.agents.loop import run_agent
from app.core.db import Db
from app.engine.tick import TickEngine, TickResult
from app.tools.context import ToolContext
from app.tools.registry import call_tool

log = logging.getLogger(__name__)

MIN_INTERVAL_S = 60.0
VERIFICATION_DROP_EPS = 0.05
MAX_ITERATIONS = 5
FLYWHEEL_PATH = Path("data/flywheel/critic.jsonl")

SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "critic.md").read_text(encoding="utf-8")


def _task_prompt(trigger: str) -> str:
    return (
        f"Trigger: {trigger}. Review the current map, weights, and verification; "
        "act only if a change is justified by what you find."
    )


def _flywheel_line(ctx: ToolContext, trigger: str, start_t: float, summary: str | None) -> dict[str, Any]:
    # db.list_changes'/list_backtests' `since` is a strict `t > since`; on a clock that
    # hasn't advanced (e.g. a paused SimClock in tests) a change written during this run
    # can land at exactly `start_t`, so look from just before it.
    since = start_t - 1e-6
    backtests = [bt for bt in ctx.db.list_backtests(ctx.app_id) if bt.t.timestamp() >= since]
    accepted = any(
        c.actor == "agent" and c.kind in ("weight", "trust") for c in ctx.db.list_changes(ctx.app_id, since=since)
    )
    best_train = max((v for bt in backtests for v in bt.train_scores.values()), default=None)
    best_holdout = max((v for bt in backtests for v in bt.holdout_scores.values()), default=None)
    return {
        "t": ctx.now(),
        "app_id": ctx.app_id,
        "trigger": trigger,
        "summary": summary or "",
        "search_spec": None,  # populated once the batch-backtest tool is registered
        "best_train_delta": best_train,
        "best_holdout_delta": best_holdout,
        "accepted": accepted,
    }


def _append_flywheel(line: dict[str, Any]) -> None:
    # Read the module global at call time (not as a default-parameter value bound at
    # import time), so tests can monkeypatch FLYWHEEL_PATH and actually have it take effect.
    FLYWHEEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with FLYWHEEL_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, default=str) + "\n")


async def run_critic(ctx: ToolContext, trigger: str, llm: LlmRouter | None = None) -> str | None:
    """One Critic episode for `ctx.app_id`. Returns the Critic's closing sentence, or
    `None` if the run was skipped (empty rate budget) or produced no final answer."""
    start_t = ctx.now()
    result = await run_agent(
        ctx,
        agent="critic",
        model_field="critic",
        system=SYSTEM_PROMPT,
        task=_task_prompt(trigger),
        llm=llm,
        max_iterations=MAX_ITERATIONS,
        enable_thinking=True,
    )
    summary = result if isinstance(result, str) else None
    _append_flywheel(_flywheel_line(ctx, trigger, start_t, summary))
    return summary


@dataclass
class _AppTriggerState:
    last_run_at: float
    seen_hotspots: set[str] = field(default_factory=set)
    had_override: bool = False
    last_verification_value: float | None = None


def hotspot_trigger(state: _AppTriggerState, result: TickResult) -> str | None:
    ids = {h.id for h in result.hotspots}
    new_ids = ids - state.seen_hotspots
    state.seen_hotspots = ids
    return f"a new hotspot appeared ({len(new_ids)})" if new_ids else None


def override_trigger(state: _AppTriggerState, result: TickResult) -> str | None:
    active = bool(result.override.any())
    became_active = active and not state.had_override
    state.had_override = active
    return "an authority override became active" if became_active else None


def verification_drop_trigger(ctx: ToolContext, state: _AppTriggerState) -> str | None:
    res = call_tool(ctx, "get_verification_report", {})
    if not res.get("ok"):
        return None
    deciding = next((m for m in res["result"]["methods"].values() if m["has_data"]), None)
    if deciding is None or deciding["value"] is None:
        return None
    value = float(deciding["value"])
    prev, state.last_verification_value = state.last_verification_value, value
    if prev is not None and value < prev - VERIFICATION_DROP_EPS:
        return f"verification dropped ({prev:.3f} -> {value:.3f})"
    return None


class CriticScheduler:
    """Wakes the Critic for each active app on a trigger, or at least every
    `MIN_INTERVAL_S`. One instance watches every app in `engine`."""

    def __init__(self, db: Db, engine: TickEngine, llm: LlmRouter | None = None, poll_s: float = 5.0) -> None:
        self.db = db
        self.engine = engine
        self.llm = llm or LlmRouter()
        self.poll_s = poll_s
        self._state: dict[str, _AppTriggerState] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def _state_for(self, app_id: str, now: float) -> _AppTriggerState:
        if app_id not in self._state:
            self._state[app_id] = _AppTriggerState(last_run_at=now)
        return self._state[app_id]

    async def _check_app(self, app_id: str, result: TickResult, now: float) -> None:
        st = self._state_for(app_id, now)
        trigger = hotspot_trigger(st, result) or override_trigger(st, result)
        if trigger is None:
            ctx = ToolContext(app_id=app_id, db=self.db, engine=self.engine, agent="critic", clock=self.engine.clock)
            trigger = verification_drop_trigger(ctx, st)
        if trigger is None and now - st.last_run_at >= MIN_INTERVAL_S:
            trigger = "scheduled check"
        if trigger is None:
            return
        st.last_run_at = now
        ctx = ToolContext(app_id=app_id, db=self.db, engine=self.engine, agent="critic", clock=self.engine.clock)
        await run_critic(ctx, trigger, self.llm)

    async def check_once(self) -> None:
        now = self.engine.clock.now()
        for app_id, rt in list(self.engine.apps.items()):
            if rt.last is None:
                continue
            try:
                await self._check_app(app_id, rt.last, now)
            except Exception:  # noqa: BLE001 - one app's Critic run must not stop the others
                log.exception("critic check failed for app %s", app_id)

    async def run(self) -> None:
        self._stop.clear()
        while not self._stop.is_set():
            await self.check_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_s)
            except TimeoutError:
                pass

    def start(self) -> asyncio.Task[None]:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self.run())
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None
