"""event_clusters: candidates from located documents (the event_points operator's `docs` extras).

Documents within `CLUSTER_CELLS` cells of a cluster's weighted centre join it, heaviest first. A
single document is already a candidate: one located report can be worth naming, and the POI
Labeler decides. Radius is the furthest member from the centre, at least half a cell.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

from app.engine.operators.base import GridLike

from .base import CandidateDraft

CLUSTER_CELLS = 3.0  # join distance, in cells (scales with cell_m)


def event_clusters(layer_id: str, extras: Mapping[str, Any], grid: GridLike) -> list[CandidateDraft]:
    docs = [d for d in (extras.get("docs") or []) if float(d.get("w", 0.0)) > 0]
    if not docs:
        return []
    docs.sort(key=lambda d: -float(d["w"]))
    lat = np.array([float(d["lat"]) for d in docs])
    lon = np.array([float(d["lon"]) for d in docs])
    w = np.array([float(d["w"]) for d in docs])
    x, y = grid.to_xy(lat, lon)
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    join = CLUSTER_CELLS * grid.cell_m

    clusters: list[list[int]] = []
    centres: list[tuple[float, float]] = []
    for i in range(len(docs)):
        best = None
        for k, (cx, cy) in enumerate(centres):
            if np.hypot(x[i] - cx, y[i] - cy) <= join:
                best = k
                break
        if best is None:
            clusters.append([i])
            centres.append((x[i], y[i]))
        else:
            clusters[best].append(i)
            m = clusters[best]
            centres[best] = (float(np.average(x[m], weights=w[m])), float(np.average(y[m], weights=w[m])))

    out = []
    for m, (cx, cy) in zip(clusters, centres, strict=True):
        clat, clon = grid.to_latlon(np.array([cx]), np.array([cy]))
        radius = float(np.max(np.hypot(x[m] - cx, y[m] - cy)))
        out.append(
            CandidateDraft(
                lat=float(np.asarray(clat)[0]),
                lon=float(np.asarray(clon)[0]),
                radius_m=max(radius, grid.cell_m / 2),
                source="event_clusters",
                stats={"n_docs": float(len(m)), "weight": float(w[m].sum())},
                contributing_layers=[layer_id],
            )
        )
    return out
