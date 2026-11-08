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

Batch path (`run_backtest_batch`, used for weight changes): no operator re-runs. It samples
candidate weight vectors from a search spec, renormalizes flexible weights and discards invalid
ones (tier bounds, pins, locked) BEFORE scoring, scores the train segment of the live layer_cache
window vectorized in chunks of 256 (numpy, or cupy on a GPU box), ranks on TRAIN only, and checks
just the top 5 on the holdout with the same acceptance rule as the single path.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.models import AppSpec, Backtest, VerificationMethod
from app.engine import tiers
from app.engine.layer_cache import CacheWindow
from app.engine.operators import registry
from app.engine.operators.base import Events, GridLike
from app.engine.tick import AppRuntime, tick_app
from app.engine.verification import (
    METHODS,
    CellEvents,
    MethodScore,
    VerificationContext,
    VerificationParams,
    cell_events,
    evaluate,
    evaluate_batch,
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


def _context(
    spec: AppSpec,
    layer_ids: Sequence[str],
    events: Mapping[str, Events],
    grid: GridLike,
    vparams: VerificationParams,
) -> tuple[VerificationContext, dict[str, CellEvents]]:
    layer_input = {o.layer_id: i.id for i in spec.inputs for o in i.operators}
    override_inputs = frozenset(
        i.id
        for i in spec.inputs
        for o in i.operators
        if o.op in registry.OPERATORS and registry.get(o.op).role == "override"
    )
    cells = {inp: cell_events(ev, grid) for inp, ev in events.items()}
    ctx = VerificationContext(
        layer_inputs=tuple(layer_input[i] for i in layer_ids),
        cell_m=grid.cell_m,
        events=cells,
        authority_inputs=frozenset(i.id for i in spec.inputs if i.tier == "locked"),
        override_inputs=override_inputs,
        params=vparams,
    )
    return ctx, cells


def _priority(spec: AppSpec) -> list[VerificationMethod]:
    return [v.method for v in sorted(spec.verification, key=lambda v: v.priority)] or list(METHODS)


def _window_bounds(events: Mapping[str, Events], now: float, history_start: float | None, p: BacktestParams):
    """(start, split, n_holdout_events) or a rejection reason."""
    first_event = min((float(ev.t.min()) for ev in events.values() if len(ev)), default=now)
    start = max(now - p.window_s, history_start if history_start is not None else first_event)
    if start >= now:
        return None, "insufficient holdout data (no history)"
    split = start + p.train_frac * (now - start)
    n_holdout = sum(int(np.sum((ev.t > split) & (ev.t <= now))) for ev in events.values())
    if n_holdout < p.min_holdout_events:
        return None, f"insufficient holdout data ({n_holdout} < {p.min_holdout_events} events)"
    return (start, split), ""


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

    span, why = _window_bounds(events, now, history_start, p)
    if span is None:
        return outcome(False, why)
    start, split = span

    source = _ReplaySource(events)
    times = np.arange(start + p.replay_step_s, now + 1e-9, p.replay_step_s)
    rt, base_win = _replay(spec, source, times, trust)
    cand_win = base_win if candidate_trust is None else _replay(spec, source, times, candidate_trust)[1]

    ids = base_win.layer_ids
    w_base = np.array([weights.get(i, rt.weights[i]) for i in ids], dtype=float)
    cand_w = {**weights, **(candidate_weights or {})}
    w_cand = np.array([cand_w.get(i, rt.weights[i]) for i in ids], dtype=float)

    ctx, cells = _context(spec, ids, events, rt.grid, p.verification)
    priority = _priority(spec)

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


# -- batch path -------------------------------------------------------------------

MAX_SAMPLES = 2048
CHUNK = 256
TOP_K = 5


def array_backend() -> tuple[object, str]:
    """(xp, device): cupy when it imports AND sees a GPU, else numpy. Device is reported honestly."""
    try:
        import cupy  # type: ignore[import-not-found]

        if cupy.cuda.runtime.getDeviceCount() > 0:
            return cupy, "gpu"
    except Exception:  # noqa: BLE001 - no cupy, no CUDA, no device: all mean CPU
        pass
    return np, "cpu"


class SearchSpec(BaseModel):
    """`{"layers": {layer_id: [lo, hi]}, "samples": N}`. Unlisted layers keep their current weight."""

    model_config = ConfigDict(extra="forbid")

    layers: dict[str, tuple[float, float]]
    samples: int = Field(default=256, ge=1, le=MAX_SAMPLES)

    @model_validator(mode="after")
    def _ranges(self) -> SearchSpec:
        for lid, (lo, hi) in self.layers.items():
            if not (0 <= lo <= hi) or not np.isfinite(hi):
                raise ValueError(f"layer {lid!r}: need 0 <= lo <= hi, got [{lo}, {hi}]")
        return self


@dataclass
class BatchCandidate:
    weights: dict[str, float]
    train: dict[VerificationMethod, float]
    holdout: dict[VerificationMethod, float]
    passed: bool
    reason: str


@dataclass
class BatchOutcome:
    backtest: Backtest
    reason: str  # one line for the trace, e.g. "512 configs backtested in 840 ms on cpu; ..."
    candidates: list[BatchCandidate] = field(default_factory=list)  # passing first, best holdout first
    baseline_holdout: dict[VerificationMethod, float] = field(default_factory=dict)


def sample_candidates(search: SearchSpec, bounds: tiers.BatchBounds, seed: int) -> np.ndarray:
    """[K, L] in `bounds.layer_ids` order: sampled within the search ranges, flexible weights renormalized."""
    unknown = set(search.layers) - set(bounds.layer_ids)
    if unknown:
        raise ValueError(f"search spec names unknown layers: {sorted(unknown)}")
    rng = np.random.default_rng(seed)
    w = np.tile(bounds.current, (search.samples, 1))
    for j, lid in enumerate(bounds.layer_ids):
        if lid in search.layers:
            lo, hi = search.layers[lid]
            w[:, j] = rng.uniform(lo, hi, search.samples)
    return tiers.renormalize_batch(w, bounds)


def rank_on_train(train: Mapping[VerificationMethod, np.ndarray], priority: Sequence[VerificationMethod]) -> np.ndarray:
    """Row order, best first: by the highest-priority method with train data, ties by the next ones."""
    keys = [np.nan_to_num(train[m], nan=-np.inf) for m in priority if m in train and np.isfinite(train[m]).any()]
    n = len(next(iter(train.values()))) if train else 0
    if not keys:
        return np.arange(n)
    return np.lexsort([-k for k in reversed(keys)])


def run_backtest_batch(
    spec: AppSpec,
    window: CacheWindow,
    events: Mapping[str, Events],
    search_spec: Mapping | SearchSpec,
    *,
    now: float,
    bounds: tiers.BatchBounds,
    grid: GridLike,
    history_start: float | None = None,
    params: BacktestParams | None = None,
    seed: int = 0,
    xp: object | None = None,
) -> BatchOutcome:
    """`window`: the live layer_cache window (weighted layers). `bounds`: from `tiers.bounds(...)`
    for the same layers (any order). `xp`: force numpy/cupy; default picks via `array_backend()`."""
    p = params or BacktestParams()
    started = time.perf_counter()
    search = search_spec if isinstance(search_spec, SearchSpec) else SearchSpec.model_validate(search_spec)
    if xp is None:
        xp, device = array_backend()
    else:
        device = "gpu" if getattr(xp, "__name__", "") == "cupy" else "cpu"
    if set(bounds.layer_ids) != set(window.layer_ids):
        raise ValueError("bounds and window must cover the same weighted layers")
    to_window = [bounds.layer_ids.index(lid) for lid in window.layer_ids]  # bounds order -> window order

    w_all = sample_candidates(search, bounds, seed)
    valid = tiers.valid_mask(w_all, bounds)  # discard invalid BEFORE scoring
    w_valid = w_all[valid][:, to_window]
    current = bounds.current[to_window]

    def finish(passed: bool, reason: str, best: BatchCandidate | None = None, **kw) -> BatchOutcome:
        elapsed = (time.perf_counter() - started) * 1000.0
        bt = Backtest(
            id=uuid.uuid4().hex,
            kind="batch",
            candidates_tested=len(w_all),
            candidates_valid=int(valid.sum()),
            train_scores=best.train if best else {},
            holdout_scores=best.holdout if best else {},
            passed=passed,
            device=device,
            elapsed_ms=elapsed,
            t=datetime.fromtimestamp(now, UTC),
        )
        head = f"{len(w_all)} configs backtested in {elapsed:.0f} ms on {device} ({int(valid.sum())} valid)"
        return BatchOutcome(backtest=bt, reason=f"{head}; {reason}", **kw)

    hist = history_start if history_start is not None else (float(window.t[0]) if len(window.t) else None)
    span, why = _window_bounds(events, now, hist, p)
    if span is None:
        return finish(False, why)
    start, split = span
    if not len(w_valid):
        return finish(False, "no valid candidates within tier bounds")

    ctx, cells = _context(spec, window.layer_ids, events, grid, p.verification)
    priority = _priority(spec)

    def seg(t0: float, t1: float) -> tuple[CacheWindow, VerificationContext]:
        return _segment(window, t0, t1), replace(ctx, events={k: v.between(t0, t1) for k, v in cells.items()})

    # train: vectorized over all valid candidates, in chunks
    train_win, train_ctx = seg(start, split)
    chunks = [
        evaluate_batch(train_win, w_valid[i : i + CHUNK], train_ctx, priority, xp)
        for i in range(0, len(w_valid), CHUNK)
    ]
    train = {m: np.concatenate([c[m] for c in chunks]) for m in priority}
    top = rank_on_train(train, priority)[:TOP_K]

    # holdout: only the top candidates by train, each against the current weights
    hold_win, hold_ctx = seg(split, now)
    base_hold = _values(evaluate(hold_win, current, hold_ctx, priority))
    cands = []
    for i in top:
        cand_hold = _values(evaluate(hold_win, w_valid[i], hold_ctx, priority))
        ok, _, why = accept(base_hold, cand_hold, priority, p.epsilon)
        cands.append(
            BatchCandidate(
                weights={lid: float(w_valid[i, j]) for j, lid in enumerate(window.layer_ids)},
                train={m: float(train[m][i]) for m in priority if np.isfinite(train[m][i])},
                holdout=cand_hold,
                passed=ok,
                reason=why,
            )
        )
    passing = [c for c in cands if c.passed]
    decide = next((m for m in priority if m in base_hold), None)
    if decide:
        passing.sort(key=lambda c: -c.holdout.get(decide, -np.inf))
    ordered = passing + [c for c in cands if not c.passed]
    best = ordered[0] if ordered else None
    reason = f"{len(passing)} of top {len(cands)} by train passed on holdout"
    if best:
        reason += f": {best.reason}"
    return finish(bool(passing), reason, best, candidates=ordered, baseline_holdout=base_hold)
