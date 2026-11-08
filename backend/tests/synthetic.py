"""Synthetic scenarios for operator tests. Test-only: no demo values belong in backend/app."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.engine.operators.base import Events

EARTH_R = 6_371_000.0
LAT0, LON0 = 40.0, -75.0  # arbitrary test origin


@dataclass
class FixedClock:
    t: float

    def now(self) -> float:
        return self.t


@dataclass
class LocalGrid:
    """Equirectangular test grid; SW corner at (LAT0, LON0)."""

    cell_m: float = 25.0
    h: int = 20
    w: int = 20

    @property
    def shape(self) -> tuple[int, int]:
        return (self.h, self.w)

    def to_xy(self, lat, lon):
        x = np.radians(np.asarray(lon) - LON0) * EARTH_R * np.cos(np.radians(LAT0))
        y = np.radians(np.asarray(lat) - LAT0) * EARTH_R
        return x, y

    def to_latlon(self, x, y):
        lat = LAT0 + np.degrees(np.asarray(y) / EARTH_R)
        lon = LON0 + np.degrees(np.asarray(x) / (EARTH_R * np.cos(np.radians(LAT0))))
        return lat, lon

    def latlon_of(self, xy) -> tuple[float, float]:
        lat, lon = self.to_latlon(np.array([xy[0]]), np.array([xy[1]]))
        return float(lat[0]), float(lon[0])

    def dist_m(self, point: dict, xy) -> float:
        x, y = self.to_xy(np.array([point["lat"]]), np.array([point["lon"]]))
        return float(np.hypot(x[0] - xy[0], y[0] - xy[1]))


def events_from_xy(grid: LocalGrid, rows: list[tuple[str, float, float, float]]) -> Events:
    """rows: (user_id, t, x, y) in grid meters."""
    uid = np.array([r[0] for r in rows], dtype=object)
    t = np.array([r[1] for r in rows], dtype=float)
    lat, lon = grid.to_latlon(np.array([r[2] for r in rows]), np.array([r[3] for r in rows]))
    return Events(uid, t, lat, lon)


def flee_scenario(
    grid: LocalGrid,
    origin=(250.0, 250.0),
    n_people=200,
    flee_radius=150.0,
    hide_frac=0.2,
    area=300.0,
    duration=60,
    dt=5,
    gps_sigma=8.0,
    n_spoof=0,
    reverse=False,
    seed=0,
) -> Events:
    """Port of reference simulate(): within flee_radius of `origin`, most people run away at 2-4 m/s
    (+-30 deg), some hide; outside it, walkers drift randomly. Spoofers ping one fixed spot.
    reverse=True plays time backwards, so people converge on `origin` (a sink)."""
    rng = np.random.default_rng(seed)
    o = np.array(origin)
    start = o + rng.uniform(-area / 2, area / 2, size=(n_people, 2))
    rows = []
    for i, s in enumerate(start):
        rel = s - o
        r = np.linalg.norm(rel)
        if r < flee_radius and rng.random() > hide_frac:
            ang = np.arctan2(rel[1], rel[0]) + np.radians(rng.uniform(-30, 30))
            v = rng.uniform(2.0, 4.0) * np.array([np.cos(ang), np.sin(ang)])
        elif r < flee_radius:
            v = np.zeros(2)
        else:
            ang = rng.uniform(0, 2 * np.pi)
            v = 1.2 * np.array([np.cos(ang), np.sin(ang)])
        for t in range(0, duration + 1, dt):
            pos = s + v * t + rng.normal(0, gps_sigma, 2)
            rows.append((f"u{i}", float(duration - t if reverse else t), pos[0], pos[1]))
    for j in range(n_spoof):
        for t in range(0, duration + 1, dt):
            rows.append((f"spoof{j}", float(t), o[0] - 140.0, o[1] + 130.0))
    return events_from_xy(grid, rows)
