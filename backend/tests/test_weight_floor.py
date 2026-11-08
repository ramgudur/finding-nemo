"""#211: a flexible weight never drops below 10% of its creation-time value (single and batch)."""

import numpy as np
import pytest

from app.engine import tiers
from app.engine.backtest import SearchSpec, sample_candidates


def _slots(a=0.5, b=0.5, created=(0.5, 0.5)):
    return [
        tiers.WeightSlot("a.density", "flexible", a, created_weight=created[0]),
        tiers.WeightSlot("b.density", "flexible", b, created_weight=created[1]),
    ]


def test_floor_is_ten_percent_of_the_creation_weight():
    lo, hi, why = tiers.slot_bounds(_slots(b=0.06)[1], now=0.0, authority_evidence=False, methods_with_data=3)
    assert why is None
    assert lo == pytest.approx(0.05)  # 10% of 0.5, above the -25% step (0.045)
    assert hi == pytest.approx(0.075)


def test_single_update_below_the_floor_is_rejected():
    slots = _slots(a=0.94, b=0.06)
    ok = tiers.check_update(slots, {"a.density": 0.945, "b.density": 0.055}, total=1.0, now=0.0, methods_with_data=3)
    assert ok.ok
    low = tiers.check_update(slots, {"a.density": 0.955, "b.density": 0.045}, total=1.0, now=0.0, methods_with_data=3)
    assert not low.ok and any("b.density" in r for r in low.reasons)


def test_batch_search_drops_candidates_below_the_floor():
    slots = _slots(a=0.94, b=0.06)
    b = tiers.bounds(slots, total=1.0, now=0.0, methods_with_data=3)
    search = SearchSpec.model_validate({"layers": {"a.density": [0.9, 1.0], "b.density": [0.0, 0.1]}, "samples": 400})
    w = sample_candidates(search, b, seed=0)
    valid = tiers.valid_mask(w, b)
    assert valid.any() and not valid.all()
    j = list(b.layer_ids).index("b.density")
    assert (w[valid, j] >= 0.05 - 1e-9).all()  # every surviving candidate respects the floor


def test_a_weight_already_below_the_floor_can_still_rise():
    lo, hi, _ = tiers.slot_bounds(_slots(b=0.02)[1], now=0.0, authority_evidence=False, methods_with_data=3)
    assert lo == pytest.approx(0.02) and hi > 0.02  # may stay or rise, never fall further
    slots = _slots(a=0.98, b=0.02)
    b = tiers.bounds(slots, total=1.0, now=0.0, methods_with_data=3)
    assert tiers.valid_mask(np.array([[0.98, 0.02]]), b).all()  # leaving it unchanged stays valid


def test_no_creation_weight_no_floor():
    s = tiers.WeightSlot("x.density", "flexible", 0.04)
    lo, _, _ = tiers.slot_bounds(s, now=0.0, authority_evidence=False, methods_with_data=3)
    assert lo == pytest.approx(max(0.0, 0.04 - tiers.FLEX_MAX_REL_PER_STEP * max(0.04, tiers.FLEX_MIN_REF)))
    assert np.isfinite(lo)
