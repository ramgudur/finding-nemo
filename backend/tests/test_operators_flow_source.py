import numpy as np
import pytest
from synthetic import FixedClock, LocalGrid, events_from_xy, flee_scenario

from app.engine.operators.flow_source import FlowSourceParams, flow_source

GRID = LocalGrid()
ORIGIN = (250.0, 250.0)
FULL_WINDOW = FlowSourceParams(window_s=61)


def _run(events, params=FULL_WINDOW, trust=None):
    return flow_source(events, params, trust, FixedClock(float(events.t.max())), GRID)


def _peak_err(layer):
    i, j = np.unravel_index(np.argmax(layer), layer.shape)
    return float(np.hypot((j + 0.5) * GRID.cell_m - ORIGIN[0], (i + 0.5) * GRID.cell_m - ORIGIN[1]))


@pytest.mark.parametrize(
    ("kw", "max_median_err"),
    [({}, 15.0), ({"n_people": 40}, 30.0), ({"gps_sigma": 20.0}, 15.0)],
)
def test_backprojected_source_is_close(kw, max_median_err):
    errs = []
    for seed in range(10):
        r = _run(flee_scenario(GRID, origin=ORIGIN, seed=seed, **kw))
        assert r.extras["kind"] == "source"
        errs.append(GRID.dist_m(r.extras["point"], ORIGIN))
    assert np.median(errs) < max_median_err


def test_layer_peaks_near_source():
    errs = [_peak_err(_run(flee_scenario(GRID, origin=ORIGIN, seed=s)).layer) for s in range(10)]
    assert np.median(errs) < 100.0


def test_default_window_uses_last_20s():
    ev = flee_scenario(GRID, origin=ORIGIN, duration=60, seed=1)
    r = flow_source(ev, FlowSourceParams(), None, FixedClock(20.0), GRID)
    assert GRID.dist_m(r.extras["point"], ORIGIN) < 20.0


def test_converging_flow_is_sink_and_sink_polarity_heats_it():
    ev = flee_scenario(GRID, origin=ORIGIN, reverse=True, seed=2)
    r = _run(ev, FULL_WINDOW.model_copy(update={"polarity": "sink"}))
    assert r.extras["kind"] == "sink"
    assert GRID.dist_m(r.extras["point"], ORIGIN) < 20.0
    assert _peak_err(r.layer) < 100.0
    # the same converging flow gives (almost) no heat with source polarity around the target
    src = _run(ev)
    assert src.layer[10, 10] < 0.1 * r.layer[10, 10]


def test_stationary_spoofers_add_no_signal():
    clean = _run(flee_scenario(GRID, origin=ORIGIN, seed=3))
    spoofed = _run(flee_scenario(GRID, origin=ORIGIN, n_spoof=40, seed=3))
    np.testing.assert_allclose(spoofed.layer, clean.layer)
    assert spoofed.extras["point"] == clean.extras["point"]


def test_zero_trust_users_are_ignored():
    real = flee_scenario(GRID, origin=ORIGIN, n_people=80, seed=4)
    # 30 coordinated fakes all "fleeing" from a decoy point
    rng = np.random.default_rng(4)
    decoy = np.array([80.0, 420.0])
    rows = []
    for k in range(30):
        ang = rng.uniform(0, 2 * np.pi)
        d = np.array([np.cos(ang), np.sin(ang)])
        rows += [(f"fake{k}", float(t), *(decoy + d * (10 + 3 * t))) for t in range(0, 61, 5)]
    fakes = events_from_xy(GRID, rows)
    both = type(real)(
        *(
            np.r_[a, b]
            for a, b in zip(
                (real.user_id, real.t, real.lat, real.lon), (fakes.user_id, fakes.t, fakes.lat, fakes.lon), strict=True
            )
        )
    )
    trust = {f"fake{k}": 0.0 for k in range(30)}
    r = _run(both, trust=trust)
    assert GRID.dist_m(r.extras["point"], ORIGIN) < 30.0
    np.testing.assert_allclose(r.layer, _run(real).layer)


def test_empty_and_untracked_inputs():
    empty = events_from_xy(GRID, [])
    r = flow_source(empty, FlowSourceParams(), None, FixedClock(0.0), GRID)
    assert r.layer.shape == GRID.shape and not r.layer.any()
    assert r.extras["point"] is None
    # single taps (one ping per user) give no tracks
    taps = events_from_xy(GRID, [(f"u{i}", 1.0, 100.0 + i, 100.0) for i in range(10)])
    r = flow_source(taps, FlowSourceParams(), None, FixedClock(1.0), GRID)
    assert not r.layer.any() and r.extras["n_tracks"] == 0


def test_parallel_movers_give_no_point():
    rows = [(f"u{i}", float(t), 100.0 + 5 * t, 50.0 + 30 * i) for i in range(10) for t in range(0, 21, 5)]
    r = flow_source(events_from_xy(GRID, rows), FlowSourceParams(), None, FixedClock(20.0), GRID)
    assert r.extras["point"] is None and r.extras["n_tracks"] == 10


def test_deterministic():
    ev = flee_scenario(GRID, origin=ORIGIN, seed=5)
    a, b = _run(ev), _run(ev)
    np.testing.assert_array_equal(a.layer, b.layer)
    assert a.extras == b.extras


def test_physics_fit_beats_the_reference_on_noise_and_fake_flight():
    """Constant-velocity track fits (#84) vs reference/flowsource.py; full table: tests/bench_flow_source.py."""
    import bench_flow_source as bench

    seeds = range(12)
    for kw in ({"n": 80, "gps": 15.0}, {"n": 80, "spoof_frac": 0.1, "spoof_kind": "flight"}):
        ref_err, op_err, _ = bench.evaluate(seeds, **kw)
        assert np.median(op_err) < np.median(ref_err), kw
    _, op_err, covered = bench.evaluate(seeds, n=80)
    assert np.median(op_err) < 15
    assert 0.55 <= covered <= 0.98  # 2 x uncertainty_m covers most true sources (~75% measured)
