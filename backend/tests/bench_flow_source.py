"""Benchmark: flow_source point estimate vs the lead's reference/flowsource.py on synthetic scenarios.

    cd backend && python tests/bench_flow_source.py [--seeds 40]

Reports the median / p90 error (m) of the back-projected source vs the number of tracks, GPS noise and
the spoof fraction (stationary duplicates and a coordinated fake flight from a decoy origin), plus
how often the true source lies within 2 x uncertainty_m (2-D Gaussian: ~86% if calibrated).
Test-only: scenario values stay out of backend/app.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parents[1])]

from reference import flowsource as ref  # noqa: E402
from synthetic import FixedClock, LocalGrid, events_from_xy  # noqa: E402

from app.engine.operators.flow_source import FlowSourceParams, flow_source  # noqa: E402

GRID = LocalGrid(cell_m=25.0, h=24, w=24)
ORIGIN = np.array([300.0, 300.0])
DECOY = ORIGIN + np.array([-160.0, 150.0])
WINDOW_S, NOW = 20.0, 30.0


def scenario(n=80, spoof_frac=0.0, spoof_kind="dupes", gps=8.0, seed=0, hide=0.2, duration=60, dt=2.0):
    """Entities within 150 m of ORIGIN flee at 2-4 m/s (+-30 deg) after staggered starts, some stay put;
    the rest drift. Spoofers either ping DECOY exactly or fake a straight, uniform flight from it."""
    rng = np.random.default_rng(seed)
    rows = []
    n_spoof = int(round(spoof_frac * n / max(1e-9, 1 - spoof_frac)))
    for i, s in enumerate(ORIGIN + rng.uniform(-150, 150, size=(n, 2))):
        rel = s - ORIGIN
        if np.linalg.norm(rel) < 150 and rng.random() > hide:
            ang = np.arctan2(rel[1], rel[0]) + np.radians(rng.uniform(-30, 30))
            v = rng.uniform(2.0, 4.0) * np.array([np.cos(ang), np.sin(ang)])
        elif np.linalg.norm(rel) < 150:
            v = np.zeros(2)
        else:
            ang = rng.uniform(0, 2 * np.pi)
            v = 1.2 * np.array([np.cos(ang), np.sin(ang)])
        t0 = rng.uniform(0, 10)
        for t in np.arange(0, duration - t0 + 1e-9, dt):
            pos = s + v * t + rng.normal(0, gps, 2)
            rows.append((f"u{i}", float(t0 + t), pos[0], pos[1]))
    for j in range(n_spoof):
        a = 2 * np.pi * j / max(n_spoof, 1)
        d = np.array([np.cos(a), np.sin(a)])
        for t in np.arange(0, duration + 1e-9, dt):
            p = DECOY if spoof_kind == "dupes" else DECOY + d * (10 + 4.0 * t)
            rows.append((f"s{j}", float(t), p[0], p[1]))
    return rows


def operator_estimate(rows):
    ev = events_from_xy(GRID, rows)
    res = flow_source(ev, FlowSourceParams(window_s=WINDOW_S), None, FixedClock(NOW), GRID)
    pt = res.extras["point"]
    if pt is None:
        return None, None
    x, y = GRID.to_xy(np.array([pt["lat"]]), np.array([pt["lon"]]))
    return np.array([x[0], y[0]]), res.extras["uncertainty_m"]


def reference_estimate(rows):
    uid = {u: k for k, u in enumerate(sorted({r[0] for r in rows}))}
    pings = np.array([(uid[r[0]], r[1], r[2], r[3]) for r in rows], dtype=float)
    p, _, _ = ref.backproject_source(ref.displacements(pings, NOW - WINDOW_S + 1e-9, NOW))
    return p


def evaluate(seeds, **kw):
    ref_err, op_err, covered = [], [], []
    for s in seeds:
        rows = scenario(seed=s, **kw)
        p_ref = reference_estimate(rows)
        p_op, unc = operator_estimate(rows)
        ref_err.append(np.inf if p_ref is None else float(np.linalg.norm(p_ref - ORIGIN)))
        e = np.inf if p_op is None else float(np.linalg.norm(p_op - ORIGIN))
        op_err.append(e)
        if unc is not None:
            covered.append(e <= 2 * unc)
    return np.array(ref_err), np.array(op_err), (float(np.mean(covered)) if covered else float("nan"))


CASES = [
    *[(f"{n} tracks", {"n": n}) for n in (20, 40, 80, 200)],
    *[(f"80 tracks, GPS {g:.0f} m", {"n": 80, "gps": g}) for g in (4.0, 15.0, 25.0)],
    *[(f"80 + {f:.0%} duplicate spoofers", {"n": 80, "spoof_frac": f}) for f in (0.1, 0.25, 0.4)],
    *[
        (f"80 + {f:.0%} fake-flight spoofers", {"n": 80, "spoof_frac": f, "spoof_kind": "flight"})
        for f in (0.1, 0.25, 0.4)
    ],
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=40)
    seeds = range(ap.parse_args().seeds)
    print(f"flow_source vs reference: error to the true source (m), {len(seeds)} seeds, {WINDOW_S:.0f} s window")
    print(f"{'scenario':<34}{'reference med/p90':>20}{'operator med/p90':>20}{'within 2x unc':>16}")
    for label, kw in CASES:
        r, o, cov = evaluate(seeds, **kw)
        print(
            f"{label:<34}{np.median(r):>10.1f} /{np.percentile(r, 90):>7.1f}"
            f"{np.median(o):>12.1f} /{np.percentile(o, 90):>7.1f}{cov:>15.0%}"
        )


if __name__ == "__main__":
    main()
