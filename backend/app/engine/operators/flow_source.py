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
`estimator="consensus"` swaps the Cauchy fit for a consensus estimator (see consensus_intersection).
Benchmark of both: tests/bench_flow_source.py.

A moving source: the estimate shows where the source was when entities left, so it trails a moving
source by about its speed x the information's age. `source_speed_mps` (set by the spec; 0 = static)
widens `uncertainty_m` by that drift so downstream radii stay honest; the point itself is unchanged.

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
    robust_scale_m: float = Field(default=40.0, gt=0)  # "wls" only
    iters: int = Field(default=8, ge=1)
    # "wls": Cauchy-reweighted least squares from the weighted mean (default).
    # "consensus": consensus start + heading-spread noise model + Tukey refinement. Better against
    # noisy coordinated fake tracks and with many tracks, but with a heavier tail when uniform fake
    # flight reaches ~25% of tracks (tests/bench_flow_source.py), so it is opt-in.
    estimator: Literal["wls", "consensus"] = "wls"
    consensus_tracks: int = Field(default=60, ge=3)  # longest tracks whose pairwise crossings seed the start
    opposite_weight: float = Field(default=0.1, ge=0, le=1)  # weight of tracks moving against the kind
    # How fast the source itself may move (m/s; the spec sets it, 0 = static). Headings only show where
    # the source was when entities left, so the estimate is stale by the information's age; with a
    # moving source uncertainty_m grows by source_speed_mps x age.
    source_speed_mps: float = Field(default=0.0, ge=0)


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


# -- consensus estimator ------------------------------------------------------------------

TUKEY_C = 4.685  # biweight cutoff (95% efficiency under Gaussian noise)
CAUCHY_C = 2.385
HEADING_Q = 0.5  # quantile of angular residuals used for the heading spread
MAX_HEADING_RAD = np.radians(45.0)


def _half_normal_quantile(q: float) -> float:
    """z with P(|N(0,1)| < z) = q (bisection; no scipy)."""
    from math import erf, sqrt

    lo, hi = 0.0, 10.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if erf(mid / sqrt(2)) < q else (lo, mid)
    return (lo + hi) / 2


def track_stats(
    x: np.ndarray, y: np.ndarray, t: np.ndarray, uidx: np.ndarray, users: np.ndarray, direction: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """Per kept track: (centroid [N,2], pings n [N], along-track spread S [N] in m^2) and the pooled
    perpendicular position-noise variance (m^2) of the constant-velocity fits."""
    centroid = np.zeros((len(users), 2))
    n = np.zeros(len(users))
    spread = np.zeros(len(users))
    perp_ss, dof = 0.0, 0
    per_track = []
    for i, u in enumerate(users):
        m = uidx == u
        px, py, pt = x[m], y[m], t[m]
        c = np.array([px.mean(), py.mean()])
        d = direction[i]
        along = (px - c[0]) * d[0] + (py - c[1]) * d[1]
        perp = -(px - c[0]) * d[1] + (py - c[1]) * d[0]
        centroid[i], n[i], spread[i] = c, len(px), float(np.sum((along - along.mean()) ** 2))
        if len(px) > 2:
            per_track.append(float(np.sum(perp**2) / (len(px) - 2)))
            perp_ss += float(np.sum(perp**2))
            dof += len(px) - 2
        del pt
    sig2 = float(np.median(per_track)) if per_track else 25.0
    return centroid, n, spread, max(sig2, 1.0)


def _consensus_start(c, d, base, sig2, n, spread, m, kappa0=0.35):
    """Best crossing of the longest tracks' lines by robust agreement, sources and sinks scored apart."""
    idx = np.argsort(-base)[:m]
    cand = [np.average(c, axis=0, weights=base)]
    for a in range(len(idx)):
        for b in range(a + 1, len(idx)):
            i, j = idx[a], idx[b]
            mat = np.c_[d[i], -d[j]]
            if abs(np.linalg.det(mat)) < 0.17:  # lines within ~10 degrees: unstable crossing
                continue
            s_ = np.linalg.solve(mat, c[j] - c[i])
            cand.append(c[i] + s_[0] * d[i])
    cand = np.array(cand)
    diff = cand[:, None, :] - c[None, :, :]
    along = np.einsum("knj,nj->kn", diff, d)
    r = np.linalg.norm(diff - along[..., None] * d[None], axis=2)
    sd = np.sqrt(sig2 / n + along**2 * (sig2 / np.maximum(spread, 1e-6) + kappa0**2))
    score = base / (1 + (r / sd / CAUCHY_C) ** 2)
    radial = -along  # > 0: the track moves away from the candidate
    best = np.maximum((score * (radial > 0)).sum(1), (score * (radial < 0)).sum(1))
    return cand[int(np.argmax(best))]


def consensus_intersection(
    centroid: np.ndarray,
    direction: np.ndarray,
    base_w: np.ndarray,
    n: np.ndarray,
    spread: np.ndarray,
    sig2: float,
    params: FlowSourceParams,
) -> tuple[np.ndarray, str, float, float] | None:
    """Fake-resistant point where movement lines meet. Returns (xy, kind, spread_m, uncertainty_m).

    Noise model per line: var = sig2/n + along^2 * (sig2/S + kappa^2), where along is the distance
    back from the track to the foot point and kappa is the heading spread of entities around the
    radial direction (estimated once, from tracks consistent with the consensus start, then held
    fixed so a large group of decoy tracks can't inflate it). Refinement uses Tukey's biweight (zero
    weight past the cutoff) and down-weights tracks moving against the detected kind.
    uncertainty_m: 1-sigma semi-major axis of the sandwich covariance."""
    if len(centroid) < params.min_tracks or base_w.sum() <= 0:
        return None
    eye = np.eye(2)
    proj = eye[None] - direction[:, :, None] * direction[:, None, :]
    a0 = np.einsum("n,nij->ij", base_w, proj)
    ev = np.linalg.eigvalsh(a0)
    if ev[0] <= 1e-3 * ev[1]:
        return None  # all lines (nearly) parallel: no intersection
    p = _consensus_start(centroid, direction, base_w, sig2, n, spread, params.consensus_tracks)
    along = np.einsum("ij,ij->i", direction, p - centroid)
    r = np.linalg.norm(np.einsum("nij,nj->ni", proj, p - centroid), axis=1)
    kind_sign = 1.0 if np.average(np.sign(-along), weights=base_w) > 0 else -1.0
    w_dir = np.where(np.sign(-along) == kind_sign, 1.0, params.opposite_weight)
    ang = np.arctan2(r, np.maximum(np.abs(along), 1e-6))
    excess = np.sqrt(np.maximum(ang**2 - sig2 / np.maximum(spread, 1e-6), 0.0))
    ok = (ang < MAX_HEADING_RAD) & (w_dir == 1.0)
    kappa = float(np.quantile(excess[ok], HEADING_Q) / _half_normal_quantile(HEADING_Q)) if ok.sum() >= 3 else 0.35
    kappa = float(np.clip(kappa, np.radians(3.0), MAX_HEADING_RAD))
    w = base_w * w_dir
    for _ in range(max(params.iters, 12)):
        along = np.einsum("ij,ij->i", direction, p - centroid)
        r = np.linalg.norm(np.einsum("nij,nj->ni", proj, p - centroid), axis=1)
        z = r / np.sqrt(sig2 / n + along**2 * (sig2 / np.maximum(spread, 1e-6) + kappa**2))
        w_new = base_w * w_dir * np.where(z < TUKEY_C, (1 - (z / TUKEY_C) ** 2) ** 2, 0.0)
        if w_new.sum() <= 0:
            break
        w = w_new
        a = np.einsum("n,nij->ij", w, proj)
        if np.linalg.eigvalsh(a)[0] <= 1e-9 * np.trace(a):
            break
        p = np.linalg.solve(a, np.einsum("n,nij,nj->i", w, proj, centroid))
    r = np.linalg.norm(np.einsum("nij,nj->ni", proj, p - centroid), axis=1)
    radial = np.einsum("ij,ij->i", direction, centroid - p)
    kind = "source" if np.average(np.sign(radial), weights=w) > 0 else "sink"
    spread_m = float(np.sqrt(np.average(r**2, weights=w)))
    g = np.einsum("nij,nj->ni", proj, p - centroid)
    a = np.einsum("n,nij->ij", w, proj)
    b = np.einsum("n,ni,nj->ij", w**2, g, g)
    ai = np.linalg.inv(a)
    n_eff = float(w.sum() ** 2 / np.sum(w**2))
    cov = ai @ b @ ai * n_eff / max(n_eff - 2.0, 1.0)
    uncertainty = float(np.sqrt(max(np.linalg.eigvalsh(cov)[-1], 0.0)))
    return p, kind, spread_m, uncertainty


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
    if params.estimator == "consensus" and len(start):
        cent, n_pings, spread, sig2 = track_stats(x, y, ev.t[order], uidx, users, direction)
        est = consensus_intersection(cent, direction, line_w, n_pings, spread, sig2, params)
    else:
        est = backproject(start, direction, line_w, params)
    if est is not None:
        p, kind, spread, uncertainty = est
        if params.source_speed_mps > 0 and len(users):
            # information age: since the median first ping of the tracks used (a lower bound on when
            # their headings were set)
            first_t = ev.t[order][np.unique(uidx, return_index=True)[1]][users]
            age_s = max(now - float(np.median(first_t)), 0.0)
            uncertainty = float(np.hypot(uncertainty, params.source_speed_mps * age_s))
        lat, lon = grid.to_latlon(np.array([p[0]]), np.array([p[1]]))
        extras.update(
            point={"lat": float(lat[0]), "lon": float(lon[0])},
            kind=kind,
            spread_m=spread,
            uncertainty_m=uncertainty,
        )
    return OperatorResult(layer=layer, extras=extras)
