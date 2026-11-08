"""Benchmark: Nemotron rate budget under load, before vs after #221 (admission + scaled intervals).

    cd backend && python tests/bench_rate_budget.py [--apps 1 2 3 5] [--minutes 10]

Simulates several apps' Critic and POI Labeler scheduling (scheduled checks + triggered events)
and a dev's compile attempts, all sharing one provider's token bucket (default 40 RPM, matching
the live free-tier budget CLAUDE.md documents). "before" never calls `budget` at all (every
scheduled/triggered reason runs immediately, like the code before #221); "after" uses
`LlmRouter.admit` to defer a run the bucket can't cover, and `budget.scaled_interval` to stretch
scheduled checks with the app count -- the actual mechanism in `app.agents.budget` and the
admission checks in `critic.py`/`poi_labeler.py`/`scout.py`.

Deliberately standalone from `engine.tick`/`AppRuntime` (see `bench_tick.py` for that machinery):
this benchmarks the admission mechanism itself, not full tick/agent integration, so the scheduling
state machine below is a direct, minimal re-implementation of each scheduler's reason/priority
logic against a raw token bucket. Single-threaded and synchronous, so "after" mode never races
two runs for the same tokens -- consistent with asyncio's cooperative scheduling for the real
code, modulo network latency gaps this benchmark doesn't model.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent)]

from app.agents import budget  # noqa: E402
from app.agents.llm import _TokenBucket  # noqa: E402

DT_S = 2.0  # poll granularity, matches the engine tick / scheduler poll_s
TRIGGER_COOLDOWN_S = 15.0
RPM = 40.0  # one shared provider bucket across every agent, the documented free-tier budget

# a background agent's synthetic cadence per app: a "trigger" (new hotspot, persistence, etc.)
TRIGGER_INTERVAL_S = {"critic": 25.0, "poi": 35.0}
MIN_INTERVAL_S = {"critic": 60.0, "poi": 60.0}
COMPILE_INTERVAL_S = 45.0  # a dev iterating on one app's spec


@dataclass
class _AgentState:
    last_run_at: float = float("-inf")
    last_triggered_at: float = float("-inf")
    pending_trigger: str | None = None


@dataclass
class _Stats:
    calls_ok: int = 0
    calls_wasted: int = 0  # spent on an episode that then failed to finish
    compiles_attempted: int = 0
    compiles_ok: int = 0


def _spend(bucket: _TokenBucket, n: int, stats: _Stats) -> bool:
    """Try to take `n` tokens one at a time, as a real multi-call episode would. Returns whether
    every call succeeded; tokens already taken before a failure are wasted (rule 2: the episode
    produces nothing, but the budget it spent getting there is gone)."""
    taken = 0
    for _ in range(n):
        if not bucket.try_take():
            break
        taken += 1
    ok = taken == n
    if ok:
        stats.calls_ok += taken
    else:
        stats.calls_wasted += taken
    return ok


def simulate(n_apps: int, minutes: float, gated: bool) -> _Stats:
    t = [0.0]
    bucket = _TokenBucket(RPM, clock=lambda: t[0])
    stats = _Stats()
    agents = {(app, kind): _AgentState() for app in range(n_apps) for kind in ("critic", "poi")}
    # phase-offset each app so they don't all tick in lockstep (more realistic, avoids an artifact
    # where every app's cadence aligns and either starves or never contends)
    phase = {app: (app * 7.0) % 20.0 for app in range(n_apps)}

    total_s = minutes * 60.0
    now = 0.0
    while now < total_s:
        t[0] = now
        for app in range(n_apps):
            for kind in ("critic", "poi"):
                st = agents[(app, kind)]
                triggered = (now + phase[app]) % TRIGGER_INTERVAL_S[
                    kind
                ] < DT_S  # a trigger fires roughly on this cadence
                reason: str | None = None
                priority: budget.Priority | None = None
                if st.pending_trigger is not None and now - st.last_triggered_at >= TRIGGER_COOLDOWN_S:
                    reason, priority = st.pending_trigger, "triggered"
                elif triggered and now - st.last_triggered_at >= TRIGGER_COOLDOWN_S:
                    reason, priority = "triggered", "triggered"
                elif now - st.last_run_at >= (
                    budget.scaled_interval(MIN_INTERVAL_S[kind], n_apps, kind, RPM) if gated else MIN_INTERVAL_S[kind]
                ):
                    reason, priority = "scheduled check", "scheduled"
                if reason is None:
                    continue
                if gated:
                    if bucket.available() < budget.EST_CALLS[kind] + budget.HEADROOM[priority] * bucket.capacity:
                        if priority == "triggered":
                            st.pending_trigger = reason
                        continue  # deferred, not skipped: no tokens spent, try again next poll
                st.pending_trigger = None
                if priority == "triggered":
                    st.last_triggered_at = now
                st.last_run_at = now
                _spend(bucket, budget.EST_CALLS[kind], stats)
            # one dev iterating on this app's spec, independent of the scheduler priorities above
            if (now + phase[app]) % COMPILE_INTERVAL_S < DT_S:
                stats.compiles_attempted += 1
                if _spend(bucket, budget.EST_CALLS["compiler"], stats):
                    stats.compiles_ok += 1
        now += DT_S
    return stats


def run(n_apps: int, minutes: float) -> dict:
    before = simulate(n_apps, minutes, gated=False)
    after = simulate(n_apps, minutes, gated=True)
    return {"n_apps": n_apps, "minutes": minutes, "before": before, "after": after}


def _row(label: str, s: _Stats, minutes: float) -> str:
    rate = s.compiles_ok / s.compiles_attempted if s.compiles_attempted else 1.0
    return (
        f"  {label:<7} calls/min {s.calls_ok / minutes:6.1f}  wasted {s.calls_wasted:4d}  "
        f"compile success {rate * 100:5.1f}% ({s.compiles_ok}/{s.compiles_attempted})"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apps", type=int, nargs="+", default=[1, 2, 3, 5])
    ap.add_argument("--minutes", type=float, default=10.0)
    a = ap.parse_args()
    print(f"shared bucket: {RPM:.0f} RPM, {a.minutes:.0f} simulated minutes per run\n")
    for n in a.apps:
        r = run(n, a.minutes)
        print(f"{n} app{'s' if n != 1 else ''}:")
        print(_row("before", r["before"], a.minutes))
        print(_row("after", r["after"], a.minutes))


if __name__ == "__main__":
    main()
