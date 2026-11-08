"""Instruction trace de-duplication (#169).

End-user pages recompute their instruction on every tick, so tracing every request floods the
authority trace with identical entries. `InstructionTraceDedup` remembers, per (app, grid cell of
the caller's position), the last outcome: whether the guard passed, the destination label, and the
failing checks with their reasons. A TraceEntry is written only when that outcome changes, so a
new destination, a newly failing check or a new failure reason is always traced.

The memory is a bounded LRU (`max_keys`), in process only. It holds grid cell indices, never a
raw position, and nothing from it is written to the trace (the trace still carries no position
and no text).
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Hashable

from app.core.models import Instruction

DEFAULT_MAX_KEYS = 4096

Outcome = tuple[bool, str | None, tuple[tuple[str, str], ...]]


def outcome_of(ins: Instruction) -> Outcome:
    label = ins.destination.label if ins.destination is not None else None
    failing = tuple((c.name, c.detail) for c in ins.guard.checks if not c.passed)
    return ins.guard.passed, label, failing


class InstructionTraceDedup:
    def __init__(self, max_keys: int = DEFAULT_MAX_KEYS) -> None:
        self.max_keys = max(1, int(max_keys))
        self._last: OrderedDict[Hashable, Outcome] = OrderedDict()
        self._lock = threading.Lock()

    def should_trace(self, key: Hashable, ins: Instruction) -> bool:
        """True if this outcome differs from the last one seen for `key` (and remembers it)."""
        outcome = outcome_of(ins)
        with self._lock:
            prev = self._last.get(key)
            self._last[key] = outcome
            self._last.move_to_end(key)
            while len(self._last) > self.max_keys:
                self._last.popitem(last=False)
        return prev != outcome

    def __len__(self) -> int:
        return len(self._last)
