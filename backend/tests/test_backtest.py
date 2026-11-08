import numpy as np
import pytest
from synthetic import LocalGrid, events_from_xy

from app.core.models import AppSpec
from app.engine.backtest import BacktestParams, accept, run_backtest

GRID = LocalGrid()  # 20 x 20 cells of 25 m
THREAT, DECOY = (137.5, 137.5), (387.5, 387.5)
NOW = 600.0


def _spec() -> AppSpec:
    (lat0, lat1), (lon0, lon1) = (
        GRID.to_latlon(np.array([0.0, 500.0]), np.array([0.0, 500.0]))[0],
        GRID.to_latlon(np.array([0.0, 500.0]), np.array([0.0, 500.0]))[1],
    )
    return AppSpec.model_validate(
        {
            "app_id": "bt-test",
            "intent": "synthetic backtest scenario",
            "status": "active",
            "grid": {"cell_m": 25.0, "bbox": {"min_lat": lat0, "min_lon": lon0, "max_lat": lat1, "max_lon": lon1}},
            "inputs": [
                {
                    "id": "crowd",
                    "kind": "push",
                    "shape": "point",
                    "tier": "flexible",
                    "meaning": "a press means danger nearby",
                    "operators": [{"op": "density", "weight": 1.0}],
                },
                {
                    "id": "news",
                    "kind": "pull",
                    "shape": "document",
                    "tier": "stiff",
                    "meaning": "local news",
                    "operators": [{"op": "event_points", "weight": 0.2}],
                },
                {
                    "id": "police",
                    "kind": "push",
                    "shape": "fixed_point",
                    "tier": "locked",
                    "meaning": "police marker",
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


def _events(seed=0, n_real=15, n_spoof=30):
    rng = np.random.default_rng(seed)
    crowd = []
    for k in range(n_real):  # real presses near the threat, every 20 s
        crowd += [(f"r{k}", float(t), *(np.array(THREAT) + rng.normal(0, 15, 2))) for t in range(5, 600, 20)]
    for k in range(n_spoof):  # coordinated fakes, stationary at a decoy
        crowd += [(f"s{k}", float(t), *DECOY) for t in range(5, 600, 20)]
    news = [("local-paper", 300.0, *THREAT)]
    police = [("officer", 560.0, *THREAT)]
    return {
        "crowd": events_from_xy(GRID, crowd),
        "news": events_from_xy(GRID, news),
        "police": events_from_xy(GRID, police),
    }


WEIGHTS = {"crowd.density": 1.0, "news.event_points": 0.2}
SPOOFERS_OUT = {f"s{k}": 0.0 for k in range(30)}
REAL_OUT = {f"r{k}": 0.1 for k in range(15)}


def test_dropping_spoofer_trust_passes_on_holdout():
    out = run_backtest(_spec(), _events(), now=NOW, weights=WEIGHTS, candidate_trust=SPOOFERS_OUT)
    bt = out.backtest
    assert bt.passed, out.reason
    assert out.deciding_method == "authority_confirmation"
    assert bt.holdout_scores["authority_confirmation"] > out.baseline_holdout["authority_confirmation"]
    assert bt.kind == "single" and bt.device == "cpu" and bt.candidates_tested == 1
    assert bt.train_scores and bt.elapsed_ms > 0
    assert bt.t.timestamp() == NOW


def test_lowering_real_users_fails():
    out = run_backtest(_spec(), _events(), now=NOW, weights=WEIGHTS, candidate_trust=REAL_OUT)
    assert not out.backtest.passed
    assert "did not improve" in out.reason


def test_candidate_weights_without_trust_change():
    good = run_backtest(_spec(), _events(), now=NOW, weights=WEIGHTS, candidate_weights={"news.event_points": 3.0})
    assert good.backtest.passed, good.reason
    bad = run_backtest(_spec(), _events(), now=NOW, weights=WEIGHTS, candidate_weights={"news.event_points": 0.0})
    assert not bad.backtest.passed


def test_insufficient_holdout_data():
    ev = _events(n_real=1, n_spoof=0)  # one press every 20 s: ~9 events in the last 180 s
    out = run_backtest(_spec(), ev, now=NOW, weights=WEIGHTS, candidate_trust=SPOOFERS_OUT)
    assert not out.backtest.passed
    assert out.reason.startswith("insufficient holdout data")
    assert out.backtest.holdout_scores == {}
    empty = {k: v.select(np.zeros(len(v), dtype=bool)) for k, v in _events().items()}
    assert run_backtest(_spec(), empty, now=NOW, weights=WEIGHTS).reason.startswith("insufficient holdout data")


def test_window_is_capped_and_split_by_time():
    # with a 300 s window the police arrival (t=560) is still in the holdout (510, 600]
    out = run_backtest(
        _spec(),
        _events(),
        now=NOW,
        weights=WEIGHTS,
        candidate_trust=SPOOFERS_OUT,
        params=BacktestParams(window_s=300.0),
    )
    assert out.backtest.passed, out.reason


def test_deterministic():
    a = run_backtest(_spec(), _events(), now=NOW, weights=WEIGHTS, candidate_trust=SPOOFERS_OUT)
    b = run_backtest(_spec(), _events(), now=NOW, weights=WEIGHTS, candidate_trust=SPOOFERS_OUT)
    assert a.backtest.holdout_scores == b.backtest.holdout_scores
    assert a.backtest.train_scores == b.backtest.train_scores


ORDER = ["authority_confirmation", "predictive_check", "cross_input_agreement"]


@pytest.mark.parametrize(
    ("base", "cand", "passed", "method"),
    [
        ({"predictive_check": 0.5}, {"predictive_check": 0.6}, True, "predictive_check"),
        ({"predictive_check": 0.5}, {"predictive_check": 0.5}, False, "predictive_check"),
        # the highest-priority method with data decides; a lower one may drop by up to ε
        (
            {"authority_confirmation": 0.2, "predictive_check": 0.9},
            {"authority_confirmation": 0.3, "predictive_check": 0.89},
            True,
            "authority_confirmation",
        ),
        # ... but not by more than ε
        (
            {"authority_confirmation": 0.2, "predictive_check": 0.9},
            {"authority_confirmation": 0.3, "predictive_check": 0.1},
            False,
            "authority_confirmation",
        ),
        # a lower method only one side has data for is not compared
        (
            {"authority_confirmation": 0.2, "cross_input_agreement": 0.9},
            {"authority_confirmation": 0.3},
            True,
            "authority_confirmation",
        ),
        # a higher method only the candidate has data for can't be compared: skipped
        ({"predictive_check": 0.5}, {"authority_confirmation": 0.1, "predictive_check": 0.6}, True, "predictive_check"),
        # losing a higher method's data rejects, even if a lower one improves
        (
            {"authority_confirmation": 0.4, "predictive_check": 0.5},
            {"predictive_check": 0.9},
            False,
            "authority_confirmation",
        ),
        ({}, {}, False, None),
    ],
)
def test_accept_rule(base, cand, passed, method):
    ok, m, reason = accept(base, cand, ORDER, epsilon=0.02)
    assert ok is passed and m == method and reason
