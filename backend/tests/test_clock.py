from datetime import UTC, datetime

import pytest

from app.core import clock
from app.core.clock import SimClock, WallClock, from_iso, get_clock, set_clock, to_iso, to_seconds


def test_manual_sim_clock_only_moves_on_advance():
    c = SimClock(start=1000.0)
    assert c.now() == 1000.0
    assert c.now() == 1000.0
    assert c.advance(2.5) == 1002.5
    assert c.now() == 1002.5
    c.set("2026-11-07T00:00:00Z")
    assert c.now() == datetime(2026, 11, 7, tzinfo=UTC).timestamp()


def test_sim_clock_rejects_going_backwards_and_bad_speed():
    c = SimClock(start=0.0)
    with pytest.raises(ValueError):
        c.advance(-1)
    with pytest.raises(ValueError):
        SimClock(speed=0)
    with pytest.raises(ValueError):
        c.set_speed(-2)


def test_sim_clock_speed_runs_faster_than_wall(monkeypatch):
    wall = [100.0]
    monkeypatch.setattr(clock.time, "monotonic", lambda: wall[0])
    c = SimClock(start=0.0, speed=10.0)
    wall[0] += 1.5
    assert c.now() == pytest.approx(15.0)
    c.set_speed(None)  # freeze at current sim time
    wall[0] += 100
    assert c.now() == pytest.approx(15.0)
    c.advance(5)
    assert c.now() == pytest.approx(20.0)


def test_process_clock_swap_and_restore():
    sim = SimClock(start=42.0)
    prev = set_clock(sim)
    try:
        assert get_clock() is sim
        assert clock.now() == 42.0
        assert clock.now_iso() == "1970-01-01T00:00:42.000Z"
    finally:
        set_clock(prev)
    assert isinstance(get_clock(), WallClock)


def test_iso_round_trip_is_utc():
    t = to_seconds("2026-11-07T12:34:56.789Z")
    assert to_iso(t) == "2026-11-07T12:34:56.789Z"
    assert from_iso("2026-11-07T07:34:56-05:00") == from_iso("2026-11-07T12:34:56Z")
    assert to_seconds("2026-11-07T12:00:00") == to_seconds("2026-11-07T12:00:00+00:00")  # naive = UTC
    assert to_seconds(datetime(2026, 1, 1)) == datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    with pytest.raises(TypeError):
        to_seconds(True)
