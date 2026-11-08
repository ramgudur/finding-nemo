"""`agents/poi_labeler.py`: the POI Labeler -- names code-made candidates from the app's label set.

`run_poi_labeler` is one episode: build the task prompt (intent, label mode, label set and the
trigger), run the generic tool-calling loop with the POI tools (`tools/poi_tools.py`, guarded:
candidates only, positions copied from the candidate, label set only, sensitive stays authority).
In `auto` mode it may create nothing; a new label type is only *proposed* with `propose_label`
(a stored `LabelProposal` the dev accepts or dismisses in the console). `mode: off` never runs.

`PoiLabelerScheduler` decides *when*: as soon as an unlabeled candidate has persisted
`PERSIST_TRIGGER_TICKS` ticks (once per candidate), throttled to one triggered run per
`TRIGGER_COOLDOWN_S` per app, or a scheduled pass at least every `MIN_INTERVAL_S` while there are
candidates, or when the dev asks for a run now (`request_run`, same cooldown and budget). It
reads only the latest tick result, so a run never blocks the tick (rule 2).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.agents import budget
from app.agents.llm import LlmRouter
from app.agents.loop import run_agent
from app.core.db import Db
from app.core.models import AppSpec, TraceEntry
from app.engine.tick import TickEngine, TickResult
from app.tools.context import ToolContext

log = logging.getLogger(__name__)

MIN_INTERVAL_S = 60.0
TRIGGER_COOLDOWN_S = 15.0
RUN_NOW_TRIGGER = "the dev asked for a run now"
PERSIST_TRIGGER_TICKS = 3
MAX_ITERATIONS = 6
AGENT = "poi"  # model field and budget key

SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "poi_labeler.md").read_text(encoding="utf-8")


def task_prompt(spec: AppSpec, trigger: str) -> str:
    cfg = spec.outputs.poi_labels
    labels = (
        "\n".join(
            f"- {lab.label}" + (" (sensitive)" if lab.sensitive else "") + (" (destination)" if lab.destination else "")
            for lab in cfg.label_set
        )
        or "- (none: create nothing; you may propose label types with propose_label)"
    )
    mode = (
        "auto: label only if a candidate is clearly worth naming for this intent; otherwise create nothing and say why"
        if cfg.mode == "auto"
        else "on: label candidates the evidence supports"
    )
    return (
        f"App intent: {spec.intent}\n"
        f"Mode: {mode}\n"
        f"Minimum confidence: {cfg.min_confidence}\n"
        f"Label set:\n{labels}\n"
        f"Trigger: {trigger}"
    )


def _trace_skip(ctx: ToolContext, reason: str) -> None:
    ctx.db.add_trace(
        TraceEntry(
            t=ctx.now_dt(),
            app_id=ctx.app_id,
            agent="poi_labeler",
            action="skipped",
            skipped_reason=reason,
            why=reason,
        )
    )


async def run_poi_labeler(ctx: ToolContext, trigger: str, llm: LlmRouter | None = None) -> str | None:
    """One POI Labeler episode for `ctx.app_id`. Returns the closing sentence, or `None` if the run
    was skipped (mode off, rate budget) or produced no final answer."""
    spec = ctx.spec
    if spec.outputs.poi_labels.mode == "off":
        _trace_skip(ctx, "poi_labels.mode is off")
        return None
    result = await run_agent(
        ctx,
        agent="poi_labeler",
        model_field="poi",
        system=SYSTEM_PROMPT,
        task=task_prompt(spec, trigger),
        llm=llm,
        max_iterations=MAX_ITERATIONS,
        enable_thinking=False,  # Lightning, low effort
    )
    return result if isinstance(result, str) else None


@dataclass
class _AppState:
    last_run_at: float
    last_triggered_at: float = float("-inf")
    triggered_candidates: set[str] = field(default_factory=set)
    pending_trigger: str | None = None  # a trigger deferred because the rate budget was low


def persist_trigger(state: _AppState, result: TickResult, labeled: set[str]) -> str | None:
    """A trigger reason when an unlabeled candidate first reaches PERSIST_TRIGGER_TICKS."""
    ready = [
        c.id
        for c in result.candidates
        if c.persisted_ticks >= PERSIST_TRIGGER_TICKS and c.id not in labeled and c.id not in state.triggered_candidates
    ]
    if not ready:
        return None
    state.triggered_candidates.update(ready)
    return f"{len(ready)} candidate(s) persisted {PERSIST_TRIGGER_TICKS}+ ticks"


class PoiLabelerScheduler:
    """Wakes the POI Labeler for each active app on a persistence trigger, or at least every
    `MIN_INTERVAL_S` while the app has candidates. One instance watches every app in `engine`."""

    def __init__(self, db: Db, engine: TickEngine, llm: LlmRouter | None = None, poll_s: float = 5.0) -> None:
        self.db = db
        self.engine = engine
        self.llm = llm or LlmRouter()
        self.poll_s = poll_s
        self._state: dict[str, _AppState] = {}
        self._running: set[tuple[str, str]] = set()  # (app_id, agent) runs in flight
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()  # set by request_run so a queued run starts without a full poll wait

    def request_run(self, app_id: str, now: float) -> float | None:
        """Queue a triggered run for `app_id` (the dev's "run now"), through the same path as the
        other triggers: the same per-app cooldown and rate budget. Returns None when queued, else
        the seconds left in the cooldown. Call it from the scheduler's event loop."""
        st = self._state.setdefault(app_id, _AppState(last_run_at=now))
        left = TRIGGER_COOLDOWN_S - (now - st.last_triggered_at)
        if left > 0:
            return left
        st.pending_trigger = RUN_NOW_TRIGGER
        self._wake.set()
        return None

    def _labeled(self, app_id: str) -> set[str]:
        return {p.candidate_id for p in self.db.list_pois(app_id, status="active")}

    async def _check_app(self, app_id: str, rt_spec: AppSpec, result: TickResult, now: float) -> None:
        if rt_spec.outputs.poi_labels.mode == "off":
            return
        st = self._state.setdefault(app_id, _AppState(last_run_at=now))
        if not result.candidates and st.pending_trigger != RUN_NOW_TRIGGER:
            return  # nothing to label; only the dev's explicit request still runs (it then logs why)
        triggered = persist_trigger(st, result, self._labeled(app_id))
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
        ctx = ToolContext(app_id=app_id, db=self.db, engine=self.engine, agent="poi_labeler", clock=self.engine.clock)
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
            await run_poi_labeler(ctx, reason, self.llm)
        finally:
            self._running.discard((app_id, AGENT))

    async def check_once(self) -> None:
        now = self.engine.clock.now()
        for app_id, rt in list(self.engine.apps.items()):
            if rt.last is None:
                continue
            try:
                await self._check_app(app_id, rt.spec, rt.last, now)
            except Exception:  # noqa: BLE001 - one app's run must not stop the others
                log.exception("poi labeler check failed for app %s", app_id)

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
