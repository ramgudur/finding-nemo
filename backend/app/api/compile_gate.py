"""Per-app compile cooldown for `POST /v1/apps/{app}/compile` (#169).

`/compile` takes no key, and every run spends the shared Nemotron rate budget. So, per app:
at most one compile in flight, and at least `min_gap_s` seconds between the end of one compile
and the start of the next. A refused request gets `429` with `Retry-After` (whole seconds).

This is abuse protection on wall time (`time.monotonic`), not engine logic, so it doesn't read
`core/clock.py`: a simulated clock must never freeze or skip the cooldown. In memory only; a
restart clears it. Memory stays bounded: finished entries older than the gap are pruned.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Refusal:
    detail: str
    retry_after_s: int


class CompileGate:
    def __init__(self, min_gap_s: float, now: Callable[[], float] = time.monotonic) -> None:
        self.min_gap_s = max(0.0, float(min_gap_s))
        self._now = now
        self._lock = threading.Lock()
        self._in_flight: set[str] = set()
        self._finished_at: dict[str, float] = {}

    def try_acquire(self, app_id: str) -> Refusal | None:
        """None if this app may compile now (and marks it in flight), else why not."""
        with self._lock:
            now = self._now()
            self._finished_at = {a: t for a, t in self._finished_at.items() if now - t < self.min_gap_s}
            if app_id in self._in_flight:
                return Refusal("a compile for this app is already running", max(1, math.ceil(self.min_gap_s)))
            last = self._finished_at.get(app_id)
            if last is not None:
                wait = self.min_gap_s - (now - last)
                return Refusal(
                    f"compiles for one app must be at least {self.min_gap_s:g} s apart",
                    max(1, math.ceil(wait)),
                )
            self._in_flight.add(app_id)
            return None

    def release(self, app_id: str) -> None:
        with self._lock:
            self._in_flight.discard(app_id)
            if self.min_gap_s > 0:
                self._finished_at[app_id] = self._now()
