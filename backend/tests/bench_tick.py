"""Benchmark: one engine tick at grid sizes up to the max (250,000 cells), per stage, plus cache memory.

    cd backend && python tests/bench_tick.py [--sizes 100 250 500] [--ticks 3] [--spread]

Synthetic, domain-neutral app: points (density), tracks (flow_source + dwell), a multi-station
scalar field (field / IDW) and located documents (event_points). ``--spread`` scatters the points
over the whole grid so one big connected hotspot forms (the worst case for hotspot labelling).
Test-only: scenario values stay out of backend/app.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent)]

from app.core.clock import SimClock  # noqa: E402
from app.core.models import AppSpec  # noqa: E402
from app.engine import tick as tick_mod  # noqa: E402
from app.engine.tick import AppRuntime, InMemoryEventSource, tick_app  # noqa: E402

CELL_M = 10.0
LAT0, LON0 = 45.0, 10.0
T0 = 1_000_000.0
M_PER_DEG = 6_371_000.0 * math.pi / 180.0


def bbox(n: int) -> dict:
    side = n * CELL_M - 1.0  # just under n cells each way
    return {
        "min_lat": LAT0,
        "min_lon": LON0,
        "max_lat": LAT0 + side / M_PER_DEG,
        "max_lon": LON0 + side / (M_PER_DEG * math.cos(math.radians(LAT0 + side / M_PER_DEG / 2))),
    }


def spec(n: int) -> AppSpec:
    return AppSpec.model_validate(
        {
            "app_id": f"bench-{n}",
            "intent": "benchmark",
            "status": "active",
            "grid": {"cell_m": CELL_M, "bbox": bbox(n)},
            "inputs": [
                {
                    "id": "points",
                    "kind": "push",
                    "shape": "point",
                    "tier": "flexible",
                    "meaning": "a report at a place",
                    "operators": [{"op": "density", "weight": 0.3, "half_life_s": 300, "sigma_m": 30}],
                },
                {
                    "id": "tracks",
                    "kind": "push",
                    "shape": "track",
                    "tier": "flexible",
                    "meaning": "an entity's position stream",
                    "operators": [
                        {"op": "flow_source", "weight": 0.35},
                        {"op": "dwell", "weight": 0.35, "radius_m": 25, "min_dwell_s": 40},
                    ],
                },
                {
                    "id": "stations",
                    "kind": "push",
                    "shape": "field",
                    "tier": "stiff",
                    "meaning": "station readings of a scalar",
                    "fields": [{"name": "value", "type": "number", "required": True}],
                    "operators": [{"op": "field", "weight": 0.3, "value_field": "value"}],
                },
                {
                    "id": "docs",
                    "kind": "push",
                    "shape": "document",
                    "tier": "stiff",
                    "meaning": "a located document",
                    "operators": [{"op": "event_points", "weight": 0.3, "sigma_m": 40}],
                },
            ],
            "outputs": {"layers": []},
        }
    )


def fill(src: InMemoryEventSource, rt: AppRuntime, now: float, *, spread: bool, seed: int = 0) -> None:
    rng = np.random.default_rng(seed)
    g = rt.grid
    h, w = g.shape
    W, H = w * CELL_M, h * CELL_M
    app = rt.spec.app_id

    def ll(x, y):
        lat, lon = g.to_latlon(np.asarray(x, float), np.asarray(y, float))
        return np.atleast_1d(lat), np.atleast_1d(lon)

    # points: a few clusters, or spread over the whole grid
    n_pts = 3000
    if spread:
        x, y = rng.uniform(0, W, n_pts), rng.uniform(0, H, n_pts)
    else:
        cx, cy = rng.uniform(0.2 * W, 0.8 * W, 6), rng.uniform(0.2 * H, 0.8 * H, 6)
        k = rng.integers(0, 6, n_pts)
        x, y = cx[k] + rng.normal(0, 40, n_pts), cy[k] + rng.normal(0, 40, n_pts)
    lat, lon = ll(x, y)
    src.add(
        app,
        "points",
        [
            {"user_id": f"p{i}", "t": now - rng.uniform(0, 600), "lat": a, "lon": b}
            for i, (a, b) in enumerate(zip(lat, lon, strict=True))
        ],
    )

    # tracks: 200 entities moving away from one origin, 2 s apart, 60 s each
    ox, oy = 0.5 * W, 0.5 * H
    recs = []
    for i in range(200):
        ang, sp = rng.uniform(0, 2 * math.pi), rng.uniform(1.0, 3.0)
        r0 = rng.uniform(5, 20)
        ts = np.arange(30) * 2.0
        xs, ys = ox + (r0 + sp * ts) * math.cos(ang), oy + (r0 + sp * ts) * math.sin(ang)
        la, lo = ll(xs, ys)
        recs += [
            {"user_id": f"t{i}", "t": now - 60 + t, "lat": a, "lon": b} for t, a, b in zip(ts, la, lo, strict=True)
        ]
    src.add(app, "tracks", recs)

    # stations: 25 fixed stations with a smooth scalar
    sx, sy = rng.uniform(0, W, 25), rng.uniform(0, H, 25)
    la, lo = ll(sx, sy)
    src.add(
        app,
        "stations",
        [
            {"user_id": f"s{i}", "t": now - 30, "lat": a, "lon": b, "value": float(sx[i] / W)}
            for i, (a, b) in enumerate(zip(la, lo, strict=True))
        ],
    )

    # documents: 300 located documents
    dx, dy = rng.uniform(0, W, 300), rng.uniform(0, H, 300)
    la, lo = ll(dx, dy)
    src.add(
        app,
        "docs",
        [
            {"user_id": f"d{i}", "t": now - rng.uniform(0, 600), "lat": a, "lon": b}
            for i, (a, b) in enumerate(zip(la, lo, strict=True))
        ],
    )


class Timers:
    """Wraps the tick's stage functions (by name in tick.py's namespace) and sums their wall time."""

    def __init__(self) -> None:
        self.t: dict[str, float] = defaultdict(float)
        self._orig: dict[str, object] = {}

    def wrap(self, obj, name: str, label: str) -> None:
        fn = getattr(obj, name)
        self._orig[(obj, name)] = fn

        def timed(*a, **k):
            s = time.perf_counter()
            try:
                return fn(*a, **k)
            finally:
                key = label(*a, **k) if callable(label) else label
                self.t[key] += time.perf_counter() - s

        setattr(obj, name, timed)

    def restore(self) -> None:
        for (obj, name), fn in self._orig.items():
            setattr(obj, name, fn)


def run(n: int, ticks: int, spread: bool) -> dict:
    sp = spec(n)
    t_build = time.perf_counter()
    rt = AppRuntime(sp, cache_budget_bytes=tick_mod.CACHE_BUDGET_BYTES)  # as TickEngine.add_app does
    t_build = time.perf_counter() - t_build
    src = InMemoryEventSource()
    fill(src, rt, T0, spread=spread)
    clock = SimClock(T0)
    tm = Timers()
    tm.wrap(tick_mod.registry, "run", lambda op, *a, **k: f"op:{op}")
    tm.wrap(tick_mod, "combine", "combine")
    tm.wrap(tick_mod, "find_hotspots", "hotspots")
    tm.wrap(tick_mod.candidates, "generate", "candidates")
    tm.wrap(rt.cache, "push", "cache_push")
    totals = []
    try:
        for k in range(ticks):
            now = T0 + 2.0 * k
            s = time.perf_counter()
            r = tick_app(rt, src, now=now, clock=clock)
            totals.append(time.perf_counter() - s)
    finally:
        tm.restore()
    cache_mb = (rt.cache._layers.nbytes + rt.cache._overrides.nbytes + rt.cache._t.nbytes) / 2**20
    return {
        "n": n,
        "cells": rt.grid.shape[0] * rt.grid.shape[1],
        "layers": len(rt.weighted_ids),
        "build_s": t_build,
        "tick_s_median": float(np.median(totals)),
        "tick_s_max": float(max(totals)),
        "stages_s_per_tick": {k: v / ticks for k, v in sorted(tm.t.items(), key=lambda kv: -kv[1])},
        "cache_mb": cache_mb,
        "cache_frames": rt.cache.capacity,
        "cache_gap_s": rt.cache_min_gap_s,
        "hotspots": len(r.hotspots),
        "biggest_hotspot_cells": max((h.n_cells for h in r.hotspots), default=0),
        "errors": r.errors,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=[100, 250, 500])
    ap.add_argument("--ticks", type=int, default=3)
    ap.add_argument("--spread", action="store_true")
    a = ap.parse_args()
    for n in a.sizes:
        r = run(n, a.ticks, a.spread)
        print(
            f"{r['n']}x{r['n']} ({r['cells']:,} cells, {r['layers']} weighted layers): "
            f"tick median {r['tick_s_median']:.3f} s, max {r['tick_s_max']:.3f} s; "
            f"cache {r['cache_mb']:.0f} MB; build {r['build_s']:.2f} s; "
            f"{r['hotspots']} hotspots (biggest {r['biggest_hotspot_cells']:,} cells)"
        )
        for k, v in r["stages_s_per_tick"].items():
            print(f"    {k:<22} {v:8.3f} s")
        for e in r["errors"]:
            print(f"    error: {e}")


if __name__ == "__main__":
    main()
