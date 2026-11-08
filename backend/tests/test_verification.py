import numpy as np
import pytest
from synthetic import LocalGrid, events_from_xy

from app.engine.layer_cache import CacheWindow
from app.engine.verification import (
    CellEvents,
    VerificationContext,
    VerificationParams,
    cell_events,
    evaluate,
    methods_with_data,
    predictive_check,
    spearman,
)

H = W = 20
CELL_M = 25.0
A, B = (5, 5), (15, 15)


def blob(at, peak=1.0, sigma=1.5):
    i, j = np.indices((H, W))
    return peak * np.exp(-0.5 * ((i - at[0]) ** 2 + (j - at[1]) ** 2) / sigma**2)


def window(stacks, ids, t=None, overrides=None):
    """stacks: list over ticks of lists over layers."""
    layers = np.array([np.stack(s) for s in stacks], dtype=float)
    t = np.arange(len(stacks), dtype=float) * 2.0 if t is None else np.asarray(t, dtype=float)
    ov = np.zeros((len(stacks), H, W)) if overrides is None else overrides
    return CacheWindow(t=t, layers=layers, overrides=ov, layer_ids=tuple(ids))


def at_cell(cell, ts):
    ts = np.asarray(ts, dtype=float)
    return CellEvents(ts, np.full(len(ts), cell[0]), np.full(len(ts), cell[1]))


def ctx(layer_inputs, events, **kw):
    return VerificationContext(layer_inputs=tuple(layer_inputs), cell_m=CELL_M, events=events, **kw)


# -- predictive_check ---------------------------------------------------------------


def test_predictive_check_excludes_the_scored_input():
    # crowd's own layer sits exactly on its new events; news points elsewhere
    win = window([[blob(A), blob(B)]] * 10, ["crowd.density", "news.event_points"])
    c = ctx(["crowd", "news"], {"crowd": at_cell(A, [5.0, 6.0, 7.0])})
    r = predictive_check(win, np.array([1.0, 1.0]), c)
    assert r.has_data
    assert r.detail["per_input"]["crowd"] == 0.0  # it can't predict itself
    # once another input points at the same place, crowd's events are predicted
    win2 = window([[blob(A), blob(A)]] * 10, ["crowd.density", "news.event_points"])
    assert predictive_check(win2, np.array([1.0, 1.0]), c).value == 1.0


def test_predictive_check_single_input_scores_zero_and_weights_matter():
    win = window([[blob(A)]] * 5, ["crowd.density"])
    assert predictive_check(win, np.array([1.0]), ctx(["crowd"], {"crowd": at_cell(A, [3.0])})).value == 0.0
    # a second input that predicts crowd only counts if it has weight
    win = window([[blob(A), blob(A)]] * 5, ["crowd.density", "flow.flow_source"])
    c = ctx(["crowd", "flow"], {"crowd": at_cell(A, [3.0])})
    assert predictive_check(win, np.array([1.0, 0.0]), c).value == 0.0
    assert predictive_check(win, np.array([0.0, 1.0]), c).value == 1.0


def test_predictive_check_only_counts_events_within_horizon():
    win = window([[blob(B), blob(A)]] * 3, ["crowd.density", "news.event_points"], t=[0.0, 2.0, 4.0])
    c = ctx(["crowd", "news"], {"crowd": at_cell(A, [500.0])})  # far beyond the 60 s horizon
    assert not predictive_check(win, np.array([1.0, 1.0]), c).has_data


def test_override_mask_is_dropped_when_scoring_the_override_input():
    ov = np.zeros((4, H, W))
    ov[:, A[0], A[1]] = 1.0
    win = window([[blob(B)]] * 4, ["crowd.density"], overrides=ov)
    events = {"police": at_cell(A, [3.0]), "crowd": at_cell(A, [3.0])}
    c = ctx(["crowd"], events, override_inputs=frozenset({"police"}))
    r = predictive_check(win, np.array([1.0]), c)
    assert r.detail["per_input"]["police"] == 0.0  # its own override doesn't count
    assert r.detail["per_input"]["crowd"] == 1.0  # another input's override does


# -- authority_confirmation -----------------------------------------------------------


def test_authority_confirmation_rewards_early_and_close_peaks():
    # 150 ticks of 2 s; the crowd layer moves onto the police point halfway through the lookback
    t = np.arange(150) * 2.0
    stacks = [[blob(A) if ti >= 150.0 else blob(B), blob(B)] for ti in t]
    win = window(stacks, ["crowd.density", "news.event_points"], t=t)
    events = {"police": at_cell(A, [300.0])}
    c = ctx(["crowd", "news"], events, authority_inputs=frozenset({"police"}))
    res = evaluate(win, [1.0, 0.0], c)["authority_confirmation"]
    assert res.has_data and res.value == pytest.approx(0.5, abs=0.02)
    rep = res.detail["arrivals"][0]["layers"]
    assert rep["crowd.density"]["lead_s"] == pytest.approx(150.0)
    assert rep["crowd.density"]["distance_m"] == 0.0
    assert rep["news.event_points"]["lead_s"] is None
    assert rep["news.event_points"]["distance_m"] == pytest.approx(np.hypot(10, 10) * CELL_M)
    # weighting the wrong layer scores lower
    assert evaluate(win, [0.0, 1.0], c)["authority_confirmation"].value == 0.0


def test_authority_confirmation_needs_ticks_before_arrival():
    win = window([[blob(A)]] * 3, ["crowd.density"], t=[100.0, 102.0, 104.0])
    c = ctx(["crowd"], {"police": at_cell(A, [50.0])}, authority_inputs=frozenset({"police"}))
    assert not evaluate(win, [1.0], c)["authority_confirmation"].has_data


# -- cross_input_agreement -------------------------------------------------------------


def test_cross_input_agreement_is_weighted_by_pairs():
    rng = np.random.default_rng(0)
    near_a = blob(A) * (1 + 0.05 * rng.random((H, W)))
    win = window([[blob(A), near_a, blob(B)]] * 3, ["crowd.density", "flow.flow_source", "news.event_points"])
    c = ctx(["crowd", "flow", "news"], {})
    agree = evaluate(win, [1.0, 1.0, 0.01], c)["cross_input_agreement"]
    disagree = evaluate(win, [0.01, 1.0, 1.0], c)["cross_input_agreement"]
    assert agree.has_data and agree.value > 0.8
    assert disagree.value < agree.value
    assert agree.detail["pairs"]["crowd~flow"] > 0.9


def test_cross_input_agreement_needs_two_live_inputs():
    zero = np.zeros((H, W))
    win = window([[blob(A), blob(A), zero]] * 3, ["crowd.density", "crowd.dwell", "news.event_points"])
    r = evaluate(win, [1.0, 1.0, 1.0], ctx(["crowd", "crowd", "news"], {}))["cross_input_agreement"]
    assert not r.has_data  # two layers, but both from one input


def test_spearman_ties_and_constant():
    assert spearman(np.array([1.0, 2.0, 3.0]), np.array([10.0, 20.0, 30.0])) == pytest.approx(1.0)
    assert spearman(np.array([1.0, 1.0, 2.0]), np.array([2.0, 2.0, 1.0])) == pytest.approx(-1.0)
    assert spearman(np.ones(4), np.arange(4.0)) is None


# -- evaluate / cold start ---------------------------------------------------------------


def test_methods_with_data_for_cold_start():
    win = window([[blob(A), blob(A)]] * 5, ["crowd.density", "news.event_points"])
    c = ctx(["crowd", "news"], {"crowd": at_cell(A, [3.0])})
    res = evaluate(win, [1.0, 1.0], c)
    assert methods_with_data(res) == ["predictive_check", "cross_input_agreement"]
    assert res["authority_confirmation"].detail["reason"]
    with pytest.raises(ValueError):
        evaluate(win, [1.0], c)
    with pytest.raises(ValueError):
        evaluate(win, [1.0, 1.0], ctx(["crowd"], {}))


def test_cell_events_drops_out_of_grid():
    grid = LocalGrid()
    ev = events_from_xy(grid, [("u", 1.0, 62.5, 37.5), ("u", 2.0, -10.0, 10.0)])
    ce = cell_events(ev, grid)
    assert len(ce) == 1 and (ce.row[0], ce.col[0]) == (1, 2)


def test_params_are_configurable():
    win = window([[blob(B), blob(A)]] * 3, ["crowd.density", "news.event_points"], t=[0.0, 2.0, 4.0])
    c = ctx(["crowd", "news"], {"crowd": at_cell(A, [100.0])}, params=VerificationParams(horizon_s=120.0))
    assert predictive_check(win, np.array([1.0, 1.0]), c).value == 1.0
