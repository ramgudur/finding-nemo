"""Data retention (#213): prune old events and trace entries, outside the tick.

Events and trace rows accumulate forever otherwise, slowly bloating SQLite and slowing
``list_events`` / ``list_trace``. A background loop (never the tick, rule 2) prunes per app:

- **Events, push inputs of active apps only.** An input keeps every event that anything still
  reads: its longest operator horizon (what the tick reads), plus the backtest window and the
  read tools' window (``REPLAY_WINDOW_S``, which also reach back by that horizon), plus a margin;
  never less than ``event_floor_s`` (useful history for ``/raw`` and the console). An input with
  an operator that needs all history (an override with no ttl stays active until cleared, rule 6)
  is never pruned. Pull inputs are skipped: connectors dedupe documents by scanning the input's
  stored events, so pruning them would re-ingest (and re-locate) old feed items.
- **Trace:** the newest ``trace_keep`` entries per app, and never a pending Scout request (the
  Scout's queue lives in the trace) or anything after it.
- **Never pruned:** Changes (the rollback record, rule 11; pins and trust-rate windows are read
  from them), weights history (the stiff window) and backtests.

Event cutoffs use the platform Clock, like the tick: event times are Clock times.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field

from app.core.db import Db
from app.engine.tick import AppRuntime, TickEngine

log = logging.getLogger(__name__)

REPLAY_WINDOW_S = 600.0  # backtest window_s and the read tools' max window_s
MARGIN_S = 600.0
EVENT_FLOOR_S = 86_400.0
TRACE_KEEP = 20_000
INTERVAL_S = 300.0


@dataclass
class RetentionStats:
    events_deleted: dict[str, int] = field(default_factory=dict)  # "app/input" -> rows
    trace_deleted: dict[str, int] = field(default_factory=dict)  # app -> rows

    @property
    def total(self) -> int:
        return sum(self.events_deleted.values()) + sum(self.trace_deleted.values())


def event_keep_s(rt: AppRuntime, input_id: str, lookback_s: float, floor_s: float = EVENT_FLOOR_S) -> float:
    """How far back one input's events must be kept (``math.inf`` = keep everything)."""
    back = rt.input_lookback(input_id, lookback_s)
    if not math.isfinite(back):
        return math.inf
    return max(back + REPLAY_WINDOW_S + MARGIN_S, floor_s)


def _trace_keep_since(db: Db, app_id: str) -> float | None:
    """Time of the oldest pending Scout request: the trace from there on must stay."""
    from app.agents.scout import pending_requests  # agents import the engine, not the other way round

    pending = pending_requests(db, app_id)
    return min((e.t.timestamp() for e in pending), default=None)


def prune_once(
    db: Db,
    engine: TickEngine,
    *,
    event_floor_s: float = EVENT_FLOOR_S,
    trace_keep: int = TRACE_KEEP,
) -> RetentionStats:
    """One cleanup pass over every app the engine runs (events) and every stored app (trace)."""
    stats = RetentionStats()
    now = engine.clock.now()
    for app_id, rt in list(engine.apps.items()):
        for inp in rt.spec.inputs:
            if inp.kind != "push":
                continue
            keep = event_keep_s(rt, inp.id, engine.lookback_s, event_floor_s)
            if not math.isfinite(keep):
                continue
            n = db.prune_events(app_id, inp.id, now - keep)
            if n:
                stats.events_deleted[f"{app_id}/{inp.id}"] = n
    for spec in db.list_apps():
        n = db.prune_trace(spec.app_id, trace_keep, keep_since=_trace_keep_since(db, spec.app_id))
        if n:
            stats.trace_deleted[spec.app_id] = n
    return stats


class RetentionRunner:
    """Runs ``prune_once`` every ``interval_s`` seconds in a worker thread (never inside the tick).
    ``interval_s <= 0`` turns it off. A failed pass is logged and retried next time."""

    def __init__(
        self,
        db: Db,
        engine: TickEngine,
        *,
        interval_s: float = INTERVAL_S,
        event_floor_s: float = EVENT_FLOOR_S,
        trace_keep: int = TRACE_KEEP,
    ) -> None:
        self.db = db
        self.engine = engine
        self.interval_s = interval_s
        self.event_floor_s = event_floor_s
        self.trace_keep = trace_keep
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def run_once(self) -> RetentionStats:
        stats = prune_once(self.db, self.engine, event_floor_s=self.event_floor_s, trace_keep=self.trace_keep)
        if stats.total:
            log.info("retention: deleted events %s, trace %s", stats.events_deleted, stats.trace_deleted)
        return stats

    async def run(self) -> None:
        self._stop.clear()
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_s)
            except TimeoutError:
                try:
                    await asyncio.to_thread(self.run_once)
                except Exception:  # noqa: BLE001 - retention must never take the app down
                    log.exception("retention pass failed")

    def start(self) -> asyncio.Task[None] | None:
        if self.interval_s <= 0:
            return None
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self.run())
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None
