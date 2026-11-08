"""field: gridded / station data sampled onto the grid, with an optional downwind shift.

Sampling: the latest reading per station (user_id, else rounded location) within `max_age_s`
is interpolated onto cell centers by inverse-distance weighting; the layer is
max(scale · value, 0).

Wind (optional): with `wind_speed_field` (m/s) and `wind_dir_field` (meteorological degrees,
the direction the wind blows FROM, as Open-Meteo reports it), the operator also interpolates
the wind vector (u east, v north) onto the grid as extras["wind_u"], extras["wind_v"].

Downwind shift (optional, `shift_layer`): another layer is carried by the wind with
advection-diffusion, ∂c/∂t + u·∇c = D∇²c, over `shift_horizon_s`. It is solved semi-Lagrangian
(back-trace each cell center by u·t, bilinear sample) followed by Gaussian diffusion with
σ = √(2·D·t). Operators only see their own input, so the tick applies it:
    shifted = field.advect(layers[extras["shift"]["layer"]], extras["wind_u"], extras["wind_v"],
                           grid.cell_m, **extras["shift"]["args"])
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
from pydantic import Field, model_validator

from .base import ClockLike, Events, GridLike, OperatorParams, OperatorResult, gaussian_smooth, trust_weights


class FieldParams(OperatorParams):
    noise_floor: float = Field(default=1.0, gt=0)
    value_field: str = "value"
    scale: float = 1.0
    max_age_s: float = Field(default=3600.0, gt=0)
    idw_power: float = Field(default=2.0, gt=0)
    wind_speed_field: str | None = None
    wind_dir_field: str | None = None
    shift_layer: str | None = None  # layer_id to carry downwind; needs the wind fields
    shift_horizon_s: float = Field(default=600.0, ge=0)
    diffusion_m2_s: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def _wind_pair(self) -> FieldParams:
        if (self.wind_speed_field is None) != (self.wind_dir_field is None):
            raise ValueError("wind_speed_field and wind_dir_field go together")
        if self.shift_layer and self.wind_speed_field is None:
            raise ValueError("shift_layer needs wind_speed_field and wind_dir_field")
        return self


def _latest_per_station(ev: Events) -> np.ndarray:
    """Index of the latest event per station."""
    key = np.array(
        [u if u else f"{round(a, 5)},{round(o, 5)}" for u, a, o in zip(ev.user_id, ev.lat, ev.lon, strict=True)],
        dtype=object,
    )
    latest: dict[str, int] = {}
    for i in np.argsort(ev.t, kind="stable"):
        latest[key[i]] = int(i)
    return np.array(sorted(latest.values()), dtype=int)


def idw(sx: np.ndarray, sy: np.ndarray, values: np.ndarray, grid: GridLike, power: float) -> np.ndarray:
    """Inverse-distance-weighted interpolation of station values onto cell centers."""
    h, w = grid.shape
    if len(values) == 0:
        return np.zeros((h, w))
    cx = (np.arange(w) + 0.5) * grid.cell_m
    cy = (np.arange(h) + 0.5) * grid.cell_m
    d = np.hypot(cy[:, None, None] - sy[None, None, :], cx[None, :, None] - sx[None, None, :])
    wts = 1.0 / np.maximum(d, 0.5 * grid.cell_m) ** power
    return (wts * values).sum(axis=2) / wts.sum(axis=2)


def wind_uv(speed: np.ndarray, from_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Meteorological (speed, direction it blows FROM) -> (u east, v north), m/s."""
    rad = np.radians(from_deg)
    return -speed * np.sin(rad), -speed * np.cos(rad)


def _bilinear(layer: np.ndarray, fx: np.ndarray, fy: np.ndarray) -> np.ndarray:
    """Sample `layer` at fractional cell coordinates (fx = col, fy = row of cell centers); 0 outside."""
    h, w = layer.shape
    padded = np.pad(layer, 1)
    fx, fy = np.clip(fx + 1, 0, w + 1), np.clip(fy + 1, 0, h + 1)
    x0 = np.clip(np.floor(fx).astype(int), 0, w)
    y0 = np.clip(np.floor(fy).astype(int), 0, h)
    ax, ay = fx - x0, fy - y0
    return (
        padded[y0, x0] * (1 - ax) * (1 - ay)
        + padded[y0, x0 + 1] * ax * (1 - ay)
        + padded[y0 + 1, x0] * (1 - ax) * ay
        + padded[y0 + 1, x0 + 1] * ax * ay
    )


def advect(
    layer: np.ndarray,
    u: np.ndarray,
    v: np.ndarray,
    cell_m: float,
    horizon_s: float,
    diffusion_m2_s: float = 0.0,
) -> np.ndarray:
    """Carry `layer` downwind for `horizon_s`: semi-Lagrangian advection, then Gaussian diffusion.
    Mass that leaves the grid is lost. u, v are [H, W] (or scalars) in m/s."""
    h, w = layer.shape
    rows, cols = np.indices((h, w), dtype=float)
    du = np.broadcast_to(u, (h, w)) * horizon_s / cell_m
    dv = np.broadcast_to(v, (h, w)) * horizon_s / cell_m
    shifted = _bilinear(layer, cols - du, rows - dv)
    sigma_m = np.sqrt(2.0 * diffusion_m2_s * horizon_s)
    if sigma_m > 0:
        # mass-preserving kernel for diffusion (gaussian_smooth is peak-1)
        k = np.arange(-int(3 * sigma_m / cell_m) - 1, int(3 * sigma_m / cell_m) + 2)
        mass = np.exp(-0.5 * (k * cell_m / sigma_m) ** 2).sum() ** 2
        shifted = gaussian_smooth(shifted, sigma_m / cell_m) / mass
    return shifted


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
    ev = ev.select(_latest_per_station(ev)) if len(ev) else ev
    sx, sy = grid.to_xy(ev.lat, ev.lon)

    values = ev.column(params.value_field, np.nan)
    ok = np.isfinite(values)
    layer = np.maximum(params.scale * idw(sx[ok], sy[ok], values[ok], grid, params.idw_power), 0.0)
    extras: dict = {"n_stations": int(ok.sum())}

    if params.wind_speed_field and params.wind_dir_field:
        spd = ev.column(params.wind_speed_field, np.nan)
        deg = ev.column(params.wind_dir_field, np.nan)
        okw = np.isfinite(spd) & np.isfinite(deg)
        u, v = wind_uv(spd[okw], deg[okw])
        extras["wind_u"] = idw(sx[okw], sy[okw], u, grid, params.idw_power)
        extras["wind_v"] = idw(sx[okw], sy[okw], v, grid, params.idw_power)
        if params.shift_layer:
            extras["shift"] = {
                "layer": params.shift_layer,
                "args": {"horizon_s": params.shift_horizon_s, "diffusion_m2_s": params.diffusion_m2_s},
            }
    return OperatorResult(layer=layer, extras=extras)
