"""Backtests: would a proposed change have scored better on data it was not tuned on?

Single path (`run_backtest`, used for trust changes; also takes candidate weights):
1. Window: the last min(window_s, available history), split 70/30 by time into train and holdout.
   Fewer than `min_holdout_events` events in the holdout -> rejected, "insufficient holdout data".
2. Replay: re-run the operators through the real tick (`engine/tick.tick_app`) every
   `replay_step_s`, once with the current trust and once with the candidate trust, to get
   normalized stacks. Stacks don't depend on weights, so weights are applied at scoring time.
3. Score both (baseline, candidate) with `engine/verification` on each segment separately
   (segment ticks and segment events only, so train never peeks at holdout).
4. Accept when the highest-priority method with data improves on the holdout and no other method
   with data gets worse by more than ε (see `accept`).

The batch path (vectorized weight search) is a separate issue.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import numpy as np

from app.core.models import AppSpec, Backtest, VerificationMethod
from app.engine.layer_cache import CacheWindow
from app.engine.operators.base import Events
from app.engine.tick import AppRuntime, tick_app
from app.engine.verification import (
    METHODS,
    MethodScore,
    VerificationContext,
    VerificationParams,
    cell_events,
    evaluate,
)

MIN_HOLDOUT_EVENTS = 20


@dataclass(frozen=True)
class BacktestParams:
    window_s: float = 600.0
    train_frac: float = 0.7
    min_holdout_events: int = MIN_HOLDOUT_EVENTS
    epsilon: float = 0.02  # how much worse a non-deciding method may get on holdout
    replay_step_s: float = 10.0
    verification: VerificationParams = field(default_factory=VerificationParams)


@dataclass
class BacktestOutcome:
    backtest: Backtest
    reason: str  # one line for the trace
    baseline_train: dict[VerificationMethod, float] = field(default_factory=dict)
    baseline_holdout: dict[VerificationMethod, float] = field(default_factory=dict)
    deciding_method: VerificationMethod | None = None


class _FixedClock:
    def __init__(self, t: float) -> None:
        self.t = t

    def now(self) -> float:
        return self.t


class _ReplaySource:
    """EventSource over pre-fetched events (keeps declared fields)."""

    def __init__(self, events: Mapping[str, Events]) -> None:
        self._events = events

    def events(self, app_id: str, input_id: str, since: float, until: float) -> Events:
        ev = self._events.get(input_id)
        if ev is None:
            return Events(np.array([], dtype=object), np.array([]), np.array([]), np.array([]))
        return ev.select((ev.t > since) & (ev.t <= until))


def _replay(spec: AppSpec, source: _ReplaySource, times: np.ndarray, trust: Mapping[str, float] | None):
    rt = AppRuntime(spec, cache_ticks=max(1, len(times)))
    for t in times:
        tick_app(rt, source, now=float(t), trust=trust, clock=_FixedClock(float(t)))
    return rt, rt.cache.window()


def _segment(win: CacheWindow, t0: float, t1: float) -> CacheWindow:
    m = (win.t > t0) & (win.t <= t1)
    return CacheWindow(t=win.t[m], layers=win.layers[m], overrides=win.overrides[m], layer_ids=win.layer_ids)


def _values(res: Mapping[VerificationMethod, MethodScore]) -> dict[VerificationMethod, float]:
    return {m: r.value for m, r in res.items() if r.has_data and r.value is not None}


def accept(
    baseline: Mapping[VerificationMethod, float],
    candidate: Mapping[VerificationMethod, float],
    priority: Sequence[VerificationMethod],
    epsilon: float,
) -> tuple[bool, VerificationMethod | None, str]:
    """Holdout acceptance rule; `priority` is highest first.

    Walk methods by priority. A method the baseline has data for but the candidate lost is a
    rejection (the change destroyed evidence). A method only the candidate has data for can't
    be compared and is skipped. The first method with data on both sides decides and must
    improve; every other method with data on both sides may not get worse by more than ε.
    """
    for i, m in enumerate(priority):
        if m in baseline and m not in candidate:
            return False, m, f"{m} lost its holdout data under the candidate"
        if m in baseline and m in candidate:
            if candidate[m] <= baseline[m]:
                return False, m, f"{m} did not improve on holdout ({baseline[m]:.3f} -> {candidate[m]:.3f})"
            for other in priority[i + 1 :]:
                if other in baseline and other in candidate and candidate[other] < baseline[other] - epsilon:
                    worse = f"{baseline[other]:.3f} -> {candidate[other]:.3f}"
                    return False, m, f"{m} improved but {other} got worse on holdout by more than ε ({worse})"
            return True, m, f"{m} improved on holdout ({baseline[m]:.3f} -> {candidate[m]:.3f})"
    return False, None, "no verification method has holdout data"


def run_backtest(
    spec: AppSpec,
    events: Mapping[str, Events],
    *,
    now: float,
    weights: Mapping[str, float],
    trust: Mapping[str, float] | None = None,
    candidate_weights: Mapping[str, float] | None = None,
    candidate_trust: Mapping[str, float] | None = None,
    history_start: float | None = None,
    params: BacktestParams | None = None,
) -> BacktestOutcome:
    """`events`: per input id, covering at least the window. Weights are per layer_id; layers
    missing from `candidate_weights` keep their current weight."""
    p = params or BacktestParams()
    started = time.perf_counter()

    def outcome(passed: bool, reason: str, **kw) -> BacktestOutcome:
        bt = Backtest(
            id=uuid.uuid4().hex,
            kind="single",
            candidates_tested=1,
            candidates_valid=1,
            train_scores=kw.pop("train", {}),
            holdout_scores=kw.pop("holdout", {}),
            passed=passed,
            device="cpu",
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
            t=datetime.fromtimestamp(now, UTC),
        )
        return BacktestOutcome(backtest=bt, reason=reason, **kw)

    first_event = min((float(ev.t.min()) for ev in events.values() if len(ev)), default=now)
    start = max(now - p.window_s, history_start if history_start is not None else first_event)
    if start >= now:
        return outcome(False, "insufficient holdout data (no history)")
    split = start + p.train_frac * (now - start)
    n_holdout = sum(int(np.sum((ev.t > split) & (ev.t <= now))) for ev in events.values())
    if n_holdout < p.min_holdout_events:
        return outcome(False, f"insufficient holdout data ({n_holdout} < {p.min_holdout_events} events)")

    source = _ReplaySource(events)
    times = np.arange(start + p.replay_step_s, now + 1e-9, p.replay_step_s)
    rt, base_win = _replay(spec, source, times, trust)
    cand_win = base_win if candidate_trust is None else _replay(spec, source, times, candidate_trust)[1]

    ids = base_win.layer_ids
    w_base = np.array([weights.get(i, rt.weights[i]) for i in ids], dtype=float)
    cand_w = {**weights, **(candidate_weights or {})}
    w_cand = np.array([cand_w.get(i, rt.weights[i]) for i in ids], dtype=float)

    layer_input = {li.layer_id: li.input_id for li in rt.layers}
    cells = {inp: cell_events(ev, rt.grid) for inp, ev in events.items()}
    ctx = VerificationContext(
        layer_inputs=tuple(layer_input[i] for i in ids),
        cell_m=rt.grid.cell_m,
        events=cells,
        authority_inputs=frozenset(i.id for i in spec.inputs if i.tier == "locked"),
        override_inputs=frozenset(li.input_id for li in rt.layers if li.is_override),
        params=p.verification,
    )
    priority = [v.method for v in sorted(spec.verification, key=lambda v: v.priority)] or list(METHODS)

    def score(win: CacheWindow, w: np.ndarray, t0: float, t1: float) -> dict[VerificationMethod, float]:
        seg_ctx = replace(ctx, events={k: v.between(t0, t1) for k, v in cells.items()})
        return _values(evaluate(_segment(win, t0, t1), w, seg_ctx, priority))

    base_train, base_hold = score(base_win, w_base, start, split), score(base_win, w_base, split, now)
    cand_train, cand_hold = score(cand_win, w_cand, start, split), score(cand_win, w_cand, split, now)
    passed, method, reason = accept(base_hold, cand_hold, priority, p.epsilon)
    return outcome(
        passed,
        reason,
        train=cand_train,
        holdout=cand_hold,
        baseline_train=base_train,
        baseline_holdout=base_hold,
        deciding_method=method,
    )
