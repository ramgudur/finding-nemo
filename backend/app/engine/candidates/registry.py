"""Candidate generators: code-made places worth naming, from operator extras. Never labels.

A generator is a pure function `(layer_id, extras, grid) -> list[CandidateDraft]` registered for
the operator whose extras it reads. Only `dwell_clusters` (dwell) and `flow_endpoints`
(flow_source) are registered. The POI Labeler may label only these candidates (rule 7).

`CandidateTracker` gives candidates stable ids across ticks: a draft within
`max(MATCH_CELLS · cell_m, previous radius)` of an unused candidate from the same generator and
layer from the previous tick keeps that id and increments `persisted_ticks`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.core.models import Candidate
from app.engine.operators.base import GridLike

from .base import CandidateDraft, Generator
from .dwell_clusters import dwell_clusters
from .flow_endpoints import flow_endpoints

MATCH_CELLS = 3.0  # scales with cell_m


@dataclass(frozen=True)
class GeneratorInfo:
    name: str
    fn: Generator
    reads_op: str  # operator whose extras it reads
    description: str


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
    )
}


def generate(
    extras: Mapping[str, Mapping[str, Any]], layer_ops: Mapping[str, str], grid: GridLike
) -> tuple[list[CandidateDraft], list[str]]:
    """Run every registered generator over the layers whose operator it reads.
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
    return drafts, errors


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
            key = (d.source, tuple(d.contributing_layers))
            best_d, best = np.inf, None
            for c in self._prev:
                if c.id in used or (c.source, tuple(c.contributing_layers)) != key:
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
