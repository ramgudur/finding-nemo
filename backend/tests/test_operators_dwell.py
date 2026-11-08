import numpy as np
import pytest
from synthetic import FixedClock, LocalGrid, events_from_xy

from app.engine.operators.dwell import DwellParams, dwell, stays

GRID = LocalGrid()
ROOMS = [(100.0, 100.0), (380.0, 150.0), (250.0, 400.0)]


def _scenario(seed=0, n_per_room=8, n_walkers=40, gps_sigma=8.0, duration=300, dt=10):
    """Hiders sit in three rooms for the whole window; walkers cross the area at ~1.3 m/s."""
    rng = np.random.default_rng(seed)
    rows = []
    for r, room in enumerate(ROOMS):
        for k in range(n_per_room):
            spot = np.array(room) + rng.normal(0, 4, 2)
            rows += [(f"h{r}_{k}", float(t), *(spot + rng.normal(0, gps_sigma, 2))) for t in range(0, duration + 1, dt)]
    for k in range(n_walkers):
        start = rng.uniform(0, 500, 2)
        ang = rng.uniform(0, 2 * np.pi)
        v = 1.3 * np.array([np.cos(ang), np.sin(ang)])
        rows += [
            (f"w{k}", float(t), *(start + v * t + rng.normal(0, gps_sigma, 2))) for t in range(0, duration + 1, dt)
        ]
    return events_from_xy(GRID, rows)


def test_finds_one_cluster_per_room_and_ignores_walkers():
    for seed in range(5):
        r = dwell(_scenario(seed), DwellParams(), None, FixedClock(300.0), GRID)
        big = [c for c in r.extras["clusters"] if c["n_users"] >= 4]
        assert len(big) == 3
        for c in big:
            d = min(GRID.dist_m(c, room) for room in ROOMS)
            assert d < 10.0
            assert c["n_users"] == 8 and c["mean_dwell_s"] >= 120
        # the layer peaks in a room
        i, j = np.unravel_index(np.argmax(r.layer), r.layer.shape)
        assert min(np.hypot((j + 0.5) * 25 - rx, (i + 0.5) * 25 - ry) for rx, ry in ROOMS) < 30


def test_short_stops_are_not_stays():
    t = np.arange(0, 100, 10.0)
    assert stays(t, np.zeros_like(t), np.zeros_like(t), 25.0, 120.0) == []
    t = np.arange(0, 200, 10.0)
    assert stays(t, np.zeros_like(t), np.zeros_like(t), 25.0, 120.0) == [(0, len(t) - 1)]


def test_stay_then_walk_away_decays():
    rows = [("u", float(t), 250.0, 250.0) for t in range(0, 181, 10)]
    rows += [("u", float(t), 250.0 + 2.0 * (t - 180), 250.0) for t in range(190, 600, 10)]
    ev = events_from_xy(GRID, rows)
    params = DwellParams()
    now_r = dwell(ev, params, None, FixedClock(180.0), GRID)
    later = dwell(ev, params, None, FixedClock(180.0 + params.half_life_s), GRID)
    assert now_r.extras["n_stays"] == 1 and later.extras["n_stays"] == 1
    # the first step away (20 m) is still inside R, so the stay ends ~10 s late: ratio ~0.51
    assert later.layer.max() / now_r.layer.max() == pytest.approx(0.5, abs=0.03)


def test_trust_and_empty():
    ev = _scenario(1, n_walkers=0)
    trust = {f"h0_{k}": 0.0 for k in range(8)}
    r = dwell(ev, DwellParams(), trust, FixedClock(300.0), GRID)
    assert len([c for c in r.extras["clusters"] if c["n_users"] >= 4]) == 2
    empty = dwell(events_from_xy(GRID, []), DwellParams(), None, FixedClock(0.0), GRID)
    assert not empty.layer.any() and empty.extras["clusters"] == []
