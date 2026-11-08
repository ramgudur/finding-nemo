"""Tier bounds for layer weights (CLAUDE.md rule 5).

| Tier       | Weight changes allowed                                                     |
|------------|----------------------------------------------------------------------------|
| locked     | never                                                                      |
| stiff      | at most +/-10% per hour (vs. the weight one hour ago), and only with       |
|            | authority-confirmation evidence                                            |
| flexible   | at most +/-25% per step; flexible weights are renormalized so their sum    |
|            | stays at the value set at creation. Stiff/locked never move from it.       |

Also:
- Cold start: flexible changes are rejected until at least two verification
  methods have data.
- Stiffness comes from the tier set at creation, never from a weight's value.
- Pinned weights (by the dev) never move, whatever their tier.
- Agents can never change a tier.

This module is pure: no DB, no clock reads. Callers (``tools/guards.py``,
``engine/backtest.py``) pass in ``now`` from ``core.clock`` and the weight history.
The same rules come in two forms:
- ``check_update``: one proposed update, with human-readable reasons for the trace.
- ``bounds`` + ``renormalize_batch`` + ``valid_mask``: vectorized, for batch backtests.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast, get_args

import numpy as np

from app.core.models import Tier

TIERS: tuple[str, ...] = get_args(Tier)


STIFF_MAX_REL_PER_HOUR = 0.10
STIFF_WINDOW_S = 3600.0
FLEX_MAX_REL_PER_STEP = 0.25
# A flexible weight at (or near) 0 could otherwise never move. Its +/-25% step is
# measured against at least this value.
FLEX_MIN_REF = 0.02
MIN_METHODS_WITH_DATA = 2
# Float slack so a candidate exactly on a bound (after renormalization) still passes.
_EPS = 1e-9


@dataclass(frozen=True)
class WeightSlot:
    """One layer weight as the guards see it.

    ``tier`` and ``flexible_total``-relevant values are fixed at creation. ``history``
    is ``[(t, weight), ...]`` of applied values (any order), used for the stiff
    one-hour window; it may be empty. ``created_weight`` is the weight set at creation
    (the confirmed spec's value): the stiff window measures from it until an applied
    change is a full window old, since ``history`` only holds values after changes.
    """

    layer_id: str
    tier: Tier
    weight: float
    pinned: bool = False
    history: Sequence[tuple[float, float]] = field(default_factory=tuple)
    created_weight: float | None = None


@dataclass
class CheckResult:
    ok: bool
    weights: dict[str, float]  # final weights after renormalization (== current when rejected)
    reasons: list[str] = field(default_factory=list)


def as_tier(value: str) -> Tier:
    if value not in TIERS:
        raise ValueError(f"unknown tier {value!r}; expected one of {list(TIERS)}")
    return cast(Tier, value)


def check_tier_change(old: str, new: str, actor: str) -> tuple[bool, str]:
    """Agents never change tiers. A dev may, but only through spec confirmation."""
    if as_tier(old) == as_tier(new):
        return True, "tier unchanged"
    if actor != "dev":
        return False, f"tier change {old}->{new} rejected: only the dev can set tiers (at spec confirmation)"
    return True, "tier change by dev"


def flexible_total(slots: Iterable[WeightSlot]) -> float:
    """Sum of flexible weights. Call this at creation and store it; later calls just verify."""
    return float(sum(s.weight for s in slots if as_tier(s.tier) == "flexible"))


def stiff_reference(slot: WeightSlot, now: float, window_s: float = STIFF_WINDOW_S) -> float:
    """Weight as of ``now - window_s``: the latest history entry at or before that time.

    When history doesn't reach back a full window, the weight then was the one set at
    creation (``created_weight``), so the window measures from it: 0.50 -> 0.54 -> 0.58
    inside one hour is +16%, not two small steps. Without a ``created_weight`` it falls
    back to the earliest known value (or the current weight).
    """
    cutoff = now - window_s
    before = [(t, w) for t, w in slot.history if t <= cutoff]
    if before:
        return float(max(before, key=lambda p: p[0])[1])
    if slot.created_weight is not None:
        return float(slot.created_weight)
    if slot.history:
        return float(min(slot.history, key=lambda p: p[0])[1])
    return float(slot.weight)


def slot_bounds(
    slot: WeightSlot,
    *,
    now: float,
    authority_evidence: bool,
    methods_with_data: int,
    dev_revert: bool = False,
) -> tuple[float, float, str | None]:
    """``(lo, hi, reason_if_frozen)`` for one weight in the next step, before renormalization.

    A frozen weight returns ``lo == hi == current`` and a reason.

    ``dev_revert``: a dev undoing a recorded change (``tools/revert_tools.py``; guards only pass it
    for ``actor == "dev"``). It skips the evidence gates (cold start, stiff authority evidence) and
    the stiff one-hour window, which exist to stop agent drift. Hard bounds still hold: pins,
    locked, the flexible +/-25% step and (in ``check_update``) the flexible sum.
    """
    w = float(slot.weight)
    tier = as_tier(slot.tier)
    if slot.pinned:
        return w, w, f"{slot.layer_id}: pinned by dev"
    if tier == "locked":
        return w, w, f"{slot.layer_id}: locked tier never changes"
    if tier == "stiff":
        if dev_revert:
            return 0.0, float("inf"), None
        if not authority_evidence:
            return w, w, f"{slot.layer_id}: stiff tier needs authority-confirmation evidence"
        ref = stiff_reference(slot, now)
        lo = max(0.0, ref * (1 - STIFF_MAX_REL_PER_HOUR))
        hi = ref * (1 + STIFF_MAX_REL_PER_HOUR)
        return lo, hi, None
    # flexible
    if methods_with_data < MIN_METHODS_WITH_DATA and not dev_revert:
        return (
            w,
            w,
            (
                f"{slot.layer_id}: cold start - flexible weights stay fixed until at least "
                f"{MIN_METHODS_WITH_DATA} verification methods have data (have {methods_with_data})"
            ),
        )
    step = FLEX_MAX_REL_PER_STEP * max(w, FLEX_MIN_REF)
    return max(0.0, w - step), w + step, None


def renormalize(weights: Mapping[str, float], slots: Sequence[WeightSlot], total: float) -> dict[str, float]:
    """Scale the unpinned flexible weights so all flexible weights sum to ``total``.

    Stiff, locked and pinned weights are never touched. Raises ``ValueError`` if the
    target can't be reached (unpinned flexible sum is zero, or pinned already exceed it).
    """
    out = {k: float(v) for k, v in weights.items()}
    free = [s.layer_id for s in slots if as_tier(s.tier) == "flexible" and not s.pinned]
    pinned_sum = sum(out[s.layer_id] for s in slots if as_tier(s.tier) == "flexible" and s.pinned)
    if not free:
        return out
    target = total - pinned_sum
    current = sum(out[k] for k in free)
    if target < -_EPS:
        raise ValueError("pinned flexible weights exceed the flexible total")
    if current <= 0:
        if target <= _EPS:
            return out
        raise ValueError("cannot renormalize: unpinned flexible weights sum to zero")
    scale = max(target, 0.0) / current
    for k in free:
        out[k] *= scale
    return out


def check_update(
    slots: Sequence[WeightSlot],
    proposed: Mapping[str, float],
    *,
    total: float,
    now: float,
    authority_evidence: bool = False,
    methods_with_data: int = 0,
    dev_revert: bool = False,
) -> CheckResult:
    """Validate one proposed weight update against every tier rule.

    ``proposed`` may name a subset of layers; others keep their current weight.
    Flexible weights are renormalized to ``total`` first, then every weight is
    checked against its bounds. All-or-nothing: on any violation nothing changes.
    """
    by_id = {s.layer_id: s for s in slots}
    current = {s.layer_id: float(s.weight) for s in slots}
    reasons: list[str] = []

    unknown = sorted(set(proposed) - set(by_id))
    if unknown:
        return CheckResult(False, current, [f"unknown layers: {', '.join(unknown)}"])
    bad = [k for k, v in proposed.items() if not np.isfinite(v) or v < 0]
    if bad:
        return CheckResult(False, current, [f"weights must be finite and >= 0: {', '.join(sorted(bad))}"])

    merged = {**current, **{k: float(v) for k, v in proposed.items()}}
    changed = {k for k in proposed if abs(merged[k] - current[k]) > _EPS}
    if not changed:
        return CheckResult(True, current, ["no change"])

    # Reject explicit edits to frozen weights up front, with their reason.
    for k in sorted(changed):
        lo, hi, why = slot_bounds(
            by_id[k],
            now=now,
            authority_evidence=authority_evidence,
            methods_with_data=methods_with_data,
            dev_revert=dev_revert,
        )
        if why is not None:
            reasons.append(why)
    if reasons:
        return CheckResult(False, current, reasons)

    try:
        final = renormalize(merged, slots, total)
    except ValueError as e:
        return CheckResult(False, current, [str(e)])

    for s in slots:
        lo, hi, why = slot_bounds(
            s,
            now=now,
            authority_evidence=authority_evidence,
            methods_with_data=methods_with_data,
            dev_revert=dev_revert,
        )
        v = final[s.layer_id]
        if v < lo - _EPS or v > hi + _EPS:
            limit = why or f"allowed [{lo:.4g}, {hi:.4g}] for tier {as_tier(s.tier)}"
            reasons.append(f"{s.layer_id}: {current[s.layer_id]:.4g} -> {v:.4g} out of bounds ({limit})")
    if reasons:
        return CheckResult(False, current, reasons)
    return CheckResult(
        True, final, [f"ok: changed {', '.join(sorted(k for k in final if abs(final[k] - current[k]) > _EPS))}"]
    )


# -- vectorized form (batch backtests) ---------------------------------------


@dataclass(frozen=True)
class BatchBounds:
    """Per-layer arrays, in ``layer_ids`` order, for a batch of candidates."""

    layer_ids: tuple[str, ...]
    current: np.ndarray  # [L]
    lo: np.ndarray  # [L]
    hi: np.ndarray  # [L]
    free_flex: np.ndarray  # [L] bool: renormalization scales these
    pinned_flex_sum: float
    total: float
    frozen_reasons: tuple[str, ...]


def bounds(
    slots: Sequence[WeightSlot],
    *,
    total: float,
    now: float,
    authority_evidence: bool = False,
    methods_with_data: int = 0,
) -> BatchBounds:
    lo, hi, reasons = [], [], []
    for s in slots:
        a, b, why = slot_bounds(s, now=now, authority_evidence=authority_evidence, methods_with_data=methods_with_data)
        lo.append(a)
        hi.append(b)
        if why:
            reasons.append(why)
    free = np.array([as_tier(s.tier) == "flexible" and not s.pinned for s in slots], dtype=bool)
    pinned_sum = float(sum(s.weight for s in slots if as_tier(s.tier) == "flexible" and s.pinned))
    return BatchBounds(
        layer_ids=tuple(s.layer_id for s in slots),
        current=np.array([s.weight for s in slots], dtype=np.float64),
        lo=np.array(lo, dtype=np.float64),
        hi=np.array(hi, dtype=np.float64),
        free_flex=free,
        pinned_flex_sum=pinned_sum,
        total=float(total),
        frozen_reasons=tuple(reasons),
    )


def renormalize_batch(W: np.ndarray, b: BatchBounds) -> np.ndarray:
    """``W`` is ``[K, L]``. Returns a copy with free flexible columns scaled to the total.

    Rows whose free flexible sum is zero (and need a non-zero target) come back
    with NaN there, so ``valid_mask`` rejects them.
    """
    W = np.array(W, dtype=np.float64, copy=True)
    if not b.free_flex.any():
        return W
    target = b.total - b.pinned_flex_sum
    s = W[:, b.free_flex].sum(axis=1, keepdims=True)
    with np.errstate(divide="ignore", invalid="ignore"):
        scale = np.where(s > 0, max(target, 0.0) / s, np.nan if target > _EPS else 1.0)
    W[:, b.free_flex] *= scale
    return W


def valid_mask(W: np.ndarray, b: BatchBounds) -> np.ndarray:
    """``[K]`` bool: rows that satisfy every bound. Pass already-renormalized weights."""
    W = np.asarray(W, dtype=np.float64)
    ok = np.isfinite(W).all(axis=1)
    ok &= (W >= b.lo - _EPS).all(axis=1) & (W <= b.hi + _EPS).all(axis=1)
    if b.free_flex.any():
        flex_sum = W[:, b.free_flex].sum(axis=1) + b.pinned_flex_sum
        ok &= np.abs(flex_sum - b.total) <= 1e-6 * max(1.0, abs(b.total))
    return ok
