"""persistent_hotspots: candidates from the tick's hotspots (connected high-score areas).

Works for any app, whatever its inputs: point reports, documents, gridded data or tracks. The
tracker (registry.CandidateTracker) follows each one across ticks by position, so
`persisted_ticks` says how long a high-score area has held. Contributing layers come from the
hotspot (the layers holding most of its score mass) and can change from tick to tick.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.engine.operators.base import GridLike

from .base import CandidateDraft


def persistent_hotspots(layer_id: str, extras: Mapping[str, Any], grid: GridLike) -> list[CandidateDraft]:
    """`extras["hotspots"]` is the tick's list of `Hotspot` models (`layer_id` is "score")."""
    out = []
    for h in extras.get("hotspots") or []:
        out.append(
            CandidateDraft(
                lat=float(h.lat),
                lon=float(h.lon),
                radius_m=max(float(h.radius_m), grid.cell_m / 2),
                source="persistent_hotspots",
                stats={
                    "peak_score": float(h.score),
                    "mean_score": float(h.mean),
                    "area_m2": float(h.area_m2),
                    "n_cells": float(h.n_cells),
                },
                contributing_layers=list(h.contributing_layers),
            )
        )
    return out
