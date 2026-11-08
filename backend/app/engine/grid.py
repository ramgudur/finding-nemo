"""Grid geometry: bbox <-> local meters <-> cells, plus GeoJSON conversion.

Conventions (shared with engine/operators):
- ``bbox`` is GeoJSON order: ``(min_lon, min_lat, max_lon, max_lat)``.
- Local meters use an equirectangular projection with the origin at the bbox's
  south-west corner: x grows east, y grows north. Error is well under 0.1% for
  city-scale boxes, which is far below one cell.
- Arrays are ``[H, W]`` = ``[rows, cols]``. Cell ``(row i, col j)`` covers
  y in ``[i, i+1) * cell_m`` and x in ``[j, j+1) * cell_m``, so row 0 is the
  southern edge.
- GeoJSON coordinates are ``[lon, lat]``. This module is the backend's only
  place that builds GeoJSON coordinates; everything else passes (lat, lon).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

EARTH_RADIUS_M = 6_371_008.8  # mean Earth radius (IUGG)
_DEG = math.pi / 180.0


@dataclass(frozen=True)
class Grid:
    bbox: tuple[float, float, float, float]  # (min_lon, min_lat, max_lon, max_lat)
    cell_m: float
    # derived
    width_m: float = field(init=False)
    height_m: float = field(init=False)
    _m_per_deg_lat: float = field(init=False, repr=False)
    _m_per_deg_lon: float = field(init=False, repr=False)
    _shape: tuple[int, int] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if len(self.bbox) != 4:
            raise ValueError("bbox must be (min_lon, min_lat, max_lon, max_lat)")
        min_lon, min_lat, max_lon, max_lat = (float(v) for v in self.bbox)
        if not (-180.0 <= min_lon < max_lon <= 180.0):
            raise ValueError("bbox longitudes must satisfy -180 <= min_lon < max_lon <= 180")
        if not (-90.0 < min_lat < max_lat < 90.0):
            raise ValueError("bbox latitudes must satisfy -90 < min_lat < max_lat < 90")
        if not (self.cell_m > 0 and math.isfinite(self.cell_m)):
            raise ValueError("cell_m must be a positive finite number")

        lat0 = 0.5 * (min_lat + max_lat)
        m_lat = EARTH_RADIUS_M * _DEG
        m_lon = EARTH_RADIUS_M * _DEG * math.cos(lat0 * _DEG)
        width = (max_lon - min_lon) * m_lon
        height = (max_lat - min_lat) * m_lat
        shape = (max(1, math.ceil(height / self.cell_m - 1e-9)), max(1, math.ceil(width / self.cell_m - 1e-9)))

        object.__setattr__(self, "bbox", (min_lon, min_lat, max_lon, max_lat))
        object.__setattr__(self, "cell_m", float(self.cell_m))
        object.__setattr__(self, "width_m", width)
        object.__setattr__(self, "height_m", height)
        object.__setattr__(self, "_m_per_deg_lat", m_lat)
        object.__setattr__(self, "_m_per_deg_lon", m_lon)
        object.__setattr__(self, "_shape", shape)

    @classmethod
    def from_spec(cls, spec: Any) -> Grid:
        """Build from an AppSpec ``grid`` section: a mapping or object with ``cell_m`` and ``bbox``."""
        if isinstance(spec, dict):
            return cls(bbox=tuple(spec["bbox"]), cell_m=spec["cell_m"])  # type: ignore[arg-type]
        return cls(bbox=tuple(spec.bbox), cell_m=spec.cell_m)

    # -- shape -----------------------------------------------------------

    @property
    def shape(self) -> tuple[int, int]:
        """``(H, W)`` = (rows, cols)."""
        return self._shape

    @property
    def n_cells(self) -> int:
        return self._shape[0] * self._shape[1]

    def zeros(self, dtype: Any = np.float32) -> np.ndarray:
        return np.zeros(self._shape, dtype=dtype)

    # -- lat/lon <-> local meters ------------------------------------------

    def to_xy(self, lat: Any, lon: Any) -> tuple[np.ndarray, np.ndarray]:
        """Local meters from the SW corner. Vectorized; points outside the bbox give x/y outside [0, size)."""
        lat = np.asarray(lat, dtype=np.float64)
        lon = np.asarray(lon, dtype=np.float64)
        x = (lon - self.bbox[0]) * self._m_per_deg_lon
        y = (lat - self.bbox[1]) * self._m_per_deg_lat
        return x, y

    def to_latlon(self, x: Any, y: Any) -> tuple[np.ndarray, np.ndarray]:
        """Inverse of ``to_xy``: returns ``(lat, lon)``."""
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        lat = self.bbox[1] + y / self._m_per_deg_lat
        lon = self.bbox[0] + x / self._m_per_deg_lon
        return lat, lon

    # -- cells -----------------------------------------------------------

    def xy_to_cell(self, x: Any, y: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """``(row, col, inside)``. Rows/cols are clipped into range; use ``inside`` to drop outsiders."""
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        h, w = self._shape
        col = np.floor(x / self.cell_m)
        row = np.floor(y / self.cell_m)
        inside = np.isfinite(col) & np.isfinite(row) & (col >= 0) & (col < w) & (row >= 0) & (row < h)
        row_i = np.clip(np.nan_to_num(row, nan=0.0), 0, h - 1).astype(np.intp)
        col_i = np.clip(np.nan_to_num(col, nan=0.0), 0, w - 1).astype(np.intp)
        return row_i, col_i, inside

    def cell_of(self, lat: Any, lon: Any) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """``(row, col, inside)`` for lat/lon points."""
        return self.xy_to_cell(*self.to_xy(lat, lon))

    def contains(self, lat: Any, lon: Any) -> np.ndarray:
        return self.cell_of(lat, lon)[2]

    def cell_center_xy(self, row: Any, col: Any) -> tuple[np.ndarray, np.ndarray]:
        return (np.asarray(col, dtype=np.float64) + 0.5) * self.cell_m, (
            np.asarray(row, dtype=np.float64) + 0.5
        ) * self.cell_m

    def cell_center(self, row: Any, col: Any) -> tuple[np.ndarray, np.ndarray]:
        """``(lat, lon)`` of cell centers."""
        return self.to_latlon(*self.cell_center_xy(row, col))

    def centers_xy(self) -> tuple[np.ndarray, np.ndarray]:
        """Meshgrid of cell-center x and y, each ``[H, W]``."""
        h, w = self._shape
        cols = (np.arange(w, dtype=np.float64) + 0.5) * self.cell_m
        rows = (np.arange(h, dtype=np.float64) + 0.5) * self.cell_m
        x, y = np.meshgrid(cols, rows)
        return x, y

    def centers_latlon(self) -> tuple[np.ndarray, np.ndarray]:
        """Meshgrid of cell-center lat and lon, each ``[H, W]``."""
        return self.to_latlon(*self.centers_xy())

    def cells_within(self, lat: float, lon: float, radius_m: float) -> np.ndarray:
        """Boolean ``[H, W]`` mask of cells whose center is within ``radius_m`` of the point.

        If the point is inside the grid, its own cell is always included, so a small
        radius never yields an empty mask.
        """
        px, py = self.to_xy(lat, lon)
        cx, cy = self.centers_xy()
        mask = (cx - px) ** 2 + (cy - py) ** 2 <= float(radius_m) ** 2
        r, c, inside = self.xy_to_cell(px, py)
        if bool(inside):
            mask[int(r), int(c)] = True
        return mask

    # -- GeoJSON (the [lon, lat] boundary) ---------------------------------

    def cell_polygon(self, row: int, col: int) -> list[list[float]]:
        """Closed GeoJSON ring ``[[lon, lat], ...]`` (counter-clockwise) for one cell."""
        x0, y0 = col * self.cell_m, row * self.cell_m
        x1, y1 = min(x0 + self.cell_m, self.width_m), min(y0 + self.cell_m, self.height_m)
        xs = np.array([x0, x1, x1, x0, x0])
        ys = np.array([y0, y0, y1, y1, y0])
        lat, lon = self.to_latlon(xs, ys)
        return [[float(lo), float(la)] for la, lo in zip(lat, lon, strict=True)]

    def to_geojson(
        self,
        values: np.ndarray,
        *,
        min_value: float = 0.0,
        property_name: str = "score",
        decimals: int = 4,
    ) -> dict[str, Any]:
        """FeatureCollection of cell polygons whose value is ``> min_value``.

        Each feature carries ``{property_name, row, col}``. Cells at or below
        ``min_value`` (default: zeros) are skipped to keep payloads small.
        """
        values = np.asarray(values)
        if values.shape != self._shape:
            raise ValueError(f"values shape {values.shape} != grid shape {self._shape}")
        rows, cols = np.nonzero(np.nan_to_num(values, nan=0.0) > min_value)
        features = [
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [self.cell_polygon(int(r), int(c))]},
                "properties": {property_name: round(float(values[r, c]), decimals), "row": int(r), "col": int(c)},
            }
            for r, c in zip(rows, cols, strict=True)
        ]
        return {"type": "FeatureCollection", "bbox": list(self.bbox), "features": features}


# -- point helpers ----------------------------------------------------------


def to_geojson_point(lat: float, lon: float) -> list[float]:
    """``(lat, lon)`` -> GeoJSON ``[lon, lat]``."""
    return [float(lon), float(lat)]


def from_geojson_point(coords: Sequence[float]) -> tuple[float, float]:
    """GeoJSON ``[lon, lat]`` -> ``(lat, lon)``."""
    return float(coords[1]), float(coords[0])


def point_feature(lat: float, lon: float, properties: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": to_geojson_point(lat, lon)},
        "properties": dict(properties or {}),
    }


def haversine_m(lat1: Any, lon1: Any, lat2: Any, lon2: Any) -> np.ndarray:
    """Great-circle distance in meters. Vectorized."""
    p1, p2 = np.asarray(lat1, dtype=np.float64) * _DEG, np.asarray(lat2, dtype=np.float64) * _DEG
    dp = p2 - p1
    dl = (np.asarray(lon2, dtype=np.float64) - np.asarray(lon1, dtype=np.float64)) * _DEG
    a = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
