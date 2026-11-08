"""Critic write tools: run_backtest, apply_changes, set_user_trust; plus the dev-only pin_weight.

Flow for a weight change (CLAUDE.md rule 1):
    run_backtest(weights=...)  -> tier guards first (reject early with reasons), then a held-out
                                  backtest of the *final renormalized* weights; the tested
                                  candidate is remembered by backtest_id
    apply_changes(backtest_id) -> holdout passed? fresh? unused? live weights unchanged?
                                  tier guards again on the same candidate -> apply + Change log
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.models import Change
from app.engine import backtest as bt_mod
from app.engine.operators.base import Events
from app.engine.tick import ALL_HISTORY
from app.tools import guards
from app.tools.context import TestedCandidate, ToolContext
from app.tools.guards import GuardError


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunBacktestArgs(_Args):
    weights: dict[str, float] = Field(
        default_factory=dict, description="Proposed weights by layer_id; omitted layers keep their weight."
    )
    trust: dict[str, float] = Field(default_factory=dict, description="Proposed trust by user_id, in [0, 1].")
    reason: str = Field(description="Why this change should help; goes into the trace and the Change log.")


class ApplyChangesArgs(_Args):
    backtest_id: str = Field(description="A backtest from run_backtest whose holdout passed.")
    reason: str


class SetUserTrustArgs(_Args):
    user_id: str
    trust: float = Field(ge=0, le=1, description="Must be lower than the current trust. Raise trust via run_backtest.")
    reason: str


class PinWeightArgs(_Args):
    layer_id: str
    pinned: bool = True
    reason: str = "pinned by dev"


def _events_for_backtest(ctx: ToolContext, params: bt_mod.BacktestParams) -> dict[str, Events]:
    """Each input's events over the backtest window plus the history its operators need before it
    (all history for override inputs), so the replay sees what the live tick sees."""
    now = ctx.now()
    out = {}
    for inp in ctx.spec.inputs:
        back = ctx.runtime.input_lookback(inp.id, ctx.engine.lookback_s)
        since = now - params.window_s - back if math.isfinite(back) else ALL_HISTORY
        out[inp.id] = ctx.engine.source.events(ctx.app_id, inp.id, since, now)
    return out


def run_backtest(ctx: ToolContext, args: RunBacktestArgs) -> dict[str, Any]:
    reason = guards.require_reason(args.reason)
    if not args.weights and not args.trust:
        raise GuardError("propose weights or trust to backtest")
    if args.weights and args.trust:
        raise GuardError("backtest weights and trust separately, so each change has its own evidence")
    rt = ctx.runtime
    baseline = dict(rt.weights)
    params = bt_mod.BacktestParams()
    events = _events_for_backtest(ctx, params)
    trust = guards.live_trust_map(ctx)
    now = ctx.now()

    final = baseline
    trust_changes: dict[str, tuple[float, float]] = {}
    cand_trust = None
    if args.weights:
        # Cold start and stiff evidence need to know which methods have data: probe with a
        # no-op backtest first so a frozen proposal fails fast with the guard's reason. A stiff
        # change can only ever pass if authority_confirmation has data; whether it actually
        # *decided* the backtest is checked after the real run and again at apply.
        probe = bt_mod.run_backtest(rt.spec, events, now=now, weights=baseline, trust=trust, params=params)
        with_data = set(probe.baseline_train) | set(probe.baseline_holdout)
        final = guards.check_weights(
            ctx,
            args.weights,
            methods_with_data=len(with_data),
            authority_evidence="authority_confirmation" in with_data,
        )
        if all(abs(final[k] - baseline[k]) <= 1e-12 for k in final):
            raise GuardError(
                "no change after renormalization: pinned or frozen layers absorb it "
                "(flexible weights must keep their creation-time sum)"
            )
    else:
        for user_id, after in args.trust.items():
            before, last_inc = guards.current_trust(ctx, user_id)
            guards.check_trust(before, after, last_inc, now, reason)
            trust_changes[user_id] = (before, after)
        cand_trust = {**trust, **{u: a for u, (_, a) in trust_changes.items()}}

    out = bt_mod.run_backtest(
        rt.spec,
        events,
        now=now,
        weights=baseline,
        trust=trust,
        candidate_weights=final if args.weights else None,
        candidate_trust=cand_trust,
        params=params,
    )
    ctx.db.add_backtest(ctx.app_id, out.backtest)
    with_data = set(out.baseline_train) | set(out.baseline_holdout)
    # Stiff weights move only when authority confirmation decided the backtest (CLAUDE.md rule 5);
    # apply_changes re-checks the tier rules with exactly this flag.
    authority_decided = out.deciding_method == "authority_confirmation"
    tier_of = guards.layer_tiers(ctx)
    stiff_moved = [k for k in final if tier_of[k] == "stiff" and abs(final[k] - baseline[k]) > 1e-12]
    note = reason
    if stiff_moved and out.backtest.passed and not authority_decided:
        note = (
            f"{reason} | stiff layers {', '.join(stiff_moved)} can't be applied: the deciding method was "
            f"{out.deciding_method}, not authority_confirmation"
        )
    ctx.candidates.put(
        TestedCandidate(
            backtest_id=out.backtest.id,
            app_id=ctx.app_id,
            kind="weight" if args.weights else "trust",
            passed=out.backtest.passed,
            t=now,
            baseline_weights=baseline,
            baseline_trust=dict(trust),
            weights=final,
            trust_changes=trust_changes,
            methods_with_data=len(with_data),
            authority_evidence=authority_decided,
            reason=out.reason,
        )
    )
    return {
        "backtest_id": out.backtest.id,
        "passed": out.backtest.passed,
        "reason": out.reason,
        "deciding_method": out.deciding_method,
        "baseline_holdout": out.baseline_holdout,
        "candidate_holdout": out.backtest.holdout_scores,
        "candidate_train": out.backtest.train_scores,
        "tested_weights": {k: round(v, 6) for k, v in final.items() if abs(v - baseline[k]) > 1e-12},
        "tested_trust": {u: a for u, (_, a) in trust_changes.items()},
        "elapsed_ms": round(out.backtest.elapsed_ms, 1),
        "note": note,
    }


def apply_changes(ctx: ToolContext, args: ApplyChangesArgs) -> dict[str, Any]:
    reason = guards.require_reason(args.reason)
    cand = guards.check_candidate_for_apply(ctx, args.backtest_id)
    if cand.kind == "weight":
        final = guards.check_weights(
            ctx, cand.weights, methods_with_data=cand.methods_with_data, authority_evidence=cand.authority_evidence
        )
        if any(abs(final[k] - cand.weights[k]) > 1e-9 for k in final):
            raise GuardError("re-check produced different weights than the backtest tested; rerun it")
        changes = guards.apply_weights(ctx, final, reason=reason, backtest_id=cand.backtest_id)
    else:
        for user_id, (_, after) in cand.trust_changes.items():  # all-or-nothing: check before writing
            before, last_inc = guards.current_trust(ctx, user_id)
            guards.check_trust(before, after, last_inc, ctx.now(), reason)
        changes = [
            guards.apply_trust(ctx, u, a, reason=reason, backtest_id=cand.backtest_id)
            for u, (_, a) in sorted(cand.trust_changes.items())
        ]
    cand.used = True
    return {
        "applied": [{"target": c.target, "before": c.before, "after": c.after} for c in changes],
        "backtest_id": cand.backtest_id,
        "change_ids": [c.id for c in changes],
    }


def set_user_trust(ctx: ToolContext, args: SetUserTrustArgs) -> dict[str, Any]:
    """Lower one user's trust. Raising trust is the risky direction and needs evidence:
    run_backtest(trust=...) then apply_changes (CLAUDE.md rule 5)."""
    reason = guards.require_reason(args.reason)
    before, _ = guards.current_trust(ctx, args.user_id)
    if args.trust > before:
        raise GuardError(
            f"set_user_trust only lowers trust ({before:.2f} -> {args.trust:.2f} is an increase); "
            "raise it with run_backtest(trust=...) and then apply_changes"
        )
    c = guards.apply_trust(ctx, args.user_id, args.trust, reason=reason, backtest_id=None)
    return {"user_id": args.user_id, "before": c.before["trust"], "after": c.after["trust"], "change_id": c.id}


def pin_weight(ctx: ToolContext, args: PinWeightArgs) -> dict[str, Any]:
    """Dev only. A pinned weight never moves (renormalization included) until unpinned."""
    guards.require_dev(ctx, "pinning a weight")
    rt = ctx.runtime
    if args.layer_id not in rt.weights:
        raise GuardError(f"unknown layer {args.layer_id!r}")
    was = args.layer_id in guards.pinned_layers(ctx)
    w = rt.weights[args.layer_id]
    c = Change(
        id=guards.new_change_id(),
        app_id=ctx.app_id,
        kind="weight",
        target=args.layer_id,
        before={"weight": w, "pinned": was},
        after={"weight": w, "pinned": args.pinned},
        reason=guards.require_reason(args.reason),
        actor="dev",
        t=ctx.now_dt(),
    )
    ctx.db.add_change(c)
    return {"layer_id": args.layer_id, "pinned": args.pinned, "weight": w, "change_id": c.id}
