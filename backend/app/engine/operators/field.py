"""field: gridded or point-sampled data on the grid, with an optional shift along a vector field.

Sampling: the latest reading per sample point (user_id, else rounded location) within `max_age_s`
is interpolated onto cell centers by inverse-distance weighting; the layer is
max(scale · value, 0).

Vector field (optional): with `vector_speed_field` (magnitude, m/s) and `vector_dir_field`
(compass degrees), the operator also interpolates the vector (u east, v north) onto the grid as
extras["vector_u"], extras["vector_v"]. `vector_dir_convention` says what the degrees mean:
"from" (the direction the flow comes from) or "to" (the direction it heads).
Which quantity it is, is the spec's business.

Shift along the field (optional, `shift_layer`): another layer is carried along the vector field
with advection-diffusion, ∂c/∂t + u·∇c = D∇²c, over `shift_horizon_s`. Advection moves each cell's
mass forward along its trajectory (midpoint/RK2 substeps of at most one cell each, CFL ≤ 1, so curved
or sheared fields are followed) and deposits it bilinearly once at the end: mass is conserved except
what leaves the grid, also in converging or diverging flow. Then Gaussian diffusion with
σ = √(2·D·t). `diffusion_m2_s` unset means a spread of half a cell over the horizon (scales with
cell_m and fills gaps a forward scheme leaves in diverging flow); 0 keeps it sharp.
Operators only see their own input, so the tick applies it:
    shifted = field.advect(layers[extras["shift"]["layer"]], extras["vector_u"], extras["vector_v"],
                           grid.cell_m, **extras["shift"]["args"])
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from .base import ClockLike, Events, GridLike, OperatorParams, OperatorResult, gaussian_smooth, trust_weights


class FieldParams(OperatorParams):
    noise_floor: float = Field(default=1.0, gt=0)
    value_field: str = "value"
    scale: float = 1.0
    max_age_s: float = Field(default=3600.0, gt=0)
    idw_power: float = Field(default=2.0, gt=0)
    vector_speed_field: str | None = None
    vector_dir_field: str | None = None
    vector_dir_convention: Literal["from", "to"] = "from"
    shift_layer: str | None = None  # layer_id to carry along the vector field; needs the vector fields
    shift_horizon_s: float = Field(default=600.0, ge=0)
    diffusion_m2_s: float | None = Field(default=None, ge=0)  # None: half a cell of spread over the horizon

    @model_validator(mode="after")
    def _vector_pair(self) -> FieldParams:
        if (self.vector_speed_field is None) != (self.vector_dir_field is None):
            raise ValueError("vector_speed_field and vector_dir_field go together")
        if self.shift_layer and self.vector_speed_field is None:
            raise ValueError("shift_layer needs vector_speed_field and vector_dir_field")
        return self


def _latest_per_point(ev: Events) -> np.ndarray:
    """Index of the latest event per sample point."""
    key = np.array(
        [u if u else f"{round(a, 5)},{round(o, 5)}" for u, a, o in zip(ev.user_id, ev.lat, ev.lon, strict=True)],
        dtype=object,
    )
    latest: dict[str, int] = {}
    for i in np.argsort(ev.t, kind="stable"):
        latest[key[i]] = int(i)
    return np.array(sorted(latest.values()), dtype=int)


def idw(sx: np.ndarray, sy: np.ndarray, values: np.ndarray, grid: GridLike, power: float) -> np.ndarray:
    """Inverse-distance-weighted interpolation of point values onto cell centers."""
    h, w = grid.shape
    if len(values) == 0:
        return np.zeros((h, w))
    cx = (np.arange(w) + 0.5) * grid.cell_m
    cy = (np.arange(h) + 0.5) * grid.cell_m
    d = np.hypot(cy[:, None, None] - sy[None, None, :], cx[None, :, None] - sx[None, None, :])
    wts = 1.0 / np.maximum(d, 0.5 * grid.cell_m) ** power
    return (wts * values).sum(axis=2) / wts.sum(axis=2)


def vector_uv(
    speed: np.ndarray, deg: np.ndarray, convention: Literal["from", "to"] = "from"
) -> tuple[np.ndarray, np.ndarray]:
    """(magnitude, compass degrees) -> (u east, v north). "to": degrees point where the flow heads;
    "from": degrees point where it comes from (the vector is reversed)."""
    rad = np.radians(deg)
    sign = 1.0 if convention == "to" else -1.0
    return sign * speed * np.sin(rad), sign * speed * np.cos(rad)


def _sample_edge(f: np.ndarray, fx: np.ndarray, fy: np.ndarray) -> np.ndarray:
    """Bilinear sample at fractional cell coordinates, clamped to the edge (velocities extend outward)."""
    h, w = f.shape
    fx, fy = np.clip(fx, 0, w - 1), np.clip(fy, 0, h - 1)
    x0, y0 = (
        np.minimum(np.floor(fx).astype(int), w - 2 if w > 1 else 0),
        np.minimum(np.floor(fy).astype(int), h - 2 if h > 1 else 0),
    )
    x1, y1 = np.minimum(x0 + 1, w - 1), np.minimum(y0 + 1, h - 1)
    ax, ay = fx - x0, fy - y0
    return f[y0, x0] * (1 - ax) * (1 - ay) + f[y0, x1] * ax * (1 - ay) + f[y1, x0] * (1 - ax) * ay + f[y1, x1] * ax * ay


def advect(
    layer: np.ndarray,
    u: np.ndarray,
    v: np.ndarray,
    cell_m: float,
    horizon_s: float,
    diffusion_m2_s: float = 0.0,
    max_substeps: int = 64,
) -> np.ndarray:
    """Carry `layer` along the vector field (u east, v north; [H, W] or scalars, m/s) for `horizon_s`.

    Forward Lagrangian transport: each cell's mass rides its trajectory in midpoint (RK2) substeps of at
    most one cell (CFL ≤ 1, capped at `max_substeps`), then is deposited bilinearly. Mass is conserved
    except what leaves the grid. Then Gaussian diffusion with σ = √(2·D·horizon)."""
    h, w = layer.shape
    uu = np.broadcast_to(np.asarray(u, dtype=float), (h, w))
    vv = np.broadcast_to(np.asarray(v, dtype=float), (h, w))
    rows, cols = np.nonzero(layer)
    mass = layer[rows, cols].astype(float)
    px, py = cols.astype(float), rows.astype(float)
    reach = max(float(np.abs(uu).max(initial=0.0)), float(np.abs(vv).max(initial=0.0))) * horizon_s / cell_m
    n = int(min(max(np.ceil(reach), 1), max_substeps))
    step = horizon_s / n / cell_m
    for _ in range(n):
        mx = px + 0.5 * step * _sample_edge(uu, px, py)
        my = py + 0.5 * step * _sample_edge(vv, px, py)
        px = px + step * _sample_edge(uu, mx, my)
        py = py + step * _sample_edge(vv, mx, my)
    out = np.zeros((h, w))
    x0, y0 = np.floor(px).astype(int), np.floor(py).astype(int)
    ax, ay = px - x0, py - y0
    for dx, dy, wt in ((0, 0, (1 - ax) * (1 - ay)), (1, 0, ax * (1 - ay)), (0, 1, (1 - ax) * ay), (1, 1, ax * ay)):
        xi, yi = x0 + dx, y0 + dy
        inside = (xi >= 0) & (xi < w) & (yi >= 0) & (yi < h)
        np.add.at(out, (yi[inside], xi[inside]), (mass * wt)[inside])
    sigma_m = np.sqrt(2.0 * diffusion_m2_s * horizon_s)
    if sigma_m > 0:
        # mass-preserving kernel for diffusion (gaussian_smooth is peak-1)
        k = np.arange(-int(3 * sigma_m / cell_m) - 1, int(3 * sigma_m / cell_m) + 2)
        norm = np.exp(-0.5 * (k * cell_m / sigma_m) ** 2).sum() ** 2
        out = gaussian_smooth(out, sigma_m / cell_m) / norm
    return out


def default_diffusion(cell_m: float, horizon_s: float) -> float:
    """D giving half a cell of spread (σ = cell_m / 2) over the horizon."""
    return (0.5 * cell_m) ** 2 / (2.0 * horizon_s) if horizon_s > 0 else 0.0


def field(
    events: Events,
    params: FieldParams,
    trust: Mapping[str, float] | None,
    clock: ClockLike,
    grid: GridLike,
) -> OperatorResult:
    now = clock.now()
    ev = events.select((events.t <= now) & (events.t > now - params.max_age_s))
    ev = ev.select(trust_weights(ev.user_id, trust) > 0)
    ev = ev.select(_latest_per_point(ev)) if len(ev) else ev
    sx, sy = grid.to_xy(ev.lat, ev.lon)

    values = ev.column(params.value_field, np.nan)
    ok = np.isfinite(values)
    layer = np.maximum(params.scale * idw(sx[ok], sy[ok], values[ok], grid, params.idw_power), 0.0)
    extras: dict = {"n_points": int(ok.sum())}

    if params.vector_speed_field and params.vector_dir_field:
        spd = ev.column(params.vector_speed_field, np.nan)
        deg = ev.column(params.vector_dir_field, np.nan)
        okv = np.isfinite(spd) & np.isfinite(deg)
        u, v = vector_uv(spd[okv], deg[okv], params.vector_dir_convention)
        extras["vector_u"] = idw(sx[okv], sy[okv], u, grid, params.idw_power)
        extras["vector_v"] = idw(sx[okv], sy[okv], v, grid, params.idw_power)
        if params.shift_layer:
            extras["shift"] = {
                "layer": params.shift_layer,
                "args": {
                    "horizon_s": params.shift_horizon_s,
                    "diffusion_m2_s": params.diffusion_m2_s
                    if params.diffusion_m2_s is not None
                    else default_diffusion(grid.cell_m, params.shift_horizon_s),
                },
            }
    return OperatorResult(layer=layer, extras=extras)
