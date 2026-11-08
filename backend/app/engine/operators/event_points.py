"""event_points: geocoded documents.

Each document deposits confidence × source reliability × 0.5^(age / half_life) at its
location, then Gaussian-smoothed (peak 1). Source reliability is the trust value of the
event's `user_id`, which for documents is the source id (Scout's `set_source_reliability`).
The same document re-reported by one source (same `doc_id_field` value) counts once, latest copy.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
from pydantic import Field

from .base import (
    ClockLike,
    Events,
    GridLike,
    OperatorParams,
    OperatorResult,
    cell_index,
    gaussian_smooth,
    trust_weights,
)

MAX_DOC_EXTRAS = 200  # located documents passed on to candidate generation


class EventPointsParams(OperatorParams):
    noise_floor: float = Field(default=2.0, gt=0)
    half_life_s: float = Field(default=1800.0, gt=0)
    sigma_m: float = Field(default=75.0, ge=0)  # geocodes are coarse
    confidence_field: str = "confidence"
    doc_id_field: str = "doc_id"
    max_age_half_lives: float = Field(default=8.0, gt=0)


def _latest_per_doc(ev: Events, doc_id_field: str) -> np.ndarray:
    """Keep mask: for repeated (source, doc_id), only the latest event. Events without a doc id are kept."""
    keep = np.ones(len(ev), dtype=bool)
    docs = ev.fields.get(doc_id_field)
    if docs is None:
        return keep
    seen: set[tuple[str, str]] = set()
    for i in np.argsort(-ev.t, kind="stable"):
        if docs[i] is None or docs[i] == "":
            continue
        key = (str(ev.user_id[i]), str(docs[i]))
        if key in seen:
            keep[i] = False
        seen.add(key)
    return keep


def event_points(
    events: Events,
    params: EventPointsParams,
    trust: Mapping[str, float] | None,
    clock: ClockLike,
    grid: GridLike,
) -> OperatorResult:
    now = clock.now()
    ev = events.select((events.t <= now) & (events.t > now - params.half_life_s * params.max_age_half_lives))
    ev = ev.select(_latest_per_doc(ev, params.doc_id_field))

    conf = np.clip(ev.column(params.confidence_field, 1.0), 0.0, 1.0)
    w = conf * trust_weights(ev.user_id, trust) * 0.5 ** ((now - ev.t) / params.half_life_s)
    x, y = grid.to_xy(ev.lat, ev.lon)
    row, col, inside = cell_index(grid, x, y)
    m = inside & (w > 0)

    counts = np.zeros(grid.shape)
    np.add.at(counts, (row[m], col[m]), w[m])
    layer = gaussian_smooth(counts, params.sigma_m / grid.cell_m)
    # located documents for the event_clusters candidate generator (heaviest first, capped)
    idx = np.flatnonzero(m)
    idx = idx[np.argsort(-w[idx], kind="stable")][:MAX_DOC_EXTRAS]
    docs = [{"lat": float(ev.lat[i]), "lon": float(ev.lon[i]), "w": float(w[i])} for i in idx]
    return OperatorResult(layer=layer, extras={"n_docs": int(m.sum()), "docs": docs})
