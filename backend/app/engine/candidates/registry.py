"""Candidate generators: code-made places worth naming, from operator extras. Never labels.

A generator is a pure function `(layer_id, extras, grid) -> list[CandidateDraft]` registered for
what it reads: an operator's extras (`dwell_clusters` reads dwell, `flow_endpoints` reads
flow_source, `event_clusters` reads event_points), or the tick's hotspots (`persistent_hotspots`,
called once per tick with layer_id "score" and `{"hotspots": [...]}`). Together they cover apps with
or without tracks. The POI Labeler may label only these candidates (rule 7).

A hotspot candidate is dropped when a candidate from an operator already covers the same place
(centre within either radius), so one place isn't offered twice.

`CandidateTracker` gives candidates stable ids across ticks: a draft within
`max(MATCH_CELLS · cell_m, previous radius)` of an unused candidate from the same generator (and,
for generators with `track_by_layers`, the same layers) from the previous tick keeps that id and
increments `persisted_ticks`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.core.models import Candidate
from app.engine.operators.base import GridLike

from .base import CandidateDraft, Generator
from .dwell_clusters import dwell_clusters
from .event_clusters import event_clusters
from .flow_endpoints import flow_endpoints
from .persistent_hotspots import persistent_hotspots

MATCH_CELLS = 3.0  # scales with cell_m
HOTSPOTS = "@hotspots"  # reads_op of generators that read the tick's hotspots, not an operator


@dataclass(frozen=True)
class GeneratorInfo:
    name: str
    fn: Generator
    reads_op: str  # operator whose extras it reads, or HOTSPOTS
    description: str
    track_by_layers: bool = True  # False: follow by position only (contributing layers may change)


GENERATORS: dict[str, GeneratorInfo] = {
    g.name: g
    for g in (
        GeneratorInfo(
            "dwell_clusters",
            dwell_clusters,
            "dwell",
            "Stationary clusters of entities (stays grouped by proximity).",
        ),
        GeneratorInfo(
            "flow_endpoints",
            flow_endpoints,
            "flow_source",
            "Source or sink points of movement (where movement lines meet), sized by estimate uncertainty.",
        ),
        GeneratorInfo(
            "event_clusters",
            event_clusters,
            "event_points",
            "Places where located documents cluster (one or more reports about the same spot).",
        ),
        GeneratorInfo(
            "persistent_hotspots",
            persistent_hotspots,
            HOTSPOTS,
            "High-score areas of the combined map, followed across ticks (any inputs).",
            track_by_layers=False,
        ),
    )
}


def generate(
    extras: Mapping[str, Mapping[str, Any]],
    layer_ops: Mapping[str, str],
    grid: GridLike,
    hotspots: Sequence[Any] = (),
) -> tuple[list[CandidateDraft], list[str]]:
    """Run every registered generator over what it reads (operator extras or the tick's hotspots).
    Returns (drafts, errors); one failing generator never stops the others."""
    drafts: list[CandidateDraft] = []
    errors: list[str] = []
    for layer_id, ex in extras.items():
        op = layer_ops.get(layer_id)
        for g in GENERATORS.values():
            if g.reads_op != op:
                continue
            try:
                drafts.extend(g.fn(layer_id, ex, grid))
            except Exception as e:  # noqa: BLE001 - a bad generator must not kill the tick
                errors.append(f"{layer_id}: candidate generator {g.name}: {type(e).__name__}: {e}")
    if hotspots:
        for g in GENERATORS.values():
            if g.reads_op != HOTSPOTS:
                continue
            try:
                made = g.fn("score", {"hotspots": list(hotspots)}, grid)
            except Exception as e:  # noqa: BLE001 - a bad generator must not kill the tick
                errors.append(f"score: candidate generator {g.name}: {type(e).__name__}: {e}")
                continue
            from_operators = [d for d in drafts if _reads(d.source) != HOTSPOTS]
            drafts.extend(d for d in made if not _covered(d, from_operators, grid))
    return drafts, errors


def _reads(source: str) -> str | None:
    info = GENERATORS.get(source)
    return info.reads_op if info is not None else None


def _covered(d: CandidateDraft, others: Sequence[CandidateDraft], grid: GridLike) -> bool:
    """True if another candidate already sits on this one (either centre within the other's radius)."""
    x, y = grid.to_xy(np.array([d.lat]), np.array([d.lon]))
    for o in others:
        ox, oy = grid.to_xy(np.array([o.lat]), np.array([o.lon]))
        if float(np.hypot(float(x[0]) - float(ox[0]), float(y[0]) - float(oy[0]))) <= max(d.radius_m, o.radius_m):
            return True
    return False


def _track_key(source: str, layers: Sequence[str]) -> tuple:
    info = GENERATORS.get(source)
    return (source, tuple(layers)) if info is None or info.track_by_layers else (source,)


class CandidateTracker:
    """Stable ids and persisted_ticks across ticks, per app."""

    def __init__(self) -> None:
        self._prev: list[Candidate] = []
        self._next = 0

    def update(self, drafts: list[CandidateDraft], grid: GridLike) -> list[Candidate]:
        used: set[str] = set()
        out: list[Candidate] = []
        prev_xy = {}
        for c in self._prev:
            px, py = grid.to_xy(np.array([c.lat]), np.array([c.lon]))
            prev_xy[c.id] = (float(px[0]), float(py[0]))
        for d in drafts:
            x, y = (float(v[0]) for v in grid.to_xy(np.array([d.lat]), np.array([d.lon])))
            key = _track_key(d.source, d.contributing_layers)
            best_d, best = np.inf, None
            for c in self._prev:
                if c.id in used or _track_key(c.source, c.contributing_layers) != key:
                    continue
                px, py = prev_xy[c.id]
                dist = float(np.hypot(px - x, py - y))
                if dist <= max(MATCH_CELLS * grid.cell_m, c.radius_m) and dist < best_d:
                    best_d, best = dist, c
            if best is None:
                self._next += 1
                cid, persisted = f"c{self._next}", 0
            else:
                cid, persisted = best.id, best.persisted_ticks + 1
            used.add(cid)
            out.append(
                Candidate(
                    id=cid,
                    lat=d.lat,
                    lon=d.lon,
                    radius_m=d.radius_m,
                    source=d.source,
                    stats=d.stats,
                    contributing_layers=d.contributing_layers,
                    persisted_ticks=persisted,
                )
            )
        self._prev = out
        return out
