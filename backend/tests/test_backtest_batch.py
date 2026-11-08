import numpy as np
import pytest
from pydantic import ValidationError
from synthetic import LocalGrid, events_from_xy

from app.core.models import AppSpec
from app.engine import backtest as bt_mod
from app.engine import tiers
from app.engine.backtest import (
    SearchSpec,
    array_backend,
    rank_on_train,
    run_backtest_batch,
    sample_candidates,
)
from app.engine.layer_cache import CacheWindow
from app.engine.verification import VerificationContext, cell_events, evaluate, evaluate_batch

GRID = LocalGrid()  # 20 x 20 cells of 25 m
H, W = GRID.shape
A, B = (5, 5), (15, 15)  # cells
XY_A = ((A[1] + 0.5) * 25, (A[0] + 0.5) * 25)
IDS = ("crowd.density", "phones.flow_source")
NOW, SPLIT = 600.0, 420.0


def blob(at, peak=1.0, sigma=1.5):
    i, j = np.indices((H, W))
    return peak * np.exp(-0.5 * ((i - at[0]) ** 2 + (j - at[1]) ** 2) / sigma**2)


def _op(name: str) -> dict:
    return {"op": name, "weight": 1.0}


def _spec() -> AppSpec:
    lat, lon = GRID.to_latlon(np.array([0.0, 500.0]), np.array([0.0, 500.0]))
    flex = {"kind": "push", "tier": "flexible"}
    return AppSpec.model_validate(
        {
            "app_id": "batch-test",
            "intent": "synthetic batch backtest",
            "status": "active",
            "grid": {
                "cell_m": 25.0,
                "bbox": {"min_lat": lat[0], "min_lon": lon[0], "max_lat": lat[1], "max_lon": lon[1]},
            },
            "inputs": [
                {
                    **flex,
                    "id": "crowd",
                    "shape": "point",
                    "meaning": "press",
                    "operators": [{"op": "density", "weight": 1.0}],
                },
                {
                    **flex,
                    "id": "phones",
                    "shape": "track",
                    "meaning": "gps",
                    "operators": [{"op": "flow_source", "weight": 1.0}],
                },
                {
                    "id": "police",
                    "kind": "push",
                    "shape": "fixed_point",
                    "tier": "locked",
                    "meaning": "marker",
                    "operators": [{"op": "override", "weight": 1.0}],
                },
            ],
            "verification": [
                {"method": "authority_confirmation", "priority": 1},
                {"method": "predictive_check", "priority": 2},
                {"method": "cross_input_agreement", "priority": 3},
            ],
        }
    )


def _window(swap_after_split=False) -> CacheWindow:
    """crowd peaks at the decoy B (1.0), phones at the real spot A (0.9). With equal weights the
    clamped score peaks at B; shifting weight to phones moves the peak to A."""
    t = np.arange(2.0, NOW + 1e-9, 2.0)
    layers = np.empty((len(t), 2, H, W))
    for k, tk in enumerate(t):
        crowd, phones = blob(B, 1.0), blob(A, 0.9)
        if swap_after_split and tk > SPLIT:
            crowd, phones = blob(A, 1.0), blob(B, 0.9)
        layers[k] = (crowd, phones)
    return CacheWindow(t=t, layers=layers, overrides=np.zeros((len(t), H, W)), layer_ids=IDS)


def _events():
    rng = np.random.default_rng(0)
    crowd = [(f"c{k}", float(t), *rng.uniform(0, 500, 2)) for k in range(40) for t in (100.0, 450.0)]
    return {
        "crowd": events_from_xy(GRID, crowd),
        "police": events_from_xy(GRID, [("officer", 300.0, *XY_A), ("officer", 560.0, *XY_A)]),
    }


def _bounds(methods_with_data=3):
    slots = [
        tiers.WeightSlot("crowd.density", "flexible", 1.0),
        tiers.WeightSlot("phones.flow_source", "flexible", 1.0),
    ]
    return tiers.bounds(slots, total=2.0, now=NOW, methods_with_data=methods_with_data)


SEARCH = {"layers": {"crowd.density": [0.75, 1.25], "phones.flow_source": [0.75, 1.25]}, "samples": 300}


def _run(window=None, search=None, seed=0, **kw):
    return run_backtest_batch(
        _spec(),
        window if window is not None else _window(),
        _events(),
        search or SEARCH,
        now=NOW,
        bounds=kw.pop("bounds", _bounds()),
        grid=GRID,
        seed=seed,
        **kw,
    )


def test_batch_finds_weights_that_move_the_peak_to_the_authority_point():
    out = _run()
    bt = out.backtest
    assert bt.passed, out.reason
    assert bt.kind == "batch" and bt.device == "cpu"
    assert bt.candidates_tested == 300 and 0 < bt.candidates_valid <= 300
    assert out.reason.startswith("300 configs backtested in") and "on cpu" in out.reason
    best = out.candidates[0]
    assert best.passed and best.weights["phones.flow_source"] > best.weights["crowd.density"]
    assert sum(best.weights.values()) == pytest.approx(2.0)  # flexible total holds
    assert best.holdout["authority_confirmation"] > out.baseline_holdout["authority_confirmation"]
    assert len(out.candidates) <= 5


def test_ranks_on_train_only():
    # train says "weight phones"; after the split the layers swap places, so holdout would favor crowd
    out = _run(window=_window(swap_after_split=True))
    assert out.candidates
    for c in out.candidates:  # every holdout-evaluated candidate came from the train ranking
        assert c.weights["phones.flow_source"] > c.weights["crowd.density"]
        assert not c.passed
    assert not out.backtest.passed


def test_invalid_candidates_are_discarded_before_scoring(monkeypatch):
    seen = []
    real = bt_mod.evaluate_batch

    def spy(window, weights, *a, **k):
        seen.append(np.array(weights))
        return real(window, weights, *a, **k)

    monkeypatch.setattr(bt_mod, "evaluate_batch", spy)
    wide = {"layers": {"crowd.density": [0.0, 3.0], "phones.flow_source": [0.0, 3.0]}, "samples": 600}
    out = _run(search=wide)
    scored = np.concatenate(seen)
    assert out.backtest.candidates_valid < out.backtest.candidates_tested
    assert len(scored) == out.backtest.candidates_valid
    b = _bounds()
    assert (scored >= b.lo - 1e-9).all() and (scored <= b.hi + 1e-9).all()
    assert all(len(w) <= bt_mod.CHUNK for w in seen)  # chunked


def test_cold_start_freezes_flexible_weights():
    out = _run(bounds=_bounds(methods_with_data=1))
    assert not out.backtest.passed
    assert out.backtest.candidates_valid == 0
    assert "no valid candidates" in out.reason


def test_deterministic_with_fixed_seed():
    a, b = _run(seed=3), _run(seed=3)
    assert [c.weights for c in a.candidates] == [c.weights for c in b.candidates]
    assert a.backtest.train_scores == b.backtest.train_scores
    assert a.backtest.holdout_scores == b.backtest.holdout_scores
    w3 = sample_candidates(SearchSpec.model_validate(SEARCH), _bounds(), 3)
    w4 = sample_candidates(SearchSpec.model_validate(SEARCH), _bounds(), 4)
    assert not np.array_equal(w3, w4)


def test_insufficient_holdout_data():
    ev = _events()
    out = run_backtest_batch(
        _spec(),
        _window(),
        {"police": ev["police"]},
        SEARCH,
        now=NOW,
        bounds=_bounds(),
        grid=GRID,
    )
    assert not out.backtest.passed and "insufficient holdout data" in out.reason


def test_search_spec_validation():
    with pytest.raises(ValidationError):
        SearchSpec.model_validate({"layers": {"x": [1.0, 0.5]}})
    with pytest.raises(ValidationError):
        SearchSpec.model_validate({"layers": {"x": [0.0, 1.0]}, "samples": 4096})
    with pytest.raises(ValueError, match="unknown layers"):
        _run(search={"layers": {"nope": [0.0, 1.0]}, "samples": 8})


def test_rank_on_train_uses_priority_then_ties():
    train = {
        "authority_confirmation": np.array([0.5, 0.9, 0.9, np.nan]),
        "predictive_check": np.array([1.0, 0.1, 0.2, 1.0]),
    }
    order = rank_on_train(train, ["authority_confirmation", "predictive_check"])
    assert list(order) == [2, 1, 0, 3]


def test_array_backend_reports_cpu_without_gpu():
    xp, device = array_backend()
    assert device in {"cpu", "gpu"}
    if device == "cpu":
        assert xp is np


def test_evaluate_batch_matches_single_evaluate():
    spec = _spec()
    win = _window(swap_after_split=True)
    ctx = VerificationContext(
        layer_inputs=("crowd", "phones"),
        cell_m=25.0,
        events={k: cell_events(v, GRID) for k, v in _events().items()},
        authority_inputs=frozenset({"police"}),
        override_inputs=frozenset({"police"}),
    )
    rng = np.random.default_rng(1)
    w = rng.uniform(0, 2, size=(7, 2))
    batch = evaluate_batch(win, w, ctx)
    for i in range(len(w)):
        single = evaluate(win, w[i], ctx)
        for m, res in single.items():
            if res.has_data:
                assert batch[m][i] == pytest.approx(res.value)
            else:
                assert np.isnan(batch[m][i])
    assert spec.app_id  # spec only used for symmetry with run_backtest_batch
