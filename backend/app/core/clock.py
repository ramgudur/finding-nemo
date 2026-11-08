"""Clock: the only time source for the engine and guards.

Everything time-dependent (engine tick, decay, guards' rate limits, trust caps)
reads time from the process clock returned by ``get_clock()``. Never call
``time.time()`` or ``datetime.now()`` directly in engine or guard code.

Times are UTC. Internally a clock speaks POSIX seconds (float); helpers convert
to and from ISO-8601 at the boundaries.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Anything that can tell the current time in POSIX seconds (UTC)."""

    def now(self) -> float: ...


class WallClock:
    """Real time."""

    def now(self) -> float:
        return time.time()


class SimClock:
    """Simulated time.

    Two modes:
    - ``speed=None`` (manual): time moves only via ``advance()`` / ``set()``.
      Use this in tests for fully deterministic time.
    - ``speed=k``: time runs ``k`` times faster than wall time from ``start``.
      ``advance()`` / ``set()`` still work and shift the timeline.
    """

    def __init__(self, start: float | datetime | str = 0.0, speed: float | None = None) -> None:
        if speed is not None and speed <= 0:
            raise ValueError("speed must be > 0")
        self._lock = threading.Lock()
        self._sim_at_anchor = to_seconds(start)
        self._wall_anchor = time.monotonic()
        self._speed = speed

    @property
    def speed(self) -> float | None:
        return self._speed

    def now(self) -> float:
        with self._lock:
            if self._speed is None:
                return self._sim_at_anchor
            return self._sim_at_anchor + (time.monotonic() - self._wall_anchor) * self._speed

    def advance(self, seconds: float) -> float:
        if seconds < 0:
            raise ValueError("SimClock cannot go backwards; use set() explicitly")
        with self._lock:
            self._rebase()
            self._sim_at_anchor += seconds
            return self._sim_at_anchor

    def set(self, t: float | datetime | str) -> None:
        with self._lock:
            self._sim_at_anchor = to_seconds(t)
            self._wall_anchor = time.monotonic()

    def set_speed(self, speed: float | None) -> None:
        if speed is not None and speed <= 0:
            raise ValueError("speed must be > 0")
        with self._lock:
            self._rebase()
            self._speed = speed

    def _rebase(self) -> None:
        # Fold elapsed wall time into the sim anchor so later changes start from "now".
        if self._speed is not None:
            wall = time.monotonic()
            self._sim_at_anchor += (wall - self._wall_anchor) * self._speed
            self._wall_anchor = wall


# Process-wide clock ---------------------------------------------------------

_clock: Clock = WallClock()
_clock_lock = threading.Lock()


def get_clock() -> Clock:
    return _clock


def set_clock(clock: Clock) -> Clock:
    """Install ``clock`` as the process clock; returns the previous one."""
    global _clock
    with _clock_lock:
        prev, _clock = _clock, clock
    return prev


def now() -> float:
    """Current time in POSIX seconds from the process clock."""
    return _clock.now()


def now_iso() -> str:
    return to_iso(_clock.now())


# Conversions -----------------------------------------------------------------


def to_seconds(t: float | datetime | str) -> float:
    """POSIX seconds from seconds, an aware/naive-UTC datetime, or an ISO-8601 string."""
    if isinstance(t, bool):
        raise TypeError("bool is not a time")
    if isinstance(t, int | float):
        return float(t)
    if isinstance(t, str):
        t = from_iso(t)
    if isinstance(t, datetime):
        if t.tzinfo is None:
            t = t.replace(tzinfo=UTC)
        return t.timestamp()
    raise TypeError(f"unsupported time value: {t!r}")


def from_iso(s: str) -> datetime:
    """Parse ISO-8601 (accepts a trailing 'Z'); naive values are taken as UTC."""
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_iso(t: float | datetime) -> str:
    """UTC ISO-8601 with a 'Z' suffix and millisecond precision."""
    dt = datetime.fromtimestamp(to_seconds(t), tz=UTC)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
