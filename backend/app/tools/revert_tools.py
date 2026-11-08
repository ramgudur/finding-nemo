"""Dev-only revert of a recorded ``Change`` (CLAUDE.md rule 11: every change can be rolled back).

``revert_change`` puts the Change's ``before`` value back through the same tool paths and guards
that wrote it, as ``actor="dev"``, and the new Change carries ``reverts=<id>``:

- weight: ``guards.check_weights`` + ``guards.apply_weights``. One ``apply_changes`` writes one
  Change per moved layer (the renormalized vector), so the whole vector is reverted together: every
  sibling (same t, actor, backtest_id and reason) must still hold its ``after`` value. A pin Change
  is reverted by pinning back (``pin_weight``).
- trust: ``guards.apply_trust`` with ``dev_revert`` (no increase cap; trust stays in [0, 1]).
- poi: ``retire_poi`` / ``update_poi``, or ``reactivate_poi`` to undo a retire.
- connector: ``compiler_tools.set_connector`` (the path behind ``create_connector``, URL guard included).
- spec_confirm / spec_edit: not revertible here (a pending spec is edited with another PUT /spec).

What a dev revert skips (lead decision on #193, rule 11: undoing a wrong agent change is one step):
it needs no backtest, and it is exempt from the gates that exist to stop *agent drift*: the
evidence gates on weights (cold start, stiff authority evidence), the stiff +/-10% per hour window
and the trust increase cap (+0.1 per 5 min). The exemption is an explicit ``dev_revert`` flag on
the guard path (``guards.check_weights`` / ``guards.apply_trust``), which refuses it unless
``actor == "dev"``; no agent tool passes it. Hard bounds still hold: locked weights never move,
pins are respected (unpin first), the flexible creation-time sum holds (the whole vector of one
apply is reverted), trust stays in [0, 1].

The flexible +/-25% per-step limit is kept. It is not covered by "the agent's step was within
it": the bound is relative to the *current* weight, so undoing a decrease can exceed it. Example:
an agent moves a flexible layer 0.5 -> 0.375 (-25%, allowed); reverting 0.375 -> 0.5 is +33% of
0.375, more than 25%, so that revert is a 422 and the dev reverts in steps (or by a later change).

Errors: ``RevertNotFound`` (404), ``RevertStale`` (409: the target no longer holds the Change's
``after``, so a revert would silently overwrite a later change), ``GuardError`` (422).
"""

from __future__ import annotations

import json
import math
from typing import Any

from app.core.models import Change, ConnectorConfig
from app.tools import compiler_tools, guards, poi_tools, scoring_tools
from app.tools.context import ToolContext
from app.tools.guards import GuardError

_TOL = 1e-9


class RevertNotFound(LookupError):
    pass


class RevertStale(Exception):
    pass


def _reason(change: Change) -> str:
    return f"dev reverted change {change.id} ({change.kind} {change.target})"


def _same(a: Any, b: Any) -> bool:
    """Equal values, with float tolerance (values round-trip through JSON)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=_TOL)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    return a == b


def _show(value: Any) -> str:
    """A recorded value as compact JSON for an error message (not a Python repr)."""
    return json.dumps(value, separators=(",", ":"), sort_keys=True, default=str)


def _stale(change: Change, current: Any) -> RevertStale:
    return RevertStale(
        f"{change.kind} {change.target!r} changed since change {change.id} (now {_show(current)}, "
        f"that change left {_show(change.after)}); revert the later change first"
    )


def revert_change(ctx: ToolContext, change_id: str) -> Change:
    """Revert one Change of ``ctx.app_id``; returns the new Change (``reverts == change_id``)."""
    guards.require_dev(ctx, "reverting a change")
    change = ctx.db.get_change(change_id)
    if change is None or change.app_id != ctx.app_id:
        raise RevertNotFound(f"unknown change {change_id!r} for this app")
    if change.kind in ("spec_confirm", "spec_edit"):
        raise GuardError(
            f"{change.kind} changes can't be reverted here: confirmation is final, and a pending spec "
            "is changed back with another PUT /v1/apps/{app}/spec before it is confirmed"
        )
    handler = {"weight": _weight, "trust": _trust, "poi": _poi, "connector": _connector}[change.kind]
    return handler(ctx, change)


# -- weights -----------------------------------------------------------------------


def _is_pin(change: Change) -> bool:
    return isinstance(change.after, dict) and "pinned" in change.after


def _siblings(ctx: ToolContext, change: Change) -> list[Change]:
    """The other per-layer Changes written by the same weight update (one renormalized vector)."""
    return [
        c
        for c in ctx.db.list_changes(ctx.app_id, kind="weight")
        if not _is_pin(c)
        and c.t == change.t
        and c.actor == change.actor
        and c.backtest_id == change.backtest_id
        and c.reason == change.reason
    ]


def _weight(ctx: ToolContext, change: Change) -> Change:
    rt = ctx.runtime  # LookupError (app not running) -> the caller's 409
    if _is_pin(change):
        pinned_now = change.target in guards.pinned_layers(ctx)
        if pinned_now != bool(change.after["pinned"]):
            raise _stale(change, {"pinned": pinned_now})
        args = scoring_tools.PinWeightArgs(
            layer_id=change.target, pinned=bool(change.before["pinned"]), reason=_reason(change)
        )
        out = scoring_tools.pin_weight(ctx, args, reverts=change.id)
        return _get(ctx, out["change_id"])

    group = _siblings(ctx, change) or [change]
    by_layer = {c.target: c for c in group}
    for c in group:
        if c.target not in rt.weights:
            raise GuardError(f"layer {c.target!r} is no longer weighted")
        if not _same(rt.weights[c.target], c.after["weight"]):
            raise _stale(c, {"weight": rt.weights[c.target]})
    proposed = {layer: float(c.before["weight"]) for layer, c in by_layer.items()}
    # dev_revert: no evidence gates, no stiff hourly window; locked, pins, flexible sum and step still hold.
    final = guards.check_weights(ctx, proposed, methods_with_data=0, authority_evidence=False, dev_revert=True)
    moved = sorted(k for k in final if k not in proposed and abs(final[k] - rt.weights[k]) > _TOL)
    off = sorted(k for k, v in proposed.items() if abs(final[k] - v) > _TOL)
    if moved or off:
        raise GuardError(
            "reverting would not restore the recorded weights (renormalization would move "
            f"{', '.join(moved or off)}); revert the later weight changes first"
        )
    changes = guards.apply_weights(
        ctx, final, reason=_reason(change), backtest_id=None, reverts={k: c.id for k, c in by_layer.items()}
    )
    mine = next((c for c in changes if c.target == change.target), None)
    if mine is None:  # before == after: nothing to move
        raise GuardError(f"change {change.id} did not move {change.target!r}; nothing to revert")
    return mine


# -- trust -------------------------------------------------------------------------


def _trust(ctx: ToolContext, change: Change) -> Change:
    now, _ = guards.current_trust(ctx, change.target)
    if not _same(now, change.after["trust"]):
        raise _stale(change, {"trust": now})
    # dev_revert: exempt from the +0.1 per 5 min increase cap; trust must still be in [0, 1].
    return guards.apply_trust(
        ctx,
        change.target,
        float(change.before["trust"]),
        reason=_reason(change),
        backtest_id=None,
        reverts=change.id,
        dev_revert=True,
    )


# -- POIs --------------------------------------------------------------------------


def _poi(ctx: ToolContext, change: Change) -> Change:
    p = ctx.db.get_poi(ctx.app_id, change.target)
    if p is None:
        raise RevertNotFound(f"POI {change.target!r} no longer exists")
    current = poi_tools._poi_state(p)
    if not _same(current, change.after):
        raise _stale(change, current)
    before = change.before
    reason = _reason(change)
    if before is None or before.get("status") == "retired":  # undo a create or a reactivation
        out = poi_tools.retire_poi(ctx, poi_tools.RetirePoiArgs(poi_id=p.id, reason=reason), reverts=change.id)
    elif p.status == "retired":  # undo a retire
        out = poi_tools.reactivate_poi(
            ctx, p.id, before["label"], float(before["confidence"]), reason=reason, reverts=change.id
        )
    else:  # undo a relabel / confidence change
        args = poi_tools.UpdatePoiArgs(poi_id=p.id, label=before["label"], confidence=float(before["confidence"]))
        out = poi_tools.update_poi(ctx, args, reverts=change.id, reason=reason)
    return _get(ctx, out["change_id"])


# -- connectors --------------------------------------------------------------------


def _connector(ctx: ToolContext, change: Change) -> Change:
    spec = ctx.db.get_app(ctx.app_id)
    inp = next((i for i in spec.inputs if i.id == change.target), None) if spec is not None else None
    if inp is None:
        raise RevertNotFound(f"input {change.target!r} no longer exists")
    current = inp.connector.model_dump(mode="json") if inp.connector is not None else None
    if not _same(current, change.after):
        raise _stale(change, current)
    try:
        config = ConnectorConfig.model_validate(change.before) if change.before is not None else None
    except ValueError as e:
        raise GuardError(f"the recorded connector is no longer valid: {e}") from None
    return compiler_tools.set_connector(ctx, change.target, config, reason=_reason(change), reverts=change.id)


def _get(ctx: ToolContext, change_id: str) -> Change:
    c = ctx.db.get_change(change_id)
    assert c is not None  # noqa: S101 - the tool just wrote it
    return c
