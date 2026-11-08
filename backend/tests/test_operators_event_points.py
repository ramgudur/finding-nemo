import pytest
from synthetic import FixedClock, LocalGrid, events_from_xy

from app.engine.operators.event_points import EventPointsParams, event_points

GRID = LocalGrid()
FIELDS = ("confidence", "doc_id")
C = (262.5, 262.5)  # center of cell (10, 10)


def _run(rows, now=1000.0, params=None, trust=None):
    ev = events_from_xy(GRID, rows, FIELDS)
    return event_points(ev, params or EventPointsParams(), trust, FixedClock(now), GRID)


def test_confidence_times_reliability():
    assert _run([("news-a", 1000.0, *C, 1.0, "d1")]).layer[10, 10] == pytest.approx(1.0)
    r = _run([("news-a", 1000.0, *C, 0.5, "d1")], trust={"news-a": 0.6})
    assert r.layer[10, 10] == pytest.approx(0.3)
    # missing or junk confidence defaults to 1; out-of-range is clipped
    assert _run([("news-a", 1000.0, *C, None, "d1")]).layer[10, 10] == pytest.approx(1.0)
    assert _run([("news-a", 1000.0, *C, "high", "d1")]).layer[10, 10] == pytest.approx(1.0)
    assert _run([("news-a", 1000.0, *C, 7.0, "d1")]).layer[10, 10] == pytest.approx(1.0)


def test_decays_with_half_life():
    r = _run([("news-a", 1000.0 - 1800.0, *C, 1.0, "d1")], now=1000.0)
    assert r.layer[10, 10] == pytest.approx(0.5)


def test_same_doc_from_same_source_counts_once():
    rows = [("news-a", 900.0, *C, 1.0, "d1"), ("news-a", 1000.0, *C, 1.0, "d1")]
    r = _run(rows)
    assert r.extras["n_docs"] == 1 and r.layer[10, 10] == pytest.approx(1.0)
    # another source reporting the same story counts separately; so do docs without an id
    rows = [("news-a", 1000.0, *C, 1.0, "d1"), ("news-b", 1000.0, *C, 1.0, "d1"), ("news-a", 1000.0, *C, 1.0, None)]
    assert _run(rows).extras["n_docs"] == 3


def test_single_doc_stays_under_noise_floor():
    r = _run([("news-a", 1000.0, *C, 1.0, "d1")])
    assert r.layer.max() < EventPointsParams().noise_floor
