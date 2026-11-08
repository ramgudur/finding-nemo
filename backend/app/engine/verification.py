"""Verification methods: how well a weight vector's score map explains what happened.

All three work off `layer_cache` windows (normalized stacks [T, L, H, W] + override masks
[T, H, W]), so backtests can replay any candidate weights without re-running operators.
Events are passed as cells (`CellEvents`), so nothing here depends on the grid's projection.

- authority_confirmation: for each authority arrival, the share of the `lookback_s` before it
  during which the score peak was already within `radius_cells` of the authority point (earlier and
  closer -> higher; overrides are left out, they ARE the authority). The report also gives, per
  layer, how early and how close that layer's peak was.
- predictive_check (leave-one-input-out): for each input with new events in (t, t + horizon],
  rebuild the score at t WITHOUT that input's layers and count the share of its new events
  that land in the top `top_frac` of cells (its hit rate). An input can't predict itself. The
  value is the hit rates averaged with each input's share of the weights (#211), so moving weight
  toward inputs the others corroborate scores higher even with two inputs, where each hit rate
  alone can't depend on the weights. Equal input weights give the plain mean.
- cross_input_agreement: Spearman rank correlation between per-input layers (each input's
  unweighted layer sum) of DIFFERENT inputs, over cells where either is above `agreement_eps`,
  averaged with pair weights Wᵢ·Wⱼ (Wᵢ = sum of input i's layer weights). Has data only when
  at least two inputs have non-zero layers.

Every method returns a `MethodScore` with `has_data`; cold start needs >= 2 methods with data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from app.core.models import VerificationMethod
from app.engine.layer_cache import CacheWindow
from app.engine.operators.base import Events, GridLike, cell_index

METHODS: tuple[VerificationMethod, ...] = ("authority_confirmation", "predictive_check", "cross_input_agreement")


@dataclass(frozen=True)
class CellEvents:
    """Events as grid cells. Out-of-grid events are dropped by `cell_events`."""

    t: np.ndarray
    row: np.ndarray
    col: np.ndarray

    def __len__(self) -> int:
        return len(self.t)

    def between(self, t0: float, t1: float) -> CellEvents:
        """Events with t0 < t <= t1."""
        m = (self.t > t0) & (self.t <= t1)
        return CellEvents(self.t[m], self.row[m], self.col[m])


def cell_events(events: Events, grid: GridLike) -> CellEvents:
    x, y = grid.to_xy(events.lat, events.lon)
    row, col, inside = cell_index(grid, x, y)
    return CellEvents(events.t[inside], row[inside], col[inside])


@dataclass(frozen=True)
class VerificationParams:
    horizon_s: float = 60.0
    top_frac: float = 0.10
    stride_s: float = 10.0  # evaluate predictive_check / agreement every stride_s, not every tick
    lookback_s: float = 300.0
    radius_cells: float = 2.0  # authority match radius, in cells (fixed distances scale with cell_m)
    agreement_eps: float = 1e-3  # normalized values below this are smoothing tails, not signal


@dataclass(frozen=True)
class VerificationContext:
    layer_inputs: tuple[str, ...]  # input id of each layer, aligned with window.layer_ids
    cell_m: float
    events: Mapping[str, CellEvents]  # per input id (all inputs with events, incl. authority)
    authority_inputs: frozenset[str] = frozenset()  # inputs whose arrivals confirm (locked tier)
    override_inputs: frozenset[str] = frozenset()  # inputs feeding the override mask
    params: VerificationParams = field(default_factory=VerificationParams)


@dataclass
class MethodScore:
    value: float | None  # higher is better; None without data
    has_data: bool
    detail: dict[str, Any] = field(default_factory=dict)


def score_maps(layers: np.ndarray, weights: np.ndarray, overrides: np.ndarray | None) -> np.ndarray:
    """[T, L, H, W] x [L] -> [T, H, W] score maps: clamp(Σ w·norm, 0, 1), then overrides."""
    s = np.clip(np.einsum("l,tlhw->thw", weights, layers), 0.0, 1.0)
    return s if overrides is None else np.maximum(s, overrides)


def _strided(t: np.ndarray, stride_s: float) -> np.ndarray:
    """Indices of ticks at least stride_s apart, starting from the oldest."""
    out, last = [], -np.inf
    for i, ti in enumerate(t):
        if ti - last >= stride_s:
            out.append(i)
            last = ti
    return np.array(out, dtype=int)


def _top_threshold(score: np.ndarray, top_frac: float) -> float:
    return float(np.quantile(score, 1.0 - top_frac))


def _peak_dist_cells(grid_values: np.ndarray, r: int, c: int) -> float | None:
    """Distance (cells) from (r, c) to the nearest peak cell. A clamped score can have a plateau
    of equal maxima, so every cell at the max counts as the peak, not just the first one."""
    top = grid_values.max()
    if top <= 0:
        return None
    pr, pc = np.nonzero(grid_values >= top - 1e-12)
    return float(np.min(np.hypot(pr - r, pc - c)))


# -- predictive_check -----------------------------------------------------------


def predictive_check(window: CacheWindow, weights: np.ndarray, ctx: VerificationContext) -> MethodScore:
    p = ctx.params
    w = np.asarray(weights, dtype=float)
    layer_inputs = np.array(ctx.layer_inputs, dtype=object)
    per_input: dict[str, list[int]] = {}  # input -> [hits, total]
    for k in _strided(window.t, p.stride_s):
        t = window.t[k]
        for inp, ev in ctx.events.items():
            new = ev.between(t, t + p.horizon_s)
            if not len(new):
                continue
            keep = layer_inputs != inp
            score = np.clip(np.einsum("l,lhw->hw", w[keep], window.layers[k][keep]), 0.0, 1.0)
            if inp not in ctx.override_inputs:  # the override mask can't be split per input
                score = np.maximum(score, window.overrides[k])
            thr = _top_threshold(score, p.top_frac)
            vals = score[new.row, new.col]
            hits = int(np.sum((vals >= thr) & (vals > 0)))
            acc = per_input.setdefault(inp, [0, 0])
            acc[0] += hits
            acc[1] += len(new)
    if not per_input:
        return MethodScore(None, False, {"reason": "no input has new events after a cached tick"})
    rates = {inp: h / n for inp, (h, n) in per_input.items()}
    inputs = list(rates)
    share = _input_shares(w, ctx.layer_inputs, inputs)
    value = float(np.dot(share, [rates[i] for i in inputs]))
    return MethodScore(
        value, True, {"per_input": rates, "weight_share": dict(zip(inputs, share.tolist(), strict=True))}
    )


def _input_shares(w: np.ndarray, layer_inputs: Sequence[str], inputs: Sequence[str]) -> np.ndarray:
    """Each input's share of the weights, over `inputs`; [len(inputs)] for w [L], [K, len] for [K, L].
    An input without weighted layers (e.g. an authority override input) gets the mean weight of the
    inputs that have some, so it counts like a typical input. All-zero weights give equal shares."""
    w = np.asarray(w, dtype=float)
    per = np.stack([_input_weight(w, layer_inputs, i) for i in inputs], axis=-1)  # [..., n]
    has = np.array([any(li == i for li in layer_inputs) for i in inputs])
    if has.any() and not has.all():
        fill = per[..., has].mean(axis=-1, keepdims=True)
        per = np.where(has, per, fill)
    total = per.sum(axis=-1, keepdims=True)
    equal = np.full_like(per, 1.0 / len(inputs))
    return np.where(total > 0, per / np.where(total > 0, total, 1.0), equal)


# -- authority_confirmation -----------------------------------------------------


def authority_confirmation(window: CacheWindow, weights: np.ndarray, ctx: VerificationContext) -> MethodScore:
    p = ctx.params
    arrivals = [ev for inp, ev in ctx.events.items() if inp in ctx.authority_inputs]
    if not arrivals or not len(window.t):
        return MethodScore(None, False, {"reason": "no authority input"})
    t = np.concatenate([a.t for a in arrivals])
    row = np.concatenate([a.row for a in arrivals])
    col = np.concatenate([a.col for a in arrivals])
    radius_cells = p.radius_cells

    scores = score_maps(window.layers, np.asarray(weights, dtype=float), None)  # no overrides: they ARE the authority
    per_event, layers_report = [], []
    for ta, r, c in zip(t, row, col, strict=True):
        before = np.nonzero((window.t < ta) & (window.t >= ta - p.lookback_s))[0]
        if not len(before):
            continue
        # share of the lookback during which the score peak already sat within radius_m of the arrival
        near = 0
        for k in before:
            d = _peak_dist_cells(scores[k], r, c)
            near += d is not None and d <= radius_cells
        expected = max(1.0, p.lookback_s / _tick_s(window.t))
        per_event.append(min(1.0, near / expected))

        report = {}
        for li, lid in enumerate(window.layer_ids):
            lead, dist = None, None
            for k in before:
                dc = _peak_dist_cells(window.layers[k, li], r, c)
                if dc is None:
                    continue
                d = dc * ctx.cell_m
                if lead is None and dc <= p.radius_cells:
                    lead = float(ta - window.t[k])
                dist = d  # ends as the distance at the last tick before the arrival
            report[lid] = {"lead_s": lead, "distance_m": dist}
        layers_report.append({"t": float(ta), "layers": report})
    if not per_event:
        return MethodScore(None, False, {"reason": "no cached ticks before any authority arrival"})
    return MethodScore(float(np.mean(per_event)), True, {"arrivals": layers_report})


def _tick_s(t: np.ndarray) -> float:
    return float(np.median(np.diff(t))) if len(t) > 1 else 1.0


# -- cross_input_agreement ------------------------------------------------------


def _rank(a: np.ndarray) -> np.ndarray:
    """Average ranks with ties (like scipy.stats.rankdata)."""
    _, inv, counts = np.unique(a, return_inverse=True, return_counts=True)
    ends = np.cumsum(counts)
    avg = ends - (counts - 1) / 2.0
    return avg[inv]


def spearman(a: np.ndarray, b: np.ndarray) -> float | None:
    ra, rb = _rank(a), _rank(b)
    if ra.std() == 0 or rb.std() == 0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def _agreement_pairs(window: CacheWindow, ctx: VerificationContext) -> tuple[list[tuple[str, str, float]], int]:
    """Spearman rho for every pair of live inputs at every strided tick, from each input's
    unweighted layer sum (ranks don't depend on weights; weights enter only as pair weights)."""
    inputs = sorted(set(ctx.layer_inputs))
    idx = {inp: [i for i, li in enumerate(ctx.layer_inputs) if li == inp] for inp in inputs}
    eps = ctx.params.agreement_eps
    out: list[tuple[str, str, float]] = []
    n_ticks = 0
    for k in _strided(window.t, ctx.params.stride_s):
        per = {inp: window.layers[k][idx[inp]].sum(axis=0) for inp in inputs}
        live = [inp for inp in inputs if (per[inp] > eps).any()]
        if len(live) < 2:
            continue
        n_ticks += 1
        for a_i, a in enumerate(live):
            for b in live[a_i + 1 :]:
                support = (per[a] > eps) | (per[b] > eps)
                if support.sum() < 3:
                    continue
                rho = spearman(per[a][support], per[b][support])
                if rho is not None:
                    out.append((a, b, rho))
    return out, n_ticks


def _input_weight(w: np.ndarray, layer_inputs: Sequence[str], inp: str) -> np.ndarray:
    """Sum of an input's (non-negative) layer weights; works on [L] or [K, L]."""
    cols = [i for i, li in enumerate(layer_inputs) if li == inp]
    return np.maximum(w[..., cols], 0.0).sum(axis=-1)


def cross_input_agreement(window: CacheWindow, weights: np.ndarray, ctx: VerificationContext) -> MethodScore:
    w = np.asarray(weights, dtype=float)
    pairs, n_ticks = _agreement_pairs(window, ctx)
    if not pairs:
        return MethodScore(None, False, {"reason": "fewer than two inputs with non-zero layers"})
    total = wsum = 0.0
    per_pair: dict[str, list[float]] = {}
    for a, b, rho in pairs:
        pw = max(float(_input_weight(w, ctx.layer_inputs, a) * _input_weight(w, ctx.layer_inputs, b)), 1e-12)
        total += pw * rho
        wsum += pw
        per_pair.setdefault(f"{a}~{b}", []).append(rho)
    return MethodScore(
        float(total / wsum),
        True,
        {"pairs": {k: float(np.mean(v)) for k, v in per_pair.items()}, "ticks": n_ticks},
    )


# -- all methods ----------------------------------------------------------------

_FNS = {
    "authority_confirmation": authority_confirmation,
    "predictive_check": predictive_check,
    "cross_input_agreement": cross_input_agreement,
}


def evaluate(
    window: CacheWindow,
    weights: Sequence[float] | np.ndarray,
    ctx: VerificationContext,
    methods: Sequence[VerificationMethod] = METHODS,
) -> dict[VerificationMethod, MethodScore]:
    if len(ctx.layer_inputs) != len(window.layer_ids):
        raise ValueError("layer_inputs must align with window.layer_ids")
    w = np.asarray(weights, dtype=float)
    if w.shape != (len(window.layer_ids),):
        raise ValueError(f"expected {len(window.layer_ids)} weights, got shape {w.shape}")
    return {m: _FNS[m](window, w, ctx) for m in methods}


def methods_with_data(results: Mapping[VerificationMethod, MethodScore]) -> list[VerificationMethod]:
    """Methods that had data. Cold start rejects flexible weight changes while this has < 2 entries."""
    return [m for m, r in results.items() if r.has_data]


# -- batch (vectorized over K weight vectors) -------------------------------------------


def _predictive_batch(window: CacheWindow, w: Any, ctx: VerificationContext, xp: Any) -> np.ndarray:
    p = ctx.params
    k_count = w.shape[0]
    layer_inputs = np.array(ctx.layer_inputs, dtype=object)
    hits: dict[str, Any] = {}
    totals: dict[str, int] = {}
    for k in _strided(window.t, p.stride_s):
        t = window.t[k]
        for inp, ev in ctx.events.items():
            new = ev.between(t, t + p.horizon_s)
            if not len(new):
                continue
            keep = np.nonzero(layer_inputs != inp)[0]
            score = xp.clip(xp.einsum("kl,lhw->khw", w[:, keep], xp.asarray(window.layers[k][keep])), 0.0, 1.0)
            if inp not in ctx.override_inputs:
                score = xp.maximum(score, xp.asarray(window.overrides[k])[None])
            thr = xp.quantile(score.reshape(k_count, -1), 1.0 - p.top_frac, axis=1)
            vals = score[:, xp.asarray(new.row), xp.asarray(new.col)]
            h = ((vals >= thr[:, None]) & (vals > 0)).sum(axis=1)
            hits[inp] = hits.get(inp, 0) + h
            totals[inp] = totals.get(inp, 0) + len(new)
    if not totals:
        return np.full(k_count, np.nan)
    inputs = list(totals)
    rates = np.stack([_to_numpy(hits[i], xp) / totals[i] for i in inputs], axis=-1)  # [K, n]
    share = _input_shares(_to_numpy(w, xp), ctx.layer_inputs, inputs)  # [K, n]
    return (rates * share).sum(axis=-1)


def _authority_batch(window: CacheWindow, w: Any, ctx: VerificationContext, xp: Any) -> np.ndarray:
    p = ctx.params
    k_count = w.shape[0]
    arrivals = [ev for inp, ev in ctx.events.items() if inp in ctx.authority_inputs]
    if not arrivals or not len(window.t):
        return np.full(k_count, np.nan)
    t = np.concatenate([a.t for a in arrivals])
    row = np.concatenate([a.row for a in arrivals])
    col = np.concatenate([a.col for a in arrivals])
    h, wd = window.layers.shape[2:]
    rr, cc = np.indices((h, wd))
    expected = max(1.0, p.lookback_s / _tick_s(window.t))
    per_event = []
    for ta, r, c in zip(t, row, col, strict=True):
        before = np.nonzero((window.t < ta) & (window.t >= ta - p.lookback_s))[0]
        if not len(before):
            continue
        within = xp.asarray(np.hypot(rr - r, cc - c) <= p.radius_cells)  # [H, W]
        near = xp.zeros(k_count)
        for k in before:
            s = xp.clip(xp.einsum("kl,lhw->khw", w, xp.asarray(window.layers[k])), 0.0, 1.0)
            top = s.reshape(k_count, -1).max(axis=1)
            peak = s >= (top - 1e-12)[:, None, None]
            near = near + ((peak & within[None]).reshape(k_count, -1).any(axis=1) & (top > 0))
        per_event.append(xp.minimum(1.0, near / expected))
    if not per_event:
        return np.full(k_count, np.nan)
    return _to_numpy(xp.stack(per_event).mean(axis=0), xp)


def _agreement_batch(window: CacheWindow, w: np.ndarray, ctx: VerificationContext) -> np.ndarray:
    pairs, _ = _agreement_pairs(window, ctx)  # weight-independent: computed once for all K
    if not pairs:
        return np.full(w.shape[0], np.nan)
    total = np.zeros(w.shape[0])
    wsum = np.zeros(w.shape[0])
    for a, b, rho in pairs:
        pw = np.maximum(_input_weight(w, ctx.layer_inputs, a) * _input_weight(w, ctx.layer_inputs, b), 1e-12)
        total += pw * rho
        wsum += pw
    return total / wsum


def _to_numpy(a: Any, xp: Any) -> np.ndarray:
    return np.asarray(a.get() if hasattr(a, "get") else a, dtype=float)


def evaluate_batch(
    window: CacheWindow,
    weights: np.ndarray,
    ctx: VerificationContext,
    methods: Sequence[VerificationMethod] = METHODS,
    xp: Any = np,
) -> dict[VerificationMethod, np.ndarray]:
    """Like `evaluate` for K weight vectors at once ([K, L]); `xp` is numpy or cupy.
    Returns per method a [K] numpy array, NaN where the method has no data.
    Matches `evaluate` row by row (tested), so batch train scores and single holdout scores agree."""
    w_np = np.atleast_2d(np.asarray(weights, dtype=float))
    if len(ctx.layer_inputs) != len(window.layer_ids) or w_np.shape[1] != len(window.layer_ids):
        raise ValueError("weights and layer_inputs must align with window.layer_ids")
    w = xp.asarray(w_np)
    out: dict[VerificationMethod, np.ndarray] = {}
    for m in methods:
        if m == "predictive_check":
            out[m] = _predictive_batch(window, w, ctx, xp)
        elif m == "authority_confirmation":
            out[m] = _authority_batch(window, w, ctx, xp)
        else:
            out[m] = _agreement_batch(window, w_np, ctx)
    return out
