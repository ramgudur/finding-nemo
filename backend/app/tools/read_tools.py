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


# --- corroboration by reference inputs (Critic only, read-only) ------------------------------

REFERENCE_TIERS = ("stiff", "locked")
# Only inputs that locate something can corroborate a location; a field (e.g. a vector field) can't.
REFERENCE_SHAPES = ("point", "fixed_point", "document", "track")
CORROBORATION_WINDOW_S = 600.0
CORROBORATION_RADIUS_CELLS = 3.0
MAX_CORROBORATION_USERS = 200
CORROBORATED_SHARE = 0.5
_REF_CHUNK = 512


class CorroborationArgs(_Args):
    window_s: float = Field(
        default=CORROBORATION_WINDOW_S, gt=0, le=CORROBORATION_WINDOW_S, description="How far back to look, in seconds."
    )


def _min_dist(x: np.ndarray, y: np.ndarray, rx: np.ndarray, ry: np.ndarray) -> np.ndarray:
    """Distance from each (x, y) to its nearest reference point (meters), in chunks."""
    out = np.full(len(x), np.inf)
    for i in range(0, len(rx), _REF_CHUNK):
        dx = x[:, None] - rx[None, i : i + _REF_CHUNK]
        dy = y[:, None] - ry[None, i : i + _REF_CHUNK]
        out = np.minimum(out, np.sqrt(dx * dx + dy * dy).min(axis=1))
    return out


def get_corroboration(ctx: ToolContext, args: CorroborationArgs) -> dict[str, Any]:
    """How much each crowd reporter is corroborated by the app's reference inputs.

    Reference events: recent events of inputs whose tier is stiff or locked and that locate
    something (not field-shaped). Scored: every user of
    every flexible point/track input, by the share of their recent events within
    R = 3 x cell_m of any reference event. Deterministic code; the Critic only reads it."""
    spec = ctx.spec
    grid = ctx.runtime.grid
    now = ctx.now()
    since = now - args.window_s
    radius = CORROBORATION_RADIUS_CELLS * float(grid.cell_m)
    refs = [i for i in spec.inputs if i.tier in REFERENCE_TIERS and i.shape in REFERENCE_SHAPES]
    ref_x, ref_y, ref_counts = [], [], {}
    for inp in refs:
        ev = ctx.engine.source.events(ctx.app_id, inp.id, since, now)
        ref_counts[inp.id] = len(ev)
        if len(ev):
            x, y = grid.to_xy(ev.lat, ev.lon)
            ref_x.append(x)
            ref_y.append(y)
    n_ref = sum(ref_counts.values())
    base: dict[str, Any] = {
        "window_s": args.window_s,
        "radius_m": _r(radius, 1),
        "reference_inputs": [{"input_id": i.id, "tier": i.tier, "events": ref_counts[i.id]} for i in refs],
        "reference_events": n_ref,
    }
    if n_ref == 0:
        reason = "no stiff or locked input in this app" if not refs else "no reference events in the window"
        return {
            **base,
            "users": [],
            "summary": {"users_scored": 0, "users_corroborated": 0, "users_uncorroborated": 0},
            "note": f"{reason}: nothing to compare against, so corroboration gives no reason to change trust",
        }
    rx, ry = np.concatenate(ref_x), np.concatenate(ref_y)
    trust = ctx.db.list_trust(ctx.app_id)
    users: list[dict[str, Any]] = []
    for inp in spec.inputs:
        if inp.tier != "flexible" or inp.shape not in ("point", "track"):
            continue
        ev = ctx.engine.source.events(ctx.app_id, inp.id, since, now)
        if not len(ev):
            continue
        x, y = grid.to_xy(ev.lat, ev.lon)
        near = _min_dist(np.asarray(x, dtype=float), np.asarray(y, dtype=float), rx, ry) <= radius
        uids = np.array([u or "" for u in ev.user_id], dtype=object)
        for u in sorted({u for u in uids if u}):
            mask = uids == u
            n = int(mask.sum())
            users.append(
                {
                    "user_id": u,
                    "input_id": inp.id,
                    "events": n,
                    "corroborated_share": _r(float(near[mask].mean()), 3),
                    "trust": _r(float(trust.get(u, trust_mod.DEFAULT_TRUST)), 3),
                }
            )
    users.sort(key=lambda d: (-d["corroborated_share"], -d["events"], d["user_id"]))
    corroborated = [d for d in users if d["corroborated_share"] >= CORROBORATED_SHARE]
    shown = users
    if len(users) > MAX_CORROBORATION_USERS:  # keep both ends: the most and the least corroborated
        half = MAX_CORROBORATION_USERS // 2
        shown = users[:half] + users[-(MAX_CORROBORATION_USERS - half) :]
    return {
        **base,
        "users": shown,
        "truncated": len(shown) < len(users),
        "summary": {
            "users_scored": len(users),
            "users_corroborated": len(corroborated),
            "users_uncorroborated": sum(1 for d in users if d["corroborated_share"] == 0),
            "corroborated_below_full_trust": sum(1 for d in corroborated if d["trust"] < trust_mod.DEFAULT_TRUST),
        },
        "note": "users at full trust (1.0) cannot be raised further; only lowered users can be raised back",
    }
