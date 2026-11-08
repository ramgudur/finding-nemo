"""flow_source: divergence / flux operator for moving tracks.

Ported from reference/flowsource.py (the lead's reference). Two outputs from the same tracks:
  1. layer: net outflow of trusted entities per cell over the window (exits minus entries),
     Gaussian-smoothed. `polarity` picks which side becomes heat: "source" (entities leaving)
     or "sink" (entities converging). The other side is clipped to zero.
  2. extras["point"]: weighted least-squares intersection of back-extended movement lines,
     with Cauchy robust reweighting, plus whether the flow is a source or a sink.

Physics model for the point estimate: each track is fitted with constant velocity over the window
(least-squares x(t), y(t)), so the line's anchor and heading use every ping instead of only the first
and last. That averages position noise and resists tracks whose end points are off. `uncertainty_m`
is the 1-sigma semi-major axis of the estimate's covariance (residual variance x inverse normal matrix).
Benchmark: tests/bench_flow_source.py.

Needs tracks (several pings per entity), not single taps.
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


def track_fits(
    x: np.ndarray, y: np.ndarray, t: np.ndarray, uidx: np.ndarray, min_move_m: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Constant-velocity least-squares fit per track (inputs sorted by (user, time)).
    Returns (start_xy [N,2] = fitted position at the track's first time, unit_dir [N,2],
    dist [N] = fitted distance covered, user index [N]) for tracks that moved at least min_move_m."""
    empty = np.zeros((0, 2))
    if len(x) == 0:
        return empty, empty, np.zeros(0), np.zeros(0, dtype=int)
    users, first, counts = np.unique(uidx, return_index=True, return_counts=True)
    k = np.searchsorted(users, uidx)
    tau = t - t[first][k]
    n = counts.astype(float)

    def s(v: np.ndarray) -> np.ndarray:
        return np.bincount(k, weights=v, minlength=len(users))

    st, stt = s(tau), s(tau * tau)
    sx, sy, stx, sty = s(x), s(y), s(tau * x), s(tau * y)
    denom = n * stt - st * st
    span = np.zeros(len(users))
    np.maximum.at(span, k, tau)
    ok = (counts >= 2) & (denom > 1e-9) & (span > 0)
    safe = np.where(ok, denom, 1.0)
    vx, vy = (n * stx - st * sx) / safe, (n * sty - st * sy) / safe
    x0, y0 = (sx - vx * st) / n, (sy - vy * st) / n
    speed = np.hypot(vx, vy)
    dist = speed * span
    keep = ok & (dist >= max(min_move_m, 1e-9))
    start = np.c_[x0, y0][keep]
    direction = np.c_[vx, vy][keep] / speed[keep, None]
    return start, direction, dist[keep], users[keep]


def backproject(
    start: np.ndarray, direction: np.ndarray, w: np.ndarray, params: FlowSourceParams
) -> tuple[np.ndarray, str, float, float] | None:
    """Weighted least-squares point closest to all back-extended lines, robustly reweighted.
    Returns (xy, kind, spread_m, uncertainty_m), or None if there are too few tracks or the lines
    are near-parallel."""
    if len(start) < params.min_tracks or w.sum() <= 0:
        return None
    eye = np.eye(2)
    proj = eye[None] - direction[:, :, None] * direction[:, None, :]  # [N,2,2], orthogonal to each line
    w0 = w
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
        w = w0 / (1.0 + (r / params.robust_scale_m) ** 2)
    radial = np.einsum("ij,ij->i", direction, start - p)  # > 0: moving away from p
    kind = "source" if np.average(np.sign(radial), weights=w) > 0 else "sink"
    spread = float(np.sqrt(np.average(r**2, weights=w)))
    # covariance of p: residual variance x inverse normal matrix, with weights scaled to sum to the
    # effective number of tracks so the estimate tightens as independent tracks agree
    n_eff = float(w.sum() ** 2 / np.sum(w**2))
    wn = w * (n_eff / w.sum())
    a = np.einsum("n,nij->ij", wn, proj)
    sigma2 = float(np.sum(wn * r**2) / max(n_eff - 2.0, 1.0))
    uncertainty = float(np.sqrt(sigma2 * np.linalg.eigvalsh(np.linalg.inv(a))[-1]))
    return p, kind, spread, uncertainty


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

    start, direction, dist, users = track_fits(x, y, ev.t[order], uidx, params.min_move_m)
    first_of_user = np.unique(uidx, return_index=True)[1]
    user_trust = w[first_of_user][users] if len(users) else np.zeros(0)
    line_w = np.minimum(dist, params.max_move_weight_m) * user_trust

    extras: dict = {"n_tracks": len(start), "point": None, "kind": None, "spread_m": None, "uncertainty_m": None}
    est = backproject(start, direction, line_w, params)
    if est is not None:
        p, kind, spread, uncertainty = est
        lat, lon = grid.to_latlon(np.array([p[0]]), np.array([p[1]]))
        extras.update(
            point={"lat": float(lat[0]), "lon": float(lon[0])},
            kind=kind,
            spread_m=spread,
            uncertainty_m=uncertainty,
        )
    return OperatorResult(layer=layer, extras=extras)
