"""flow_endpoints: candidates at flow_source's point estimate (a source or a sink of movement).

Radius: the ~95% radius of the estimate, 2.45 · uncertainty_m (uncertainty_m is the 1-sigma
semi-major axis), floored at half a cell. Without an uncertainty, spread_m is used instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.engine.operators.base import GridLike

from .base import CandidateDraft

SIGMA_TO_95 = 2.45  # 2-D Gaussian: 95% of the mass within 2.45 sigma
MIN_TRACKS = 3


def flow_endpoints(layer_id: str, extras: Mapping[str, Any], grid: GridLike) -> list[CandidateDraft]:
    point = extras.get("point")
    if not point or extras.get("n_tracks", 0) < MIN_TRACKS:
        return []
    unc = extras.get("uncertainty_m")
    spread = extras.get("spread_m")
    if unc is not None:
        radius = SIGMA_TO_95 * float(unc)
    elif spread is not None:
        radius = float(spread)
    else:
        radius = 0.0
    kind = extras.get("kind")
    stats = {"n_tracks": float(extras.get("n_tracks", 0)), "is_source": 1.0 if kind == "source" else 0.0}
    if unc is not None:
        stats["uncertainty_m"] = float(unc)
    return [
        CandidateDraft(
            lat=float(point["lat"]),
            lon=float(point["lon"]),
            radius_m=max(radius, grid.cell_m / 2),
            source="flow_endpoints",
            stats=stats,
            contributing_layers=[layer_id],
        )
    ]
