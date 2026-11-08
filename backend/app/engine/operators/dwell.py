"""dwell: stop detection on tracks -> stationary clusters of entities.

Stay-point detection per user: a stay is a run of pings that each lie within `radius_m` of
the centroid of the run so far (robust to position jitter) and that spans at least `min_dwell_s`.
Each stay deposits the user's trust at its centroid, decayed by
0.5^(time since the stay's last ping / half_life), then Gaussian-smoothed (peak 1).

extras["clusters"]: stays grouped by single linkage within `cluster_m`, each with centroid,
users, mean dwell and total weight. engine/candidates/dwell_clusters.py turns these into
Candidates.
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


class DwellParams(OperatorParams):
    noise_floor: float = Field(default=3.0, gt=0)
    radius_m: float = Field(default=25.0, gt=0)  # R; leave room for position noise
    min_dwell_s: float = Field(default=120.0, gt=0)  # T
    window_s: float = Field(default=900.0, gt=0)
    half_life_s: float = Field(default=300.0, gt=0)
    sigma_m: float = Field(default=20.0, ge=0)
    cluster_m: float = Field(default=30.0, ge=0)


def stays(t: np.ndarray, x: np.ndarray, y: np.ndarray, radius_m: float, min_dwell_s: float) -> list[tuple[int, int]]:
    """(start, end) index pairs (inclusive) of stays in one user's time-sorted pings."""
    out = []
    i, n = 0, len(t)
    while i < n:
        j, sx, sy = i + 1, x[i], y[i]
        while j < n and np.hypot(x[j] - sx / (j - i), y[j] - sy / (j - i)) <= radius_m:
            sx, sy = sx + x[j], sy + y[j]
            j += 1
        if t[j - 1] - t[i] >= min_dwell_s:
            out.append((i, j - 1))
            i = j
        else:
            i += 1
    return out


def _single_linkage(x: np.ndarray, y: np.ndarray, d: float) -> np.ndarray:
    """Cluster label per point: connected components of the 'within d meters' graph."""
    n = len(x)
    parent = np.arange(n)

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    close = np.hypot(x[:, None] - x[None, :], y[:, None] - y[None, :]) <= d
    for a, b in zip(*np.nonzero(np.triu(close, 1)), strict=True):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    return np.array([find(a) for a in range(n)])


def dwell(
    events: Events,
    params: DwellParams,
    trust: Mapping[str, float] | None,
    clock: ClockLike,
    grid: GridLike,
) -> OperatorResult:
    now = clock.now()
    ev = events.select((events.t <= now) & (events.t > now - params.window_s) & (events.user_id != ""))
    tw = trust_weights(ev.user_id, trust)
    ev, tw = ev.select(tw > 0), tw[tw > 0]
    x, y = grid.to_xy(ev.lat, ev.lon)

    # one row per stay: x, y, weight, dwell_s, user
    sx, sy, sw, sd, su = [], [], [], [], []
    for u in np.unique(ev.user_id):
        idx = np.nonzero(ev.user_id == u)[0]
        idx = idx[np.argsort(ev.t[idx], kind="stable")]
        for a, b in stays(ev.t[idx], x[idx], y[idx], params.radius_m, params.min_dwell_s):
            run = idx[a : b + 1]
            ended = ev.t[run[-1]]  # an ongoing stay's last ping is recent, so it barely decays
            sx.append(x[run].mean())
            sy.append(y[run].mean())
            sw.append(tw[run[0]] * 0.5 ** ((now - ended) / params.half_life_s))
            sd.append(ev.t[run[-1]] - ev.t[run[0]])
            su.append(u)

    sx_a, sy_a, sw_a = np.array(sx), np.array(sy), np.array(sw)
    row, col, inside = cell_index(grid, sx_a, sy_a)
    counts = np.zeros(grid.shape)
    np.add.at(counts, (row[inside], col[inside]), sw_a[inside])
    layer = gaussian_smooth(counts, params.sigma_m / grid.cell_m)

    clusters = []
    if len(sx):
        labels = _single_linkage(sx_a, sy_a, params.cluster_m)
        for lab in np.unique(labels):
            m = labels == lab
            wsum = float(sw_a[m].sum())
            cx, cy = (
                (np.average(sx_a[m], weights=sw_a[m]), np.average(sy_a[m], weights=sw_a[m]))
                if wsum > 0
                else (
                    sx_a[m].mean(),
                    sy_a[m].mean(),
                )
            )
            lat, lon = grid.to_latlon(np.array([cx]), np.array([cy]))
            spread = float(np.sqrt(np.mean((sx_a[m] - cx) ** 2 + (sy_a[m] - cy) ** 2)))
            clusters.append(
                {
                    "lat": float(lat[0]),
                    "lon": float(lon[0]),
                    "radius_m": max(spread, params.radius_m),
                    "n_users": len({su[k] for k in np.nonzero(m)[0]}),
                    "mean_dwell_s": float(np.mean(np.array(sd)[m])),
                    "weight": wsum,
                }
            )
        clusters.sort(key=lambda c: -c["weight"])
    return OperatorResult(layer=layer, extras={"n_stays": len(sx), "clusters": clusters})
