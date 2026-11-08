"""Shared types and helpers for operators.

An operator is a pure function `(events, params, trust, clock, grid) -> OperatorResult`.
It reads time only from `clock`, places points only through `grid`, and applies
per-user trust itself, so the layer it returns is independent of combine weights.

Layer convention (all operators): a single fresh, fully trusted contribution peaks
at ~1.0 after smoothing, whatever the cell size. A layer value therefore reads as
"effective number of trusted contributions nearby", and `noise_floor` is in the
same unit (combine divides by max(p99, noise_floor)).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
from pydantic import BaseModel, ConfigDict, Field


class GridLike(Protocol):
    """What operators need from `engine/grid.py`.

    Local meters with the origin at the grid's south-west corner: x grows east,
    y grows north. Cell (row i, col j) covers y in [i, i+1)·cell_m, x in [j, j+1)·cell_m.
    """

    cell_m: float

    @property
    def shape(self) -> tuple[int, int]:  # (H, W) = (rows, cols)
        ...

    def to_xy(self, lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]: ...

    def to_latlon(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]: ...


class ClockLike(Protocol):
    """What operators need from `core/clock.py`: current time in seconds (UTC epoch or sim time)."""

    def now(self) -> float: ...


@dataclass(frozen=True)
class Events:
    """Column-wise batch of one input's events. `t` is in the same seconds as `ClockLike.now()`."""

    user_id: np.ndarray  # str/object; "" when the event has no user
    t: np.ndarray
    lat: np.ndarray
    lon: np.ndarray
    fields: Mapping[str, np.ndarray] = field(default_factory=dict)  # the input's declared fields, per event

    def __post_init__(self) -> None:
        n = len(self.t)
        if not (len(self.user_id) == len(self.lat) == len(self.lon) == n):
            raise ValueError("Events columns must have equal length")
        if any(len(v) != n for v in self.fields.values()):
            raise ValueError("Events fields must have one value per event")

    def __len__(self) -> int:
        return len(self.t)

    @classmethod
    def from_records(cls, records: list[Mapping[str, Any]]) -> Events:
        """`t` is seconds; every key besides user_id/t/ts/lat/lon becomes a field (None where missing)."""
        names = sorted({k for r in records for k in r} - {"user_id", "t", "ts", "lat", "lon"})
        return cls(
            user_id=np.array([str(r.get("user_id") or "") for r in records], dtype=object),
            t=np.array([float(r["t"]) for r in records], dtype=float),
            lat=np.array([float(r["lat"]) for r in records], dtype=float),
            lon=np.array([float(r["lon"]) for r in records], dtype=float),
            fields={k: np.array([r.get(k) for r in records], dtype=object) for k in names},
        )

    def select(self, mask: np.ndarray) -> Events:
        return Events(
            self.user_id[mask],
            self.t[mask],
            self.lat[mask],
            self.lon[mask],
            {k: v[mask] for k, v in self.fields.items()},
        )

    def column(self, name: str, default: float) -> np.ndarray:
        """A declared field as floats; missing field, None or non-numeric values become `default`."""
        raw = self.fields.get(name)
        if raw is None:
            return np.full(len(self), float(default))
        out = np.empty(len(self))
        for i, v in enumerate(raw):
            try:
                out[i] = default if v is None else float(v)
            except (TypeError, ValueError):
                out[i] = default
        return np.where(np.isfinite(out), out, default)


@dataclass
class OperatorResult:
    layer: np.ndarray  # float64 [H, W], >= 0
    extras: dict[str, Any] = field(default_factory=dict)


class OperatorParams(BaseModel):
    """Base params model. Every operator declares its own default `noise_floor`."""

    model_config = ConfigDict(extra="forbid")

    noise_floor: float = Field(gt=0)


def trust_weights(user_id: np.ndarray, trust: Mapping[str, float] | None) -> np.ndarray:
    """Per-event trust in [0, 1]; unknown users get 1.0."""
    if not trust:
        return np.ones(len(user_id))
    w = np.array([trust.get(u, 1.0) for u in user_id], dtype=float)
    return np.clip(w, 0.0, 1.0)


def cell_index(grid: GridLike, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(row, col, inside) for local-meter points. Points outside the grid are flagged, not clipped."""
    h, w = grid.shape
    col = np.floor(x / grid.cell_m).astype(int)
    row = np.floor(y / grid.cell_m).astype(int)
    inside = (row >= 0) & (row < h) & (col >= 0) & (col < w)
    return row, col, inside


def _gauss_matrix(n: int, sigma_cells: float) -> np.ndarray:
    k = np.arange(n)
    d = k[:, None] - k[None, :]
    return np.exp(-0.5 * (d / sigma_cells) ** 2)


def gaussian_smooth(grid_values: np.ndarray, sigma_cells: float) -> np.ndarray:
    """Separable Gaussian with peak 1 (not mass 1), so a single deposit of 1 stays ~1 at its cell.
    Works for any grid size (no kernel-longer-than-grid edge cases)."""
    if sigma_cells <= 0:
        return grid_values.astype(float, copy=True)
    h, w = grid_values.shape
    return _gauss_matrix(h, sigma_cells) @ grid_values @ _gauss_matrix(w, sigma_cells).T
