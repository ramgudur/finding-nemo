import numpy as np
import pytest
from synthetic import FixedClock, LocalGrid, events_from_xy

from app.engine.operators.density import DensityParams, density

GRID = LocalGrid()
CENTER = (262.5, 262.5)  # middle of cell (10, 10)


def _run(rows, now=100.0, params=None, trust=None):
    return density(events_from_xy(GRID, rows), params or DensityParams(), trust, FixedClock(now), GRID)


def test_single_fresh_event_peaks_at_one_in_its_cell():
    r = _run([("u1", 100.0, *CENTER)])
    assert r.layer[10, 10] == pytest.approx(1.0)
    assert np.unravel_index(np.argmax(r.layer), r.layer.shape) == (10, 10)
    # stays below the default noise floor, so one tap can't saturate a normalized layer
    assert r.layer.max() < DensityParams().noise_floor


def test_half_life_decay():
    r = _run([("u1", 100.0 - 120.0, *CENTER)])
    assert r.layer[10, 10] == pytest.approx(0.5)


def test_trust_scales_and_zero_trust_drops():
    assert _run([("u1", 100.0, *CENTER)], trust={"u1": 0.4}).layer[10, 10] == pytest.approx(0.4)
    assert not _run([("u1", 100.0, *CENTER)], trust={"u1": 0.0}).layer.any()


def test_repeat_taps_by_one_user_are_deduped():
    rows = [("u1", 100.0 - k, CENTER[0] + k * 0.5, CENTER[1]) for k in range(5)]
    r = _run(rows)
    assert r.extras["n_events"] == 1 and r.extras["n_deduped"] == 4
    # different users at the same spot all count
    r = _run([(f"u{k}", 100.0, *CENTER) for k in range(5)])
    assert r.extras["n_events"] == 5
    # same user, far enough apart in time, counts twice
    assert _run([("u1", 100.0, *CENTER), ("u1", 80.0, *CENTER)]).extras["n_events"] == 2


def test_anonymous_events_are_not_deduped():
    r = _run([("", 100.0, *CENTER)] * 3)
    assert r.extras["n_events"] == 3


def test_future_old_and_out_of_grid_events_are_ignored():
    rows = [
        ("u1", 101.0, *CENTER),  # future
        ("u2", 100.0 - 120.0 * 9, *CENTER),  # older than the horizon
        ("u3", 100.0, -50.0, 100.0),  # west of the grid
        ("u4", 100.0, 100.0, 600.0),  # north of the grid
    ]
    r = _run(rows)
    assert not r.layer.any() and r.extras["n_events"] == 0


def test_cluster_outweighs_scatter():
    rng = np.random.default_rng(0)
    cluster = [(f"c{k}", 100.0, *(np.array(CENTER) + rng.normal(0, 10, 2))) for k in range(20)]
    scatter = [(f"s{k}", 100.0, *rng.uniform(0, 500, 2)) for k in range(20)]
    r = _run(cluster + scatter)
    i, j = np.unravel_index(np.argmax(r.layer), r.layer.shape)
    assert abs(i - 10) <= 1 and abs(j - 10) <= 1
    assert r.layer.max() > 10
