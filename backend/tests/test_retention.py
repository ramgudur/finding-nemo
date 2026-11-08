"""Data retention (#213): old events and trace go, everything still needed stays. Synthetic data."""

import asyncio
import math
from datetime import UTC, datetime

from app.core.clock import SimClock
from app.core.db import Db
from app.core.models import AppSpec, Change, Event, TraceEntry
from app.engine import retention
from app.engine.retention import REPLAY_WINDOW_S, RetentionRunner, event_keep_s, prune_once
from app.engine.tick import TickEngine, tick_app
from app.main import DbEventSource

BBOX = {"min_lat": 45.0, "min_lon": 10.0, "max_lat": 45.0045, "max_lon": 10.00635}
NOW = 2_000_000_000.0  # 2033; far enough from the epoch for 100-day-old rows
DAY = 86_400.0


def _dt(t: float) -> datetime:
    return datetime.fromtimestamp(t, tz=UTC)


def _spec() -> AppSpec:
    point = {"kind": "push", "shape": "point", "tier": "flexible", "meaning": "a report"}
    return AppSpec.model_validate(
        {
            "app_id": "ret-app",
            "intent": "test",
            "status": "active",
            "grid": {"cell_m": 10, "bbox": BBOX},
            "inputs": [
                {**point, "id": "pings", "operators": [{"op": "density", "weight": 1.0, "half_life_s": 60}]},
                {
                    "id": "marker",
                    "kind": "push",
                    "shape": "fixed_point",
                    "tier": "locked",
                    "meaning": "an authority point",
                    "operators": [{"op": "override", "weight": 0, "radius_m": 30}],  # no ttl: needs all history
                },
                {
                    **point,
                    "id": "feed",
                    "kind": "pull",
                    "connector": {"type": "rss", "config": {"url": "https://example.com/feed.xml"}},
                    "operators": [{"op": "density", "weight": 1.0, "half_life_s": 60}],
                },
            ],
        }
    )


def _env():
    db = Db(":memory:")
    spec = _spec()
    db.upsert_app(spec, NOW)
    eng = TickEngine(DbEventSource(db), clock=SimClock(NOW))
    rt = eng.add_app(spec)
    return db, eng, rt


def _add(db: Db, input_id: str, ages_s: list[float]) -> None:
    for i, age in enumerate(ages_s):
        ev = Event(user_id=f"u{i}", lat=45.002, lon=10.003, ts=_dt(NOW - age))
        db.insert_event("ret-app", input_id, ev, NOW - age)


def _ages(db: Db, input_id: str) -> list[float]:
    return sorted(round(NOW - e.ts.timestamp()) for e in db.list_events("ret-app", input_id))


def test_keep_window_covers_horizon_backtest_and_floor():
    _, eng, rt = _env()
    back = rt.input_lookback("pings", eng.lookback_s)
    keep = event_keep_s(rt, "pings", eng.lookback_s, floor_s=0)
    assert keep >= back + REPLAY_WINDOW_S  # the backtest reads window_s + horizon back
    assert event_keep_s(rt, "pings", eng.lookback_s, floor_s=DAY) == DAY
    assert event_keep_s(rt, "marker", eng.lookback_s, floor_s=0) == math.inf


def test_old_push_events_go_needed_ones_stay():
    db, eng, rt = _env()
    keep = event_keep_s(rt, "pings", eng.lookback_s, floor_s=0)
    _add(db, "pings", [10, 300, keep - 1, keep + 1, 10 * keep])
    stats = prune_once(db, eng, event_floor_s=0)
    assert _ages(db, "pings") == [10, 300, round(keep - 1)]
    assert stats.events_deleted == {"ret-app/pings": 2}
    # the tick and the /raw-style read still see the recent events
    r = tick_app(rt, eng.source, now=NOW, clock=eng.clock)
    assert not r.errors and float(r.score.max()) > 0
    assert len(db.list_events("ret-app", "pings", since=NOW - 3600)) == 3


def test_floor_keeps_a_day_by_default():
    db, eng, _ = _env()
    _add(db, "pings", [3600, DAY - 60, DAY + 60])
    prune_once(db, eng)
    assert _ages(db, "pings") == [3600, round(DAY - 60)]


def test_all_history_and_pull_inputs_are_never_pruned():
    db, eng, _ = _env()
    _add(db, "marker", [10 * DAY])  # an override with no ttl stays active until cleared (rule 6)
    _add(db, "feed", [10 * DAY])  # connectors dedupe documents against stored events
    prune_once(db, eng, event_floor_s=0)
    assert _ages(db, "marker") == [10 * DAY]
    assert _ages(db, "feed") == [10 * DAY]


def test_apps_not_running_keep_their_events():
    db, eng, _ = _env()
    _add(db, "pings", [10 * DAY])
    eng.remove_app("ret-app")  # no runtime: horizons unknown, so nothing is pruned
    prune_once(db, eng, event_floor_s=0)
    assert _ages(db, "pings") == [10 * DAY]


def _trace(db: Db, t: float, agent: str = "critic", action: str = "annotate", saw: str = "") -> None:
    db.add_trace(TraceEntry(t=_dt(t), app_id="ret-app", agent=agent, action=action, saw=saw))


def test_trace_keeps_the_newest_n():
    db, eng, _ = _env()
    for k in range(10):
        _trace(db, NOW - 100 + k)
    stats = prune_once(db, eng, trace_keep=4)
    kept = [round(e.t.timestamp() - NOW) for e in db.list_trace("ret-app")]
    assert kept == [-94, -93, -92, -91]
    assert stats.trace_deleted == {"ret-app": 6}
    assert prune_once(db, eng, trace_keep=4).trace_deleted == {}  # nothing more to do


def test_trace_never_drops_a_pending_scout_request():
    db, eng, _ = _env()
    _trace(db, NOW - 200, agent="compiler", action="request_scout", saw="feed")  # never handled
    for k in range(10):
        _trace(db, NOW - 100 + k)
    prune_once(db, eng, trace_keep=2)
    from app.agents.scout import pending_requests

    assert [e.saw for e in pending_requests(db, "ret-app")] == ["feed"]
    assert len(db.list_trace("ret-app")) == 11  # the request and everything after it stay
    # once handled, the old entries can go
    _trace(db, NOW - 50, agent="scout", action="request_handled", saw="feed")
    prune_once(db, eng, trace_keep=2)
    assert pending_requests(db, "ret-app") == []
    assert len(db.list_trace("ret-app")) == 2


def test_changes_are_never_pruned():
    db, eng, _ = _env()
    c = Change(
        id="c-old",
        app_id="ret-app",
        kind="weight",
        target="pings.density",
        before={"weight": 1.0},
        after={"weight": 0.9},
        reason="old change",
        actor="agent",
        t=_dt(NOW - 100 * DAY),
    )
    db.add_change(c)
    _add(db, "pings", [100 * DAY])
    prune_once(db, eng, event_floor_s=0, trace_keep=1)
    assert [x.id for x in db.list_changes("ret-app")] == ["c-old"]


def test_runner_runs_in_the_background_and_can_be_off():
    db, eng, _ = _env()
    _add(db, "pings", [10 * DAY])
    off = RetentionRunner(db, eng, interval_s=0)

    async def main():
        assert off.start() is None
        runner = RetentionRunner(db, eng, interval_s=0.01, event_floor_s=0)
        runner.start()
        for _ in range(200):
            if not db.list_events("ret-app", "pings"):
                break
            await asyncio.sleep(0.01)
        await runner.stop()

    asyncio.run(main())
    assert _ages(db, "pings") == []


def test_a_failing_pass_does_not_stop_the_runner(monkeypatch):
    db, eng, _ = _env()
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise RuntimeError("db busy")

    monkeypatch.setattr(retention, "prune_once", boom)

    async def main():
        runner = RetentionRunner(db, eng, interval_s=0.01)
        runner.start()
        for _ in range(200):
            if len(calls) >= 2:
                break
            await asyncio.sleep(0.01)
        await runner.stop()

    asyncio.run(main())
    assert len(calls) >= 2
