"""Verification methods: how well a weight vector's score map explains what happened.

All three work off `layer_cache` windows (normalized stacks [T, L, H, W] + override masks
[T, H, W]), so backtests can replay any candidate weights without re-running operators.
Events are passed as cells (`CellEvents`), so nothing here depends on the grid's projection.

- authority_confirmation: for each authority arrival, the share of the `lookback_s` before it
  during which the score peak was already within `radius_m` of the authority point (earlier and
  closer -> higher; overrides are left out, they ARE the authority). The report also gives, per
  layer, how early and how close that layer's peak was.
- predictive_check (leave-one-input-out): for each input with new events in (t, t + horizon],
  rebuild the score at t WITHOUT that input's layers and count the share of its new events
  that land in the top `top_frac` of cells. Average across inputs. An input can't predict itself.
- cross_input_agreement: Spearman rank correlation between per-input layers of DIFFERENT inputs
  (over cells where either is above `agreement_eps`), averaged with pair weights wᵢ·wⱼ. Has data only when
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
    radius_m: float = 50.0
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
    shares = {inp: h / n for inp, (h, n) in per_input.items()}
    return MethodScore(float(np.mean(list(shares.values()))), True, {"per_input": shares})


# -- authority_confirmation -----------------------------------------------------


def authority_confirmation(window: CacheWindow, weights: np.ndarray, ctx: VerificationContext) -> MethodScore:
    p = ctx.params
    arrivals = [ev for inp, ev in ctx.events.items() if inp in ctx.authority_inputs]
    if not arrivals or not len(window.t):
        return MethodScore(None, False, {"reason": "no authority input"})
    t = np.concatenate([a.t for a in arrivals])
    row = np.concatenate([a.row for a in arrivals])
    col = np.concatenate([a.col for a in arrivals])
    radius_cells = p.radius_m / ctx.cell_m

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
                if lead is None and d <= p.radius_m:
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


def cross_input_agreement(window: CacheWindow, weights: np.ndarray, ctx: VerificationContext) -> MethodScore:
    w = np.asarray(weights, dtype=float)
    inputs = sorted(set(ctx.layer_inputs))
    idx = {inp: [i for i, li in enumerate(ctx.layer_inputs) if li == inp] for inp in inputs}
    total, wsum, n_ticks = 0.0, 0.0, 0
    pairs: dict[str, list[float]] = {}
    for k in _strided(window.t, ctx.params.stride_s):
        # per-input layer: the input's layers summed with their (non-negative) weights, or plain sum if all 0
        per = {}
        for inp in inputs:
            lw = np.maximum(w[idx[inp]], 0.0)
            stack = window.layers[k][idx[inp]]
            per[inp] = (np.einsum("l,lhw->hw", lw, stack) if lw.sum() > 0 else stack.sum(axis=0), lw.sum())
        eps = ctx.params.agreement_eps
        live = [inp for inp in inputs if (per[inp][0] > eps).any()]
        if len(live) < 2:
            continue
        n_ticks += 1
        for a_i, a in enumerate(live):
            for b in live[a_i + 1 :]:
                la, lb = per[a][0], per[b][0]
                support = (la > eps) | (lb > eps)
                if support.sum() < 3:
                    continue
                rho = spearman(la[support], lb[support])
                if rho is None:
                    continue
                pw = max(per[a][1] * per[b][1], 1e-12)
                total += pw * rho
                wsum += pw
                pairs.setdefault(f"{a}~{b}", []).append(rho)
    if wsum == 0:
        return MethodScore(None, False, {"reason": "fewer than two inputs with non-zero layers"})
    return MethodScore(
        float(total / wsum),
        True,
        {"pairs": {k: float(np.mean(v)) for k, v in pairs.items()}, "ticks": n_ticks},
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
