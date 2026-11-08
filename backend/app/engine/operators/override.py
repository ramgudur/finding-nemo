"""override: authority fixed points (room buttons, police markers).

Each event marks a point; every cell whose center is within `radius_m` of an active point
gets 1.0. A point stays active until an event with `clear_field` true arrives within
`radius_m` of it (or its `ttl_s` runs out, if set). The tick feeds this layer into combine's
override mask, which is applied after the weighted sum: max(score, 1.0) inside the radius.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
from pydantic import Field

from .base import ClockLike, Events, GridLike, OperatorParams, OperatorResult, trust_weights


class OverrideParams(OperatorParams):
    noise_floor: float = Field(default=1.0, gt=0)  # the layer is a 0/1 mask
    radius_m: float = Field(default=50.0, gt=0)
    clear_field: str = "cleared"
    ttl_s: float | None = Field(default=None, gt=0)  # None: active until cleared


def override(
    events: Events,
    params: OverrideParams,
    trust: Mapping[str, float] | None,
    clock: ClockLike,
    grid: GridLike,
) -> OperatorResult:
    now = clock.now()
    ev = events.select(events.t <= now)
    ev = ev.select(trust_weights(ev.user_id, trust) > 0)
    if params.ttl_s is not None:
        ev = ev.select(ev.t > now - params.ttl_s)
    x, y = grid.to_xy(ev.lat, ev.lon)
    clear = ev.column(params.clear_field, 0.0) != 0

    active: list[tuple[float, float, float]] = []  # (x, y, t)
    for i in np.argsort(ev.t, kind="stable"):
        if clear[i]:
            active = [p for p in active if np.hypot(p[0] - x[i], p[1] - y[i]) > params.radius_m]
        else:
            active.append((float(x[i]), float(y[i]), float(ev.t[i])))

    h, w = grid.shape
    cx = (np.arange(w) + 0.5) * grid.cell_m
    cy = (np.arange(h) + 0.5) * grid.cell_m
    layer = np.zeros((h, w))
    for px, py, _ in active:
        layer[(cy[:, None] - py) ** 2 + (cx[None, :] - px) ** 2 <= params.radius_m**2] = 1.0
        # a radius smaller than a cell still covers the cell the point is in
        row, col = int(py // grid.cell_m), int(px // grid.cell_m)
        if 0 <= row < h and 0 <= col < w:
            layer[row, col] = 1.0

    points = []
    if active:
        lat, lon = grid.to_latlon(np.array([p[0] for p in active]), np.array([p[1] for p in active]))
        points = [
            {"lat": float(a), "lon": float(o), "t": p[2], "radius_m": params.radius_m}
            for a, o, p in zip(lat, lon, active, strict=True)
        ]
    return OperatorResult(layer=layer, extras={"override": True, "active": points})
