import numpy as np
from synthetic import FixedClock, LocalGrid, events_from_xy

from app.engine.combine import combine
from app.engine.operators import registry
from app.engine.operators.override import OverrideParams, override

GRID = LocalGrid()
FIELDS = ("cleared",)


def _run(rows, now=100.0, params=None, trust=None):
    ev = events_from_xy(GRID, rows, FIELDS)
    return override(ev, params or OverrideParams(), trust, FixedClock(now), GRID)


def test_mark_covers_radius_until_cleared():
    r = _run([("room12", 10.0, 262.5, 262.5, None)])
    assert r.layer[10, 10] == 1.0
    assert r.layer[10, 11] == 1.0 and r.layer[12, 10] == 1.0  # 25 m, 50 m away
    assert r.layer[10, 13] == 0.0  # 75 m away
    assert set(np.unique(r.layer)) == {0.0, 1.0}
    assert len(r.extras["active"]) == 1 and r.extras["override"] is True

    cleared = _run([("room12", 10.0, 262.5, 262.5, None), ("room12", 50.0, 270.0, 260.0, True)])
    assert not cleared.layer.any() and cleared.extras["active"] == []


def test_clear_far_away_or_in_the_future_keeps_the_mark():
    far = _run([("a", 10.0, 262.5, 262.5, False), ("b", 50.0, 62.5, 62.5, True)])
    assert far.layer[10, 10] == 1.0
    future = _run([("a", 10.0, 262.5, 262.5, None), ("a", 150.0, 262.5, 262.5, True)])
    assert future.layer[10, 10] == 1.0


def test_mark_after_clear_is_active_again():
    rows = [("a", 10.0, 262.5, 262.5, None), ("a", 20.0, 262.5, 262.5, True), ("a", 30.0, 262.5, 262.5, None)]
    assert _run(rows).layer[10, 10] == 1.0


def test_ttl_and_small_radius():
    assert not _run([("a", 10.0, 262.5, 262.5, None)], params=OverrideParams(ttl_s=60)).layer.any()
    tiny = _run([("a", 99.0, 255.0, 255.0, None)], params=OverrideParams(radius_m=1.0))
    assert tiny.layer.sum() == 1.0 and tiny.layer[10, 10] == 1.0


def test_zero_trust_marks_are_ignored():
    assert not _run([("fake", 10.0, 262.5, 262.5, None)], trust={"fake": 0.0}).layer.any()


def test_registry_role_and_combine_override():
    assert registry.get("override").role == "override"
    assert registry.get("density").role == "layer"
    mask = _run([("a", 10.0, 62.5, 62.5, None)]).layer
    res = combine([np.zeros(GRID.shape)], [1.0], [1.0], override=mask)
    assert res.score[2, 2] == 1.0 and res.score[15, 15] == 0.0
