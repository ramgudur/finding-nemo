"""density: smoothed point-event density with half-life decay, trust weights and duplicate removal.

Each kept event deposits trust · 0.5^(age / half_life) at its cell; the grid is then
Gaussian-smoothed with a peak-1 kernel, so one fresh trusted event reads ~1.0.

Duplicate removal: per user, an event within `dedupe_m` meters and `dedupe_s` seconds of
that user's last kept event is dropped (repeat taps, retried POSTs). Events with no user are
never deduped. Cross-account duplicates are a spoof signal for engine/trust.py, not removed here.
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


class DensityParams(OperatorParams):
    noise_floor: float = Field(default=3.0, gt=0)
    half_life_s: float = Field(default=120.0, gt=0)
    sigma_m: float = Field(default=25.0, ge=0)
    dedupe_m: float = Field(default=5.0, ge=0)
    dedupe_s: float = Field(default=10.0, ge=0)
    max_age_half_lives: float = Field(default=8.0, gt=0)  # older events contribute < 0.4%: skip them


def dedupe_mask(
    uid: np.ndarray, t: np.ndarray, x: np.ndarray, y: np.ndarray, dedupe_m: float, dedupe_s: float
) -> np.ndarray:
    """True for events to keep."""
    keep = np.ones(len(t), dtype=bool)
    if dedupe_m <= 0 or dedupe_s <= 0:
        return keep
    _, uidx = np.unique(uid, return_inverse=True)
    order = np.lexsort((t, uidx))
    last: dict[str, int] = {}
    for i in order:
        u = uid[i]
        if not u:
            continue
        j = last.get(u)
        if j is not None and t[i] - t[j] <= dedupe_s and np.hypot(x[i] - x[j], y[i] - y[j]) <= dedupe_m:
            keep[i] = False
        else:
            last[u] = i
    return keep


def density(
    events: Events,
    params: DensityParams,
    trust: Mapping[str, float] | None,
    clock: ClockLike,
    grid: GridLike,
) -> OperatorResult:
    now = clock.now()
    horizon = params.half_life_s * params.max_age_half_lives
    ev = events.select((events.t <= now) & (events.t > now - horizon))
    x, y = grid.to_xy(ev.lat, ev.lon)

    keep = dedupe_mask(ev.user_id, ev.t, x, y, params.dedupe_m, params.dedupe_s)
    w = trust_weights(ev.user_id, trust) * 0.5 ** ((now - ev.t) / params.half_life_s)
    row, col, inside = cell_index(grid, x, y)
    m = keep & inside & (w > 0)

    counts = np.zeros(grid.shape)
    np.add.at(counts, (row[m], col[m]), w[m])
    layer = gaussian_smooth(counts, params.sigma_m / grid.cell_m)
    return OperatorResult(
        layer=layer,
        extras={"n_events": int(m.sum()), "n_deduped": int((~keep).sum())},
    )
