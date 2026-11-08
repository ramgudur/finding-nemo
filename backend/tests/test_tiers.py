import numpy as np
import pytest

from app.engine.tiers import (
    FLEX_MIN_REF,
    WeightSlot,
    bounds,
    check_tier_change,
    check_update,
    flexible_total,
    renormalize_batch,
    stiff_reference,
    valid_mask,
)

NOW = 10_000.0


def make_slots(**overrides):
    slots = {
        "auth": WeightSlot("auth", "locked", 1.0),
        "news": WeightSlot("news", "stiff", 0.5),
        "crowd_a": WeightSlot("crowd_a", "flexible", 0.4),
        "crowd_b": WeightSlot("crowd_b", "flexible", 0.4),
        "crowd_c": WeightSlot("crowd_c", "flexible", 0.2),
    }
    slots.update(overrides)
    return list(slots.values())


def check(slots, proposed, **kw):
    kw.setdefault("now", NOW)
    kw.setdefault("methods_with_data", 2)
    return check_update(slots, proposed, total=1.0, **kw)


def test_flexible_sum_holds_and_stiff_locked_dont_move():
    slots = make_slots()
    assert flexible_total(slots) == pytest.approx(1.0)
    res = check(slots, {"crowd_a": 0.48})
    assert res.ok, res.reasons
    flex = res.weights["crowd_a"] + res.weights["crowd_b"] + res.weights["crowd_c"]
    assert flex == pytest.approx(1.0)
    assert res.weights["auth"] == 1.0 and res.weights["news"] == 0.5
    assert res.weights["crowd_a"] > 0.4


def test_flexible_step_over_25_percent_rejected():
    res = check(make_slots(), {"crowd_c": 0.4})  # +100% before renorm
    assert not res.ok
    assert any("crowd_c" in r and "out of bounds" in r for r in res.reasons)
    assert res.weights["crowd_c"] == 0.2  # unchanged on reject


def test_cold_start_rejects_flexible_with_reason():
    res = check(make_slots(), {"crowd_a": 0.42}, methods_with_data=1)
    assert not res.ok
    assert any("cold start" in r and "have 1" in r for r in res.reasons)


def test_locked_and_pinned_rejected():
    res = check(make_slots(), {"auth": 0.9})
    assert not res.ok and any("locked" in r for r in res.reasons)
    slots = make_slots(crowd_a=WeightSlot("crowd_a", "flexible", 0.4, pinned=True))
    res = check(slots, {"crowd_a": 0.42})
    assert not res.ok and any("pinned" in r for r in res.reasons)


def test_pinned_flexible_does_not_move_on_renormalization():
    slots = make_slots(crowd_a=WeightSlot("crowd_a", "flexible", 0.4, pinned=True))
    res = check(slots, {"crowd_b": 0.45})
    assert res.ok, res.reasons
    assert res.weights["crowd_a"] == 0.4
    assert res.weights["crowd_b"] + res.weights["crowd_c"] == pytest.approx(0.6)


def test_stiff_needs_evidence_and_hourly_cap():
    slots = make_slots()
    res = check(slots, {"news": 0.52})
    assert not res.ok and any("authority-confirmation" in r for r in res.reasons)
    assert check(slots, {"news": 0.54}, authority_evidence=True).ok
    assert not check(slots, {"news": 0.56}, authority_evidence=True).ok

    # The +/-10% is per hour vs. the weight an hour ago, not per step.
    hist = [(NOW - 7200, 0.5), (NOW - 1800, 0.54)]
    stiff = WeightSlot("news", "stiff", 0.54, history=hist)
    assert stiff_reference(stiff, NOW) == 0.5
    slots = make_slots(news=stiff)
    assert not check(slots, {"news": 0.58}, authority_evidence=True).ok  # +16% in the hour
    assert check(slots, {"news": 0.55}, authority_evidence=True).ok
    # an hour later the reference has moved up
    assert check(slots, {"news": 0.58}, authority_evidence=True, now=NOW + 1800).ok


def test_zero_flexible_weight_can_still_move():
    slots = make_slots(
        crowd_b=WeightSlot("crowd_b", "flexible", 0.6),
        crowd_c=WeightSlot("crowd_c", "flexible", 0.0),
    )
    res = check(slots, {"crowd_c": FLEX_MIN_REF * 0.25})
    assert res.ok, res.reasons
    assert res.weights["crowd_c"] > 0


def test_bad_inputs_rejected():
    slots = make_slots()
    assert not check(slots, {"nope": 1.0}).ok
    assert not check(slots, {"crowd_a": -0.1}).ok
    assert not check(slots, {"crowd_a": float("nan")}).ok
    assert check(slots, {"crowd_a": 0.4}).reasons == ["no change"]


def test_tier_changes_by_agents_rejected():
    ok, why = check_tier_change("flexible", "locked", actor="agent")
    assert not ok and "only the dev" in why
    assert check_tier_change("flexible", "flexible", actor="agent")[0]
    assert check_tier_change("stiff", "flexible", actor="dev")[0]
    with pytest.raises(ValueError):
        check_tier_change("bogus", "locked", actor="dev")


def test_batch_matches_single_check():
    slots = make_slots()
    b = bounds(slots, total=1.0, now=NOW, authority_evidence=True, methods_with_data=2)
    rng = np.random.default_rng(0)
    K = 256
    W = np.tile(b.current, (K, 1))
    W[:, 1:] *= rng.uniform(0.7, 1.3, (K, 4))  # perturb news + crowd
    Wn = renormalize_batch(W, b)
    np.testing.assert_allclose(Wn[:, 2:].sum(axis=1), 1.0)
    np.testing.assert_array_equal(Wn[:, :2], W[:, :2])  # locked/stiff untouched
    mask = valid_mask(Wn, b)
    assert 0 < mask.sum() < K
    for k in range(K):
        res = check(slots, dict(zip(b.layer_ids, W[k], strict=True)), authority_evidence=True)
        assert res.ok == bool(mask[k]), (k, res.reasons)
        if res.ok:
            np.testing.assert_allclose([res.weights[i] for i in b.layer_ids], Wn[k])


def test_batch_cold_start_freezes_flexible():
    b = bounds(make_slots(), total=1.0, now=NOW, methods_with_data=0)
    assert any("cold start" in r for r in b.frozen_reasons)
    W = np.array([b.current, b.current * [1, 1, 1.1, 1.0, 0.8]])
    mask = valid_mask(renormalize_batch(W, b), b)
    assert mask.tolist() == [True, False]


def test_batch_zero_flexible_row_is_invalid():
    b = bounds(make_slots(), total=1.0, now=NOW, methods_with_data=2)
    W = np.array([[1.0, 0.5, 0.0, 0.0, 0.0]])
    assert not valid_mask(renormalize_batch(W, b), b).any()
