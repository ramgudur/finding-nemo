"""flow_source: divergence / flux operator for moving-GPS tracks.

Ported from reference/flowsource.py (the lead's reference). Two outputs from the same tracks:
  1. layer: net outflow of trusted people per cell over the window (exits minus entries),
     Gaussian-smoothed. `polarity` picks which side becomes heat: "source" (people leaving)
     or "sink" (people converging). The other side is clipped to zero.
  2. extras["point"]: weighted least-squares intersection of back-extended movement lines,
     with Cauchy robust reweighting, plus whether the flow is a source or a sink.

Needs tracks (several pings per user), not single taps.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

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


class FlowSourceParams(OperatorParams):
    noise_floor: float = Field(default=5.0, gt=0)
    window_s: float = Field(default=20.0, gt=0)
    polarity: Literal["source", "sink"] = "source"
    sigma_m: float = Field(default=37.5, ge=0)
    min_move_m: float = Field(default=20.0, ge=0)  # below this a user is stationary: no flow signal
    min_tracks: int = Field(default=3, ge=2)
    max_move_weight_m: float = Field(default=120.0, gt=0)  # caps "faster movers = stronger signal"
    robust_scale_m: float = Field(default=40.0, gt=0)
    iters: int = Field(default=8, ge=1)


def _sorted_tracks(uid: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Order events by (user, time). Returns (order, user index per sorted event)."""
    _, inv = np.unique(uid, return_inverse=True)
    order = np.lexsort((t, inv))
    return order, inv[order]


def divergence(x: np.ndarray, y: np.ndarray, uidx: np.ndarray, w: np.ndarray, grid: GridLike) -> np.ndarray:
    """Trust-weighted (exits - entries) per cell. Inputs are sorted by (user, time).
    Steps that leave the grid count only on the in-grid side."""
    div = np.zeros(grid.shape)
    if len(x) < 2:
        return div
    row, col, inside = cell_index(grid, x, y)
    a, b = np.arange(len(x) - 1), np.arange(1, len(x))
    step = (uidx[a] == uidx[b]) & ((row[a] != row[b]) | (col[a] != col[b]))
    ex = a[step & inside[a]]
    en = b[step & inside[b]]
    np.add.at(div, (row[ex], col[ex]), w[ex])
    np.add.at(div, (row[en], col[en]), -w[en])
    return div


def displacements(
    x: np.ndarray, y: np.ndarray, uidx: np.ndarray, min_move_m: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """First -> last ping per user (a long baseline beats GPS jitter).
    Returns (start_xy [N,2], unit_dir [N,2], dist [N], user index [N]) for users who moved."""
    if len(x) == 0:
        empty = np.zeros((0, 2))
        return empty, empty, np.zeros(0), np.zeros(0, dtype=int)
    users, first = np.unique(uidx, return_index=True)
    last = np.r_[first[1:], len(uidx)] - 1
    keep = last > first
    first, last, users = first[keep], last[keep], users[keep]
    start = np.c_[x[first], y[first]]
    d = np.c_[x[last], y[last]] - start
    dist = np.linalg.norm(d, axis=1)
    moved = dist >= max(min_move_m, 1e-9)
    return start[moved], d[moved] / dist[moved, None], dist[moved], users[moved]


def backproject(
    start: np.ndarray, direction: np.ndarray, w: np.ndarray, params: FlowSourceParams
) -> tuple[np.ndarray, str, float] | None:
    """Weighted least-squares point closest to all back-extended lines, robustly reweighted.
    Returns (xy, kind, spread_m), or None if there are too few tracks or the lines are near-parallel."""
    if len(start) < params.min_tracks or w.sum() <= 0:
        return None
    eye = np.eye(2)
    proj = eye[None] - direction[:, :, None] * direction[:, None, :]  # [N,2,2], orthogonal to each line
    p = start.mean(axis=0)
    r = np.zeros(len(start))
    for _ in range(params.iters):
        a = np.einsum("n,nij->ij", w, proj)
        evals = np.linalg.eigvalsh(a)
        if evals[0] <= 1e-3 * evals[1]:
            return None  # all lines (nearly) parallel: no intersection
        b = np.einsum("n,nij,nj->i", w, proj, start)
        p = np.linalg.solve(a, b)
        r = np.linalg.norm(np.einsum("nij,nj->ni", proj, p - start), axis=1)
        w = w / (1.0 + (r / params.robust_scale_m) ** 2)
    radial = np.einsum("ij,ij->i", direction, start - p)  # > 0: moving away from p
    kind = "source" if np.average(np.sign(radial), weights=w) > 0 else "sink"
    spread = float(np.sqrt(np.average(r**2, weights=w)))
    return p, kind, spread


def flow_source(
    events: Events,
    params: FlowSourceParams,
    trust: Mapping[str, float] | None,
    clock: ClockLike,
    grid: GridLike,
) -> OperatorResult:
    now = clock.now()
    ev = events.select((events.t > now - params.window_s) & (events.t <= now) & (events.user_id != ""))
    tw = trust_weights(ev.user_id, trust)
    ev, tw = ev.select(tw > 0), tw[tw > 0]

    order, uidx = _sorted_tracks(ev.user_id, ev.t)
    x, y = grid.to_xy(ev.lat[order], ev.lon[order])
    w = tw[order]

    div = divergence(x, y, uidx, w, grid)
    sign = 1.0 if params.polarity == "source" else -1.0
    layer = np.maximum(sign * gaussian_smooth(div, params.sigma_m / grid.cell_m), 0.0)

    start, direction, dist, users = displacements(x, y, uidx, params.min_move_m)
    first_of_user = np.unique(uidx, return_index=True)[1]
    user_trust = w[first_of_user][users] if len(users) else np.zeros(0)
    line_w = np.minimum(dist, params.max_move_weight_m) * user_trust

    extras: dict = {"n_tracks": len(start), "point": None, "kind": None, "spread_m": None}
    est = backproject(start, direction, line_w, params)
    if est is not None:
        p, kind, spread = est
        lat, lon = grid.to_latlon(np.array([p[0]]), np.array([p[1]]))
        extras.update(point={"lat": float(lat[0]), "lon": float(lon[0])}, kind=kind, spread_m=spread)
    return OperatorResult(layer=layer, extras=extras)
