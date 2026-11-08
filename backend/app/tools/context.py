"""What every tool handler gets: which app, who is calling, and the live platform objects.

Handlers never read time from anywhere but ``ctx.now()`` (the process Clock), and never write
state except through ``tools/guards.py`` checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.core import clock as clock_mod
from app.core.clock import Clock
from app.core.models import Actor, AgentName, AppSpec

if TYPE_CHECKING:
    from app.core.db import Db
    from app.engine.tick import AppRuntime, TickEngine


@dataclass
class TestedCandidate:
    """What a backtest actually tested, so ``apply_changes`` can apply exactly that and nothing else.

    Kept in process memory: after a restart the candidate is unknown and the agent must
    re-run the backtest (safe default)."""

    backtest_id: str
    app_id: str
    kind: str  # "weight" | "trust"
    passed: bool
    t: float
    baseline_weights: dict[str, float]
    baseline_trust: dict[str, float]  # the engine's trust map the backtest replayed with
    weights: dict[str, float]  # final (renormalized) weights tested; == baseline for trust candidates
    trust_changes: dict[str, tuple[float, float]]  # user_id -> (before, after)
    methods_with_data: int
    authority_evidence: bool
    reason: str
    used: bool = False


class CandidateStore:
    def __init__(self) -> None:
        self._by_id: dict[str, TestedCandidate] = {}

    def put(self, c: TestedCandidate) -> None:
        self._by_id[c.backtest_id] = c

    def get(self, backtest_id: str) -> TestedCandidate | None:
        return self._by_id.get(backtest_id)


CANDIDATES = CandidateStore()


@dataclass
class ToolContext:
    app_id: str
    db: Db
    engine: TickEngine
    actor: Actor = "agent"
    agent: AgentName | None = None
    clock: Clock | None = None
    candidates: CandidateStore = field(default_factory=lambda: CANDIDATES)
    extra: dict[str, Any] = field(default_factory=dict)

    def now(self) -> float:
        return (self.clock or clock_mod.get_clock()).now()

    def now_dt(self) -> datetime:
        return datetime.fromtimestamp(self.now(), UTC)

    @property
    def runtime(self) -> AppRuntime:
        rt = self.engine.apps.get(self.app_id)
        if rt is None:
            raise LookupError(f"app {self.app_id!r} is not running (not active?)")
        return rt

    @property
    def spec(self) -> AppSpec:
        return self.runtime.spec
