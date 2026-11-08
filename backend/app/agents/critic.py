"""`agents/critic.py`: the Critic -- tunes layer weights and per-user trust under guards.

`run_critic` is one episode: build the task prompt from a trigger reason, run the
generic tool-calling loop (`agents.loop.run_agent`), then append a line to
`data/flywheel/critic.jsonl` regardless of outcome (a skipped or no-op run still
records that nothing happened).

`CriticScheduler` decides *when* to call it: on a new hotspot, a newly active authority
override, new events on a reference input (tier stiff or locked: a connector poll or an
ingest), a drop in the deciding verification method, or a dev's "run now" request -- each
throttled to at most once per `TRIGGER_COOLDOWN_S` per app, since a moving hotspot can change
id every poll -- or a plain scheduled check at least every `MIN_INTERVAL_S`. A reference trigger
or a run-now request that lands inside the cooldown is kept pending and runs once it ends.
It runs as its own asyncio task,
reading only cached/latest tick state, so a Critic run (async, LLM-bound) never blocks the
2 s tick (rule 2).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.agents import budget
from app.agents.llm import LlmRouter
from app.agents.loop import run_agent
from app.core.db import Db
from app.core.models import AppSpec, TraceEntry
from app.engine.tick import TickEngine, TickResult
from app.tools.context import ToolContext
from app.tools.registry import call_tool

log = logging.getLogger(__name__)


def _trace_skip(ctx: ToolContext, reason: str) -> None:
    """Rule 2: deciding not to even start a run is still a skip, and still gets a trace entry --
    not just a call that fails mid-run (loop.py's own rate-budget catch)."""
    ctx.db.add_trace(
        TraceEntry(
            t=ctx.now_dt(), app_id=ctx.app_id, agent="critic", action="skipped", skipped_reason=reason, why=reason
        )
    )


MIN_INTERVAL_S = 60.0
TRIGGER_COOLDOWN_S = 15.0
VERIFICATION_DROP_EPS = 0.05
MAX_ITERATIONS = 5
AGENT = "critic"  # model field and budget key
FLYWHEEL_PATH = Path("data/flywheel/critic.jsonl")

SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "critic.md").read_text(encoding="utf-8")


REFERENCE_TIERS = ("stiff", "locked")
REFERENCE_FRESH_S = 600.0  # first sight of a reference input: trigger only if its newest event is this recent
RUN_NOW_TRIGGER = "the dev asked for a run now"


def _task_prompt(trigger: str) -> str:
    hint = (
        " Reference data arrived: call get_corroboration first and act on what it shows."
        if trigger.startswith("new reference data")
        else ""
    )
    return (
        f"Trigger: {trigger}. Review the current map, weights, and verification; "
        f"act only if a change is justified by what you find.{hint}"
    )


def _backtest_deltas(
    name: str, args: dict[str, Any], result: dict[str, Any]
) -> tuple[dict[str, Any] | None, float | None, float | None]:
    """``(search_spec, best_train_delta, best_holdout_delta)`` from one backtest tool's own
    call -- candidate minus baseline on the deciding method, read from exactly what the
    tool returned (the trace's ``result_summary`` is truncated to 200 chars, too lossy for
    this). ``run_backtest_batch`` has no baseline train score to diff against, so its train
    delta stays `None`."""
    if not result.get("ok"):
        return None, None, None
    r = result["result"]
    if name == "run_backtest":
        dm = r.get("deciding_method")
        if not dm:
            return None, None, None
        baseline_train, baseline_holdout = r.get("baseline_train") or {}, r.get("baseline_holdout") or {}
        candidate_train, candidate_holdout = r.get("candidate_train") or {}, r.get("candidate_holdout") or {}
        train_delta = (
            candidate_train[dm] - baseline_train[dm] if dm in candidate_train and dm in baseline_train else None
        )
        holdout_delta = (
            candidate_holdout[dm] - baseline_holdout[dm] if dm in candidate_holdout and dm in baseline_holdout else None
        )
        return None, train_delta, holdout_delta
    if name == "run_backtest_batch":
        baseline_holdout, best_holdout = r.get("baseline_holdout") or {}, r.get("best_holdout") or {}
        common = set(baseline_holdout) & set(best_holdout)
        holdout_delta = max((best_holdout[m] - baseline_holdout[m] for m in common), default=None)
        search_spec = (
            {"layers": args.get("layers"), "samples": args.get("samples")} if holdout_delta is not None else None
        )
        return search_spec, None, holdout_delta
    return None, None, None


def _flywheel_line(
    ctx: ToolContext,
    trigger: str,
    start_t: float,
    summary: str | None,
    backtest_calls: list[tuple[str, dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    # db.list_changes' `since` is a strict `t > since`; on a clock that hasn't advanced
    # (e.g. a paused SimClock in tests) a change written during this run can land at
    # exactly `start_t`, so look from just before it.
    since = start_t - 1e-6
    accepted = any(
        c.actor == "agent" and c.kind in ("weight", "trust") for c in ctx.db.list_changes(ctx.app_id, since=since)
    )
    search_spec = train_delta = holdout_delta = None
    for name, args, result in backtest_calls:
        spec, train, holdout = _backtest_deltas(name, args, result)
        search_spec = search_spec or spec
        train_delta = train if train is not None else train_delta
        holdout_delta = holdout if holdout is not None else holdout_delta
    return {
        "t": ctx.now(),
        "app_id": ctx.app_id,
        "trigger": trigger,
        "summary": summary or "",
        "search_spec": search_spec,
        "best_train_delta": train_delta,
        "best_holdout_delta": holdout_delta,
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
    backtest_calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def _record(name: str, args: dict[str, Any], result: dict[str, Any]) -> None:
        if name in ("run_backtest", "run_backtest_batch"):
            backtest_calls.append((name, args, result))

    result = await run_agent(
        ctx,
        agent="critic",
        model_field="critic",
        system=SYSTEM_PROMPT,
        task=_task_prompt(trigger),
        llm=llm,
        max_iterations=MAX_ITERATIONS,
        enable_thinking=True,
        on_tool_result=_record,
    )
    summary = result if isinstance(result, str) else None
    _append_flywheel(_flywheel_line(ctx, trigger, start_t, summary, backtest_calls))
    return summary


@dataclass
class _AppTriggerState:
    last_run_at: float
    last_triggered_at: float = float("-inf")
    seen_hotspots: set[str] = field(default_factory=set)
    had_override: bool = False
    last_verification_value: float | None = None
    pending_trigger: str | None = None  # a trigger deferred (low rate budget, or inside the cooldown)
    reference_seen: dict[str, tuple[int, float | None]] = field(default_factory=dict)  # input -> (count, max ts)


def reference_trigger(db: Db, spec: AppSpec, state: _AppTriggerState, now: float) -> str | None:
    """New events on a reference input (tier stiff or locked), from any path (connector poll or
    ingest). One aggregate query per reference input. The first look at an input only counts if
    its newest event is recent, so a restart doesn't re-trigger on old data."""
    fresh: list[str] = []
    for inp in spec.inputs:
        if inp.tier not in REFERENCE_TIERS or inp.shape == "field":  # a field locates nothing
            continue
        total, _, last_ts = db.event_counts(spec.app_id, inp.id, since=now)
        prev = state.reference_seen.get(inp.id)
        state.reference_seen[inp.id] = (total, last_ts)
        if total == 0 or last_ts is None:
            continue
        if prev is None:
            if now - last_ts <= REFERENCE_FRESH_S:
                fresh.append(inp.id)
        elif total > prev[0] or (prev[1] is None or last_ts > prev[1]):
            fresh.append(inp.id)
    return f"new reference data on {', '.join(fresh)}" if fresh else None


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
        self._running: set[tuple[str, str]] = set()  # (app_id, agent) runs in flight
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()  # set by request_run so a queued run starts without a full poll wait

    def request_run(self, app_id: str, now: float) -> float | None:
        """Queue a triggered run for `app_id` (the dev's "run now"), through the same path as the
        other triggers: the same per-app cooldown and rate budget. Returns None when queued, else
        the seconds left in the cooldown. Call it from the scheduler's event loop."""
        st = self._state_for(app_id, now)
        left = TRIGGER_COOLDOWN_S - (now - st.last_triggered_at)
        if left > 0:
            return left
        st.pending_trigger = RUN_NOW_TRIGGER
        self._wake.set()
        return None

    def _state_for(self, app_id: str, now: float) -> _AppTriggerState:
        if app_id not in self._state:
            self._state[app_id] = _AppTriggerState(last_run_at=now)
        return self._state[app_id]

    async def _check_app(self, app_id: str, result: TickResult, now: float) -> None:
        st = self._state_for(app_id, now)
        ctx = ToolContext(app_id=app_id, db=self.db, engine=self.engine, agent="critic", clock=self.engine.clock)

        spec = self.engine.apps[app_id].spec if app_id in self.engine.apps else self.db.get_app(app_id)
        reference = reference_trigger(self.db, spec, st, now) if spec is not None else None
        triggered = hotspot_trigger(st, result) or override_trigger(st, result)
        if reference is not None:
            triggered = reference
            if now - st.last_triggered_at < TRIGGER_COOLDOWN_S:
                st.pending_trigger = reference  # not lost: it runs as soon as the cooldown ends
        if triggered is None:
            # numpy-heavy (verification.evaluate); keep it off the event loop.
            triggered = await asyncio.to_thread(verification_drop_trigger, ctx, st)

        reason, priority = None, None
        if st.pending_trigger is not None and now - st.last_triggered_at >= TRIGGER_COOLDOWN_S:
            reason, priority = st.pending_trigger, "triggered"  # a trigger deferred for budget
        elif triggered is not None and now - st.last_triggered_at >= TRIGGER_COOLDOWN_S:
            reason, priority = triggered, "triggered"
        elif now - st.last_run_at >= budget.scaled_interval(
            MIN_INTERVAL_S, len(self.engine.apps), AGENT, self.llm.rpm(AGENT)
        ):
            reason, priority = "scheduled check", "scheduled"
        if reason is None:
            return
        if (app_id, AGENT) in self._running:
            return  # never more than one in-flight run per app and agent
        if not self.llm.admit(AGENT, budget.EST_CALLS[AGENT], priority):
            # the run itself is deferred (kept pending for triggered, just waits for scheduled),
            # but the decision not to start it now is still a skip (rule 2)
            _trace_skip(ctx, "rate budget")
            if priority == "triggered":
                st.pending_trigger = reason
            return
        st.pending_trigger = None
        if priority == "triggered":
            st.last_triggered_at = now
        st.last_run_at = now
        self._running.add((app_id, AGENT))
        try:
            await run_critic(ctx, reason, self.llm)
        finally:
            self._running.discard((app_id, AGENT))

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
            self._wake.clear()
            await self.check_once()
            try:
                await asyncio.wait_for(self._wake_or_stop(), timeout=self.poll_s)
            except TimeoutError:
                pass

    async def _wake_or_stop(self) -> None:
        waits = [asyncio.ensure_future(self._stop.wait()), asyncio.ensure_future(self._wake.wait())]
        try:
            await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waits:
                w.cancel()

    def start(self) -> asyncio.Task[None]:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self.run())
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None
