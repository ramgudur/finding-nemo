"""Critic read tools: compact summaries of the live engine state, never raw event arrays.

All of these are read-only except ``annotate``, which writes a TraceEntry note (no state change).
Numbers are rounded so tool results stay small in the agent's context.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from app.core.models import TraceEntry
from app.engine import trust as trust_mod
from app.engine.operators.base import Events
from app.engine.tiers import MIN_METHODS_WITH_DATA
from app.engine.verification import METHODS, VerificationContext, cell_events, evaluate, methods_with_data
from app.tools import guards
from app.tools.context import ToolContext
from app.tools.guards import GuardError


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_Args):
    pass


class LayerStatsArgs(_Args):
    layer_id: str | None = Field(default=None, description="One layer, or omit for all layers.")


class WindowArgs(_Args):
    window_s: float = Field(default=300.0, gt=0, le=600, description="How far back to look, in seconds.")


class AnnotateArgs(_Args):
    text: str = Field(min_length=1, max_length=1000, description="A note for the dev, shown in the trace.")


def _r(x: float, nd: int = 4) -> float:
    return round(float(x), nd)


def _latest(ctx: ToolContext):
    last = ctx.runtime.last
    if last is None:
        raise GuardError("no tick has run yet for this app")
    return last


def _scalar_extras(extras: dict[str, Any]) -> dict[str, Any]:
    """Keep small JSON-able extras (counts, kinds, point estimates); drop arrays and grids."""
    out: dict[str, Any] = {}
    for k, v in extras.items():
        if isinstance(v, bool | str) or v is None:
            out[k] = v
        elif isinstance(v, int | float | np.integer | np.floating):
            out[k] = _r(v)
        elif isinstance(v, list | tuple) and len(v) <= 4 and all(isinstance(e, int | float) for e in v):
            out[k] = [_r(e, 6) for e in v]
        elif isinstance(v, dict) and len(v) <= 8 and all(isinstance(e, int | float | str) for e in v.values()):
            out[k] = {kk: _r(e, 6) if isinstance(e, int | float) else e for kk, e in v.items()}
    return out


def get_map_summary(ctx: ToolContext, args: NoArgs) -> dict[str, Any]:
    last = _latest(ctx)
    tier_of = guards.layer_tiers(ctx)
    pins = guards.pinned_layers(ctx)
    score = last.score
    return {
        "t": last.t,
        "grid": {"shape": list(score.shape), "cell_m": ctx.runtime.grid.cell_m},
        "score": {
            "max": _r(score.max()),
            "p99": _r(np.percentile(score, 99)),
            "cells_over_0.5": int((score >= 0.5).sum()),
        },
        "override_cells": int((last.override > 0).sum()),
        "hotspots": [
            {
                "id": h.id,
                "lat": _r(h.lat, 6),
                "lon": _r(h.lon, 6),
                "peak": _r(h.score),
                "visibility": h.visibility,
                "area_m2": _r(h.area_m2, 1),
                "persisted_ticks": h.persisted_ticks,
                "contributing_layers": list(h.contributing_layers),
            }
            for h in last.hotspots
        ],
        "weights": {
            k: {"weight": _r(w), "tier": tier_of[k], "pinned": k in pins} for k, w in sorted(last.weights.items())
        },
        "flexible_total": _r(guards.flexible_total(ctx)),
        "errors": last.errors[:10],
    }


def get_layer_stats(ctx: ToolContext, args: LayerStatsArgs) -> dict[str, Any]:
    last = _latest(ctx)
    rt = ctx.runtime
    info = {li.layer_id: li for li in rt.layers}
    if args.layer_id is not None and args.layer_id not in info:
        raise GuardError(f"unknown layer {args.layer_id!r}")
    ids = [args.layer_id] if args.layer_id else [li.layer_id for li in rt.layers]
    out = {}
    for lid in ids:
        li = info[lid]
        entry: dict[str, Any] = {"input": li.input_id, "op": li.op, "override": li.is_override}
        if lid in last.layer_ids:
            layer = last.normalized[last.layer_ids.index(lid)]
            r, c = np.unravel_index(int(np.argmax(layer)), layer.shape)
            lat, lon = rt.grid.cell_center(r, c)
            entry.update(
                weight=_r(rt.weights[lid]),
                max=_r(layer.max()),
                mean=_r(layer.mean()),
                nonzero_share=_r((layer > 1e-3).mean()),
                peak={"lat": _r(lat, 6), "lon": _r(lon, 6)} if layer.max() > 0 else None,
            )
        entry["extras"] = _scalar_extras(last.extras.get(lid, {}))
        out[lid] = entry
    return {"t": last.t, "layers": out}


def _window_events(ctx: ToolContext, window_s: float) -> dict[str, Events]:
    now = ctx.now()
    return {inp.id: ctx.engine.source.events(ctx.app_id, inp.id, now - window_s, now) for inp in ctx.spec.inputs}


def get_verification_report(ctx: ToolContext, args: WindowArgs) -> dict[str, Any]:
    rt = ctx.runtime
    now = ctx.now()
    win = rt.cache.window(now - args.window_s, now)
    if len(win.t) < 2:
        raise GuardError("not enough ticks cached yet for a verification report")
    layer_input = {li.layer_id: li.input_id for li in rt.layers}
    vctx = VerificationContext(
        layer_inputs=tuple(layer_input[i] for i in win.layer_ids),
        cell_m=rt.grid.cell_m,
        events={k: cell_events(ev, rt.grid) for k, ev in _window_events(ctx, args.window_s).items()},
        authority_inputs=frozenset(i.id for i in ctx.spec.inputs if i.tier == "locked"),
        override_inputs=frozenset(li.input_id for li in rt.layers if li.is_override),
    )
    priority = [v.method for v in sorted(ctx.spec.verification, key=lambda v: v.priority)] or list(METHODS)
    w = np.array([rt.weights[i] for i in win.layer_ids], dtype=float)
    res = evaluate(win, w, vctx, priority)
    with_data = methods_with_data(res)
    return {
        "window": {"from": float(win.t[0]), "to": float(win.t[-1]), "ticks": len(win.t)},
        "methods": {
            m: {
                "priority": i + 1,
                "has_data": res[m].has_data,
                "value": None if res[m].value is None else _r(res[m].value),
                "detail": _scalar_extras(res[m].detail),
            }
            for i, m in enumerate(priority)
        },
        "methods_with_data": with_data,
        "cold_start": len(with_data) < MIN_METHODS_WITH_DATA,
    }


def _concat(batches: list[Events]) -> Events:
    batches = [b for b in batches if len(b)]
    if not batches:
        from app.engine.tick import empty_events

        return empty_events()
    names = sorted({k for b in batches for k in b.fields})
    return Events(
        user_id=np.concatenate([b.user_id for b in batches]),
        t=np.concatenate([b.t for b in batches]),
        lat=np.concatenate([b.lat for b in batches]),
        lon=np.concatenate([b.lon for b in batches]),
        fields={
            k: np.concatenate([b.fields.get(k, np.full(len(b), None, dtype=object)) for b in batches]) for k in names
        },
    )


def get_suspicious_users(ctx: ToolContext, args: WindowArgs) -> dict[str, Any]:
    """Spoof flags per input (with that input's plausibility limits), merged per user. Flags, not verdicts."""
    by_input = _window_events(ctx, args.window_s)
    events = _concat(list(by_input.values()))
    plaus = {inp.id: inp.plausibility for inp in ctx.spec.inputs}
    shapes = {inp.id: inp.shape for inp in ctx.spec.inputs}  # shape checks only on track inputs
    suspects = trust_mod.suspicious_users_by_input(by_input, plaus, shape_by_input=shapes)  # each input's own limits
    out = trust_mod.summarize(suspects)
    for s in out["top"]:
        s["trust"] = _r(guards.current_trust(ctx, s["user_id"])[0], 3)
    return {"window_s": args.window_s, "n_users": len({u for u in events.user_id if u}), **out}


def annotate(ctx: ToolContext, args: AnnotateArgs) -> dict[str, Any]:
    """A note for the dev in the trace. Changes nothing."""
    if ctx.agent is None:
        raise GuardError("annotate needs an agent name")
    ctx.db.add_trace(TraceEntry(t=ctx.now_dt(), app_id=ctx.app_id, agent=ctx.agent, action="annotate", why=args.text))
    return {"noted": True}
