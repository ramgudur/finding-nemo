"""dwell_clusters: candidates from the dwell operator's stationary clusters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.engine.operators.base import GridLike

from .base import CandidateDraft

MIN_ENTITIES = 2  # one stationary entity is not a cluster


def dwell_clusters(layer_id: str, extras: Mapping[str, Any], grid: GridLike) -> list[CandidateDraft]:
    out = []
    for c in extras.get("clusters") or []:
        if c.get("n_users", 0) < MIN_ENTITIES:
            continue
        out.append(
            CandidateDraft(
                lat=float(c["lat"]),
                lon=float(c["lon"]),
                radius_m=max(float(c.get("radius_m", 0.0)), grid.cell_m / 2),
                source="dwell_clusters",
                stats={
                    "n_entities": float(c.get("n_users", 0)),
                    "mean_dwell_s": float(c.get("mean_dwell_s", 0.0)),
                    "weight": float(c.get("weight", 0.0)),
                },
                contributing_layers=[layer_id],
            )
        )
    return out
