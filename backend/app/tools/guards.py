"""Guards: every agent-proposed state change is validated here, in code, never in prompts.

CLAUDE.md rules enforced here:
1. Nemotron proposes, code decides. A weight change needs a ``backtest_id`` whose holdout
   passed, and the candidate is re-checked against the tier rules right before it's applied.
4. Locked stays locked: tiers, verification methods and locked weights can't be changed by
   agents (there is no tool for the first two; ``engine/tiers.py`` freezes locked weights).
5. Tier bounds, renormalization, cold start, pins (``engine/tiers.py``) and the trust cap
   (+0.1 per user per 5 min of Clock time; lowering is free with a reason).
11. Every applied change writes a ``Change`` with before/after so the dev can roll it back.

A failed check raises ``GuardError`` with reasons meant for the trace.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Mapping, Sequence

from app.core.models import Change
from app.engine import tiers
from app.engine import trust as trust_mod
from app.tools.context import TestedCandidate, ToolContext

BACKTEST_MAX_AGE_S = 600.0


class GuardError(Exception):
    def __init__(self, reasons: str | Sequence[str]) -> None:
        self.reasons = [reasons] if isinstance(reasons, str) else list(reasons)
        super().__init__("; ".join(self.reasons))


def require_dev(ctx: ToolContext, what: str) -> None:
    if ctx.actor != "dev":
        raise GuardError(f"{what} is a dev-only action")


def require_reason(reason: str | None) -> str:
    if not reason or not reason.strip():
        raise GuardError("a reason is required")
    return reason.strip()


# -- weights ---------------------------------------------------------------------


def layer_tiers(ctx: ToolContext) -> dict[str, str]:
    """layer_id -> tier set at creation (the input's tier in the confirmed spec)."""
    return {o.layer_id: inp.tier for inp in ctx.spec.inputs for o in inp.operators}


def flexible_total(ctx: ToolContext) -> float:
    """Sum of flexible weights as confirmed in the spec (the value set at creation)."""
    tier_of = layer_tiers(ctx)
    return float(sum(o.weight for inp in ctx.spec.inputs for o in inp.operators if tier_of[o.layer_id] == "flexible"))


def pinned_layers(ctx: ToolContext) -> set[str]:
    """Layers whose latest dev pin change says pinned. Pins live in the Change log (durable, reversible)."""
    pinned: dict[str, bool] = {}
    for c in ctx.db.list_changes(ctx.app_id, kind="weight"):  # oldest first
        if isinstance(c.after, dict) and "pinned" in c.after:
            pinned[c.target] = bool(c.after["pinned"])
    return {k for k, v in pinned.items() if v}


def created_weights(ctx: ToolContext) -> dict[str, float]:
    """layer_id -> weight set at creation (the confirmed spec's value; applied changes never edit it)."""
    return {o.layer_id: float(o.weight) for inp in ctx.spec.inputs for o in inp.operators}


def weight_slots(ctx: ToolContext) -> list[tiers.WeightSlot]:
    rt = ctx.runtime
    tier_of = layer_tiers(ctx)
    created = created_weights(ctx)
    pins = pinned_layers(ctx)
    slots = []
    for layer_id, w in rt.weights.items():
        history = [(r.t, r.weight) for r in ctx.db.weight_history_since(ctx.app_id, layer_id, 0.0)]
        slots.append(
            tiers.WeightSlot(
                layer_id,
                tier_of[layer_id],
                w,
                pinned=layer_id in pins,
                history=history,
                created_weight=created.get(layer_id),
            )
        )
    return slots


def check_weights(
    ctx: ToolContext,
    proposed: Mapping[str, float],
    *,
    methods_with_data: int,
    authority_evidence: bool,
    dev_revert: bool = False,
) -> dict[str, float]:
    """Tier rules on a proposed update. Returns the final (renormalized) weights or raises.
    ``dev_revert`` (dev only, never set by an agent tool) lifts the anti-drift gates; see tiers.slot_bounds."""
    if dev_revert:
        require_dev(ctx, "the dev-revert exemption")
    res = tiers.check_update(
        weight_slots(ctx),
        proposed,
        total=flexible_total(ctx),
        now=ctx.now(),
        authority_evidence=authority_evidence,
        methods_with_data=methods_with_data,
        dev_revert=dev_revert,
    )
    if not res.ok:
        raise GuardError(res.reasons)
    return res.weights


def check_candidate_for_apply(ctx: ToolContext, backtest_id: str) -> TestedCandidate:
    """The backtest exists, is ours, passed on the holdout, is fresh, unused, and the weights
    it was measured against are still the live ones."""
    cand = ctx.candidates.get(backtest_id)
    bt = ctx.db.get_backtest(ctx.app_id, backtest_id)
    if cand is None or bt is None or cand.app_id != ctx.app_id:
        raise GuardError(f"unknown backtest {backtest_id!r} for this app; run_backtest first")
    if not bt.passed or not cand.passed:
        raise GuardError(f"backtest {backtest_id} did not pass on the holdout: {cand.reason}")
    if cand.used:
        raise GuardError(f"backtest {backtest_id} was already applied")
    age = ctx.now() - cand.t
    if age > BACKTEST_MAX_AGE_S:
        raise GuardError(f"backtest {backtest_id} is {age:.0f} s old (max {BACKTEST_MAX_AGE_S:.0f}); rerun it")
    live = ctx.runtime.weights
    drift = sorted(k for k, v in cand.baseline_weights.items() if abs(live.get(k, float("nan")) - v) > 1e-9)
    if drift:
        raise GuardError(f"weights changed since backtest {backtest_id} ({', '.join(drift)}); rerun it")
    # Trust drift: the backtest replayed operators with a trust map; if any user's trust moved since
    # (or a trust candidate's starting value is no longer current), its evidence no longer holds.
    live_trust = live_trust_map(ctx)
    users = set(cand.baseline_trust) | set(live_trust)
    moved = {u for u in users if abs(live_trust.get(u, 1.0) - cand.baseline_trust.get(u, 1.0)) > 1e-9}
    moved |= {u for u, (before, _) in cand.trust_changes.items() if abs(current_trust(ctx, u)[0] - before) > 1e-9}
    if moved:
        shown = ", ".join(sorted(moved)[:5]) + (" ..." if len(moved) > 5 else "")
        raise GuardError(f"trust changed since backtest {backtest_id} ({shown}); rerun it")
    return cand


def live_trust_map(ctx: ToolContext) -> dict[str, float]:
    """The trust map the tick (and so the backtest replay) uses for this app."""
    return dict(ctx.engine.trust(ctx.app_id)) if ctx.engine.trust else {}


def apply_weights(
    ctx: ToolContext,
    new: Mapping[str, float],
    *,
    reason: str,
    backtest_id: str | None,
    reverts: Mapping[str, str] | None = None,
) -> list[Change]:
    """Write the change everywhere: live engine, weights history, Change log. Returns the Changes.
    ``reverts`` maps layer_id -> the Change id each new Change reverts (dev reverts only)."""
    rt = ctx.runtime
    tier_of = layer_tiers(ctx)
    now, now_dt = ctx.now(), ctx.now_dt()
    before = dict(rt.weights)
    changed = {k: float(v) for k, v in new.items() if abs(before[k] - float(v)) > 1e-12}
    if not changed:
        return []
    rt.set_weights(changed)
    changes = []
    for layer_id, w in sorted(changed.items()):
        ctx.db.record_weight(ctx.app_id, layer_id, w, tier_of[layer_id], now, ctx.actor, reason, backtest_id)
        c = Change(
            id=new_change_id(),
            app_id=ctx.app_id,
            kind="weight",
            target=layer_id,
            before={"weight": before[layer_id]},
            after={"weight": w},
            reason=reason,
            backtest_id=backtest_id,
            actor=ctx.actor,
            t=now_dt,
            reverts=(reverts or {}).get(layer_id),
        )
        ctx.db.add_change(c)
        changes.append(c)
    return changes


# -- trust -----------------------------------------------------------------------


def check_trust(
    before: float,
    after: float,
    last_increase_at: float | None,
    now: float,
    reason: str,
    *,
    dev_revert: bool = False,
) -> None:
    """Lowering is free (with a reason). Increases: engine/trust.py bounds (+0.1, one per 5 min of Clock time).
    ``dev_revert``: a dev undoing a recorded change skips the increase rate cap (it exists to stop agent
    drift); trust must still be in [0, 1]."""
    if dev_revert:
        if not math.isfinite(after) or not 0.0 <= after <= 1.0:
            raise GuardError(f"trust must be in [0, 1], got {after}")
        if not reason.strip():
            raise GuardError("a reason is required")
        return
    res = trust_mod.check_trust_change(before, after, now=now, last_increase_at=last_increase_at, reason=reason)
    if not res.ok:
        raise GuardError(res.reason)


def current_trust(ctx: ToolContext, user_id: str) -> tuple[float, float | None]:
    """``(score, last_increase_t)``. Unknown users have trust 1.0. The last increase comes from
    the Change log (only real increases count; a first record that lowers trust doesn't start the
    5-minute window)."""
    rec = ctx.db.get_trust(ctx.app_id, user_id)
    score = 1.0 if rec is None else float(rec.score)
    increases = [
        c.t.timestamp()
        for c in ctx.db.list_changes(ctx.app_id, kind="trust")
        if c.target == user_id and float(c.after["trust"]) > float(c.before["trust"])
    ]
    return score, max(increases, default=None)


def apply_trust(
    ctx: ToolContext,
    user_id: str,
    after: float,
    *,
    reason: str,
    backtest_id: str | None,
    reverts: str | None = None,
    dev_revert: bool = False,
) -> Change:
    if dev_revert:
        require_dev(ctx, "the dev-revert exemption")
    before, last_inc = current_trust(ctx, user_id)
    check_trust(before, after, last_inc, ctx.now(), reason, dev_revert=dev_revert)
    ctx.db.set_trust(ctx.app_id, user_id, after, ctx.now())
    c = Change(
        id=new_change_id(),
        app_id=ctx.app_id,
        kind="trust",
        target=user_id,
        before={"trust": before},
        after={"trust": after},
        reason=reason,
        backtest_id=backtest_id,
        actor=ctx.actor,
        t=ctx.now_dt(),
        reverts=reverts,
    )
    ctx.db.add_change(c)
    return c


def new_change_id() -> str:
    return uuid.uuid4().hex
