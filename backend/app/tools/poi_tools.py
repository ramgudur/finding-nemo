"""POI Labeler tools: read candidates and evidence, then create/update/retire POIs under guards.

Guards (CLAUDE.md rules 7, 8, 11):
- POIs come only from code-generated candidates in the latest tick. Tools take a
  ``candidate_id``, never coordinates; a POI's position and radius are copied from its candidate.
- Labels come only from the spec's ``label_set``. A new label type is a proposal: the tool
  rejects it and says to use ``annotate`` so it shows in the trace for the dev.
- ``poi_labels.mode == "off"`` blocks all writes. Confidence below ``min_confidence`` is rejected.
- Visibility: a sensitive label is always authority; a destination label is public (the public
  route already reveals it). Otherwise public only if the candidate has contributing layers and
  all of them are public output layers (the hotspot rule); else authority.
- One active POI per candidate (relabel with ``update_poi``). Every write records a ``Change``.
- POIs are outputs only: nothing here touches the engine, weights or scores.

Read tools return summaries only: counts and aggregates, never raw events or user ids.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from app.core.models import POI, Candidate, Change, Evidence, Visibility
from app.tools import geo_tools, guards
from app.tools.context import ToolContext
from app.tools.guards import GuardError

MAX_EVIDENCE = 8
TIME_BUCKETS = 12


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_Args):
    pass


class FindCandidatesArgs(_Args):
    min_persisted_ticks: int = Field(default=0, ge=0)
    source: str | None = Field(default=None, description="A candidate generator name, or omit for all.")
    unlabeled_only: bool = False


class CandidateArgs(_Args):
    candidate_id: str


class CandidateRadiusArgs(_Args):
    candidate_id: str
    radius_m: float = Field(default=150.0, gt=0, le=geo_tools.MAX_RADIUS_M)


class CandidateWindowArgs(_Args):
    candidate_id: str
    window_s: float = Field(default=600.0, gt=0, le=24 * 3600)


class EvidenceIn(_Args):
    kind: str = Field(max_length=40)
    text: str = Field(max_length=300)
    data: dict[str, Any] = Field(default_factory=dict)


class CreatePoiArgs(_Args):
    candidate_id: str
    label: str
    confidence: float = Field(ge=0, le=1)
    evidence: list[EvidenceIn] = Field(default_factory=list, max_length=MAX_EVIDENCE)


class UpdatePoiArgs(_Args):
    poi_id: str
    label: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    evidence: list[EvidenceIn] | None = Field(default=None, max_length=MAX_EVIDENCE)


class RetirePoiArgs(_Args):
    poi_id: str
    reason: str


def _r(x: float, nd: int = 4) -> float:
    return round(float(x), nd)


def _candidates(ctx: ToolContext) -> dict[str, Candidate]:
    last = ctx.runtime.last
    if last is None:
        raise GuardError("no tick has run yet for this app")
    return {c.id: c for c in last.candidates}


def _candidate(ctx: ToolContext, candidate_id: str) -> Candidate:
    cand = _candidates(ctx).get(candidate_id)
    if cand is None:
        raise GuardError(f"unknown candidate {candidate_id!r}: POIs come only from current code-made candidates")
    return cand


def _active_pois(ctx: ToolContext) -> list[POI]:
    return ctx.db.list_pois(ctx.app_id, status="active")


def _cand_summary(c: Candidate, labels: dict[str, str]) -> dict[str, Any]:
    return {
        "id": c.id,
        "lat": _r(c.lat, 6),
        "lon": _r(c.lon, 6),
        "radius_m": _r(c.radius_m, 1),
        "source": c.source,
        "persisted_ticks": c.persisted_ticks,
        "contributing_layers": c.contributing_layers,
        "stats": {k: _r(v) for k, v in list(c.stats.items())[:8]},
        "label": labels.get(c.id),
    }


def get_poi_candidates(ctx: ToolContext, args: NoArgs) -> dict[str, Any]:
    cfg = ctx.spec.outputs.poi_labels
    labels = {p.candidate_id: p.label for p in _active_pois(ctx)}
    cands = sorted(_candidates(ctx).values(), key=lambda c: -c.persisted_ticks)
    return {
        "mode": cfg.mode,
        "min_confidence": cfg.min_confidence,
        "label_set": [lb.model_dump() for lb in cfg.label_set],
        "intent": ctx.spec.intent,
        "candidates": [_cand_summary(c, labels) for c in cands[:30]],
    }


def find_candidates(ctx: ToolContext, args: FindCandidatesArgs) -> dict[str, Any]:
    labeled = {p.candidate_id: p.label for p in _active_pois(ctx)}
    out = [
        _cand_summary(c, labeled)
        for c in _candidates(ctx).values()
        if c.persisted_ticks >= args.min_persisted_ticks
        and (args.source is None or c.source == args.source)
        and not (args.unlabeled_only and c.id in labeled)
    ]
    out.sort(key=lambda c: -c["persisted_ticks"])
    return {"candidates": out[:30], "n": len(out)}


def osm_features_near(ctx: ToolContext, args: CandidateRadiusArgs) -> dict[str, Any]:
    c = _candidate(ctx, args.candidate_id)
    feats = geo_tools.OVERPASS.features_near(c.lat, c.lon, args.radius_m)
    return {"candidate_id": c.id, "radius_m": args.radius_m, "features": feats}


def _events_near(ctx: ToolContext, c: Candidate, window_s: float):
    now = ctx.now()
    grid = ctx.runtime.grid
    cx, cy = grid.to_xy(c.lat, c.lon)
    radius = max(c.radius_m, grid.cell_m)
    out = {}
    for inp in ctx.spec.inputs:
        ev = ctx.engine.source.events(ctx.app_id, inp.id, now - window_s, now)
        if not len(ev):
            continue
        x, y = grid.to_xy(ev.lat, ev.lon)
        near = np.hypot(x - float(cx), y - float(cy)) <= radius
        if near.any():
            out[inp.id] = ev.select(near)
    return out


def input_values_at(ctx: ToolContext, args: CandidateWindowArgs) -> dict[str, Any]:
    """Per input, what arrived near the candidate: counts and declared numeric fields (aggregated)."""
    c = _candidate(ctx, args.candidate_id)
    numeric = {inp.id: [f.name for f in inp.fields if f.type in ("number", "integer")] for inp in ctx.spec.inputs}
    res = {}
    for inp_id, ev in _events_near(ctx, c, args.window_s).items():
        entry: dict[str, Any] = {"events": len(ev), "distinct_sources": len({u for u in ev.user_id if u})}
        fields = {}
        for name in numeric.get(inp_id, []):
            col = ev.column(name, np.nan)
            ok = col[np.isfinite(col)]
            if len(ok):
                fields[name] = {"mean": _r(ok.mean()), "min": _r(ok.min()), "max": _r(ok.max()), "n": int(len(ok))}
        if fields:
            entry["fields"] = fields
        res[inp_id] = entry
    return {"candidate_id": c.id, "window_s": args.window_s, "inputs": res}


def get_layer_stats_at(ctx: ToolContext, args: CandidateArgs) -> dict[str, Any]:
    c = _candidate(ctx, args.candidate_id)
    rt = ctx.runtime
    last = rt.last
    grid = rt.grid
    mask = grid.cells_within(c.lat, c.lon, max(c.radius_m, grid.cell_m))
    layers = {
        lid: {"mean": _r(last.normalized[i][mask].mean()), "max": _r(last.normalized[i][mask].max())}
        for i, lid in enumerate(last.layer_ids)
    }
    return {
        "candidate_id": c.id,
        "score": {"mean": _r(last.score[mask].mean()), "max": _r(last.score[mask].max())},
        "override_cells": int((last.override[mask] > 0).sum()),
        "layers": layers,
    }


def get_time_pattern(ctx: ToolContext, args: CandidateWindowArgs) -> dict[str, Any]:
    """Event counts near the candidate in equal time buckets (oldest first), per input."""
    c = _candidate(ctx, args.candidate_id)
    now = ctx.now()
    edges = np.linspace(now - args.window_s, now, TIME_BUCKETS + 1)
    out = {}
    for inp_id, ev in _events_near(ctx, c, args.window_s).items():
        counts, _ = np.histogram(ev.t, bins=edges)
        out[inp_id] = {
            "counts": counts.astype(int).tolist(),
            "first_seen_s_ago": _r(now - float(ev.t.min()), 1),
            "last_seen_s_ago": _r(now - float(ev.t.max()), 1),
        }
    return {"candidate_id": c.id, "bucket_s": _r(args.window_s / TIME_BUCKETS, 1), "inputs": out}


# -- writes ----------------------------------------------------------------------


def _check_writable(ctx: ToolContext) -> None:
    if ctx.spec.outputs.poi_labels.mode == "off":
        raise GuardError("POI labeling is off for this app")


def _check_label(ctx: ToolContext, label: str) -> None:
    known = {lb.label for lb in ctx.spec.outputs.poi_labels.label_set}
    if label not in known:
        raise GuardError(
            f"label {label!r} is not in this app's label_set {sorted(known)}; to suggest a new label type, "
            "use annotate so the dev can accept it"
        )


def _check_confidence(ctx: ToolContext, confidence: float) -> None:
    floor = ctx.spec.outputs.poi_labels.min_confidence
    if confidence < floor:
        raise GuardError(f"confidence {confidence:.2f} is below min_confidence {floor:.2f}")


def poi_visibility(ctx: ToolContext, label: str, cand: Candidate | None) -> Visibility:
    """Sensitive -> authority. Destination -> public (the public route already reveals it, and a
    destination label can't be sensitive). Otherwise public only if every contributing layer of the
    candidate is a public output layer; with no candidate to judge, authority."""
    lbl = next(lb for lb in ctx.spec.outputs.poi_labels.label_set if lb.label == label)
    if lbl.sensitive:
        return "authority"
    if lbl.destination:
        return "public"
    if cand is None:
        return "authority"
    public = {o.id for o in ctx.spec.outputs.layers if o.visibility == "public"}
    contributing = cand.contributing_layers
    return "public" if contributing and all(c in public for c in contributing) else "authority"


def _evidence(items: list[EvidenceIn] | None) -> list[Evidence]:
    return [Evidence(kind=e.kind, text=e.text, data=e.data) for e in (items or [])]


def _record(ctx: ToolContext, poi_id: str, before: Any, after: Any, reason: str, reverts: str | None = None) -> Change:
    c = Change(
        id=guards.new_change_id(),
        app_id=ctx.app_id,
        kind="poi",
        target=poi_id,
        before=before,
        after=after,
        reason=reason,
        actor=ctx.actor,
        t=ctx.now_dt(),
        reverts=reverts,
    )
    ctx.db.add_change(c)
    return c


def _poi_state(p: POI) -> dict[str, Any]:
    return {"label": p.label, "confidence": p.confidence, "status": p.status, "visibility": p.visibility}


def create_poi(ctx: ToolContext, args: CreatePoiArgs) -> dict[str, Any]:
    _check_writable(ctx)
    cand = _candidate(ctx, args.candidate_id)
    _check_label(ctx, args.label)
    _check_confidence(ctx, args.confidence)
    if any(p.candidate_id == cand.id for p in _active_pois(ctx)):
        raise GuardError(f"candidate {cand.id} already has an active POI; use update_poi to relabel it")
    now = ctx.now_dt()
    poi = POI(
        id=f"poi-{guards.new_change_id()[:12]}",
        candidate_id=cand.id,
        lat=cand.lat,
        lon=cand.lon,
        radius_m=cand.radius_m,
        label=args.label,
        confidence=args.confidence,
        evidence=_evidence(args.evidence),
        visibility=poi_visibility(ctx, args.label, cand),
        status="active",
        created_at=now,
        updated_at=now,
    )
    ctx.db.upsert_poi(ctx.app_id, poi, ctx.now())
    ch = _record(ctx, poi.id, None, _poi_state(poi), f"labeled candidate {cand.id} as {args.label}")
    return {"poi_id": poi.id, "label": poi.label, "visibility": poi.visibility, "change_id": ch.id}


def _poi(ctx: ToolContext, poi_id: str) -> POI:
    p = ctx.db.get_poi(ctx.app_id, poi_id)
    if p is None:
        raise GuardError(f"unknown POI {poi_id!r}")
    return p


def update_poi(
    ctx: ToolContext, args: UpdatePoiArgs, *, reverts: str | None = None, reason: str = "updated POI"
) -> dict[str, Any]:
    _check_writable(ctx)
    p = _poi(ctx, args.poi_id)
    if p.status != "active":
        raise GuardError(f"POI {p.id} is retired")
    label = args.label if args.label is not None else p.label
    _check_label(ctx, label)
    conf = args.confidence if args.confidence is not None else p.confidence
    _check_confidence(ctx, conf)
    cand = _candidates(ctx).get(p.candidate_id)
    update: dict[str, Any] = {"label": label, "confidence": conf, "updated_at": ctx.now_dt()}
    if args.evidence is not None:
        update["evidence"] = _evidence(args.evidence)
    if cand is not None:  # follow the candidate; positions only ever come from candidates
        update.update(lat=cand.lat, lon=cand.lon, radius_m=cand.radius_m, visibility=poi_visibility(ctx, label, cand))
    elif label != p.label:  # candidate gone: judge by the label alone (authority unless destination)
        update["visibility"] = poi_visibility(ctx, label, None)
    new = p.model_copy(update=update)
    ctx.db.upsert_poi(ctx.app_id, new, ctx.now())
    ch = _record(ctx, p.id, _poi_state(p), _poi_state(new), reason, reverts)
    return {"poi_id": p.id, "label": new.label, "visibility": new.visibility, "change_id": ch.id}


def retire_poi(ctx: ToolContext, args: RetirePoiArgs, *, reverts: str | None = None) -> dict[str, Any]:
    _check_writable(ctx)
    reason = guards.require_reason(args.reason)
    p = _poi(ctx, args.poi_id)
    if p.status == "retired":
        raise GuardError(f"POI {p.id} is already retired")
    new = p.model_copy(update={"status": "retired", "updated_at": ctx.now_dt()})
    ctx.db.upsert_poi(ctx.app_id, new, ctx.now())
    ch = _record(ctx, p.id, _poi_state(p), _poi_state(new), reason, reverts)
    return {"poi_id": p.id, "status": "retired", "change_id": ch.id}


def reactivate_poi(
    ctx: ToolContext, poi_id: str, label: str, confidence: float, *, reason: str, reverts: str | None = None
) -> dict[str, Any]:
    """Dev only (reverting a retire): bring a retired POI back, under the same rules as create_poi.
    Its candidate must still be a current code-made candidate (rule 7: positions only from
    candidates) with no other active POI; position and visibility are re-taken from it."""
    guards.require_dev(ctx, "reactivating a POI")
    reason = guards.require_reason(reason)
    _check_writable(ctx)
    p = _poi(ctx, poi_id)
    if p.status == "active":
        raise GuardError(f"POI {p.id} is already active")
    cand = _candidates(ctx).get(p.candidate_id)
    if cand is None:
        raise GuardError(
            f"POI {p.id}'s candidate {p.candidate_id!r} is no longer a current candidate; "
            "POIs come only from current code-made candidates"
        )
    if any(o.candidate_id == cand.id for o in _active_pois(ctx)):
        raise GuardError(f"candidate {cand.id} already has an active POI")
    _check_label(ctx, label)
    _check_confidence(ctx, confidence)
    new = p.model_copy(
        update={
            "status": "active",
            "label": label,
            "confidence": confidence,
            "lat": cand.lat,
            "lon": cand.lon,
            "radius_m": cand.radius_m,
            "visibility": poi_visibility(ctx, label, cand),
            "updated_at": ctx.now_dt(),
        }
    )
    ctx.db.upsert_poi(ctx.app_id, new, ctx.now())
    ch = _record(ctx, p.id, _poi_state(p), _poi_state(new), reason, reverts)
    return {"poi_id": p.id, "status": "active", "change_id": ch.id}
