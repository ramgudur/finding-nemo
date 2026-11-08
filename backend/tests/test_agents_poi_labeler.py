import asyncio

import numpy as np
import pytest
from fake_llm import FakeAsyncOpenAI

from app.agents import poi_labeler as pl
from app.agents.llm import LlmRouter
from app.agents.poi_labeler import (
    PERSIST_TRIGGER_TICKS,
    PoiLabelerScheduler,
    _AppState,
    persist_trigger,
    run_poi_labeler,
    task_prompt,
)
from app.core.clock import SimClock
from app.core.config import Settings
from app.core.db import Db
from app.core.models import AppSpec
from app.engine.tick import InMemoryEventSource, TickEngine
from app.tools import geo_tools
from app.tools.context import ToolContext

T0 = 1_800_000_000.0
BBOX = {"min_lat": 0.0, "min_lon": 0.0, "max_lat": 0.005, "max_lon": 0.005}  # ~550 m


def _spec(mode="on", labels=("threat origin", "gathering point")) -> AppSpec:
    return AppSpec.model_validate(
        {
            "app_id": "poi-app",
            "intent": "find where entities stop and stay",
            "status": "active",
            "grid": {"cell_m": 25, "bbox": BBOX},
            "inputs": [
                {
                    "id": "tracks",
                    "kind": "push",
                    "shape": "track",
                    "tier": "flexible",
                    "meaning": "position pings",
                    "operators": [{"op": "dwell", "weight": 1.0}],
                }
            ],
            "outputs": {
                "layers": [{"id": "tracks.dwell", "visibility": "authority"}],
                "poi_labels": {"mode": mode, "label_set": [{"label": lab} for lab in labels], "min_confidence": 0.6},
            },
        }
    )


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def fake_fetch(query):
        return {"elements": [{"type": "node", "id": 1, "lat": 0.0025, "lon": 0.0025, "tags": {"name": "Hall"}}]}

    monkeypatch.setattr(geo_tools, "OVERPASS", geo_tools.OverpassClient(fetch=fake_fetch, min_interval_s=0))


def _engine(spec: AppSpec, ticks: int = 1):
    """Six entities stay put near the grid center; tick `ticks` times, 2 s apart."""
    db = Db(":memory:")
    db.upsert_app(spec, t=T0)
    db.create_keys(spec.app_id, t=T0)
    clock = SimClock(start=T0 + 300)
    src = InMemoryEventSource()
    rng = np.random.default_rng(0)
    rows = []
    for k in range(6):
        for t in range(0, 301 + 2 * ticks, 10):
            rows.append(
                {
                    "user_id": f"e{k}",
                    "t": T0 + t,
                    "lat": 0.0025 + rng.normal(0, 2e-5),
                    "lon": 0.0025 + rng.normal(0, 2e-5),
                }
            )
    src.add(spec.app_id, "tracks", rows)
    eng = TickEngine(src, clock=clock)
    eng.add_app(spec)
    for i in range(ticks):
        if i:
            clock.advance(2.0)
        eng.tick_once()
    return db, eng, clock


def _ctx(db, eng, clock):
    return ToolContext(app_id="poi-app", db=db, engine=eng, agent="poi_labeler", clock=clock)


def _router(script) -> LlmRouter:
    fake = FakeAsyncOpenAI(script)
    return LlmRouter(Settings(_env_file=None), clients={"nvidia": fake, "openrouter": fake})


def test_labels_a_candidate_from_the_label_set_with_its_own_position():
    db, eng, clock = _engine(_spec())
    cand = eng.latest("poi-app").candidates[0]
    out = asyncio.run(run_poi_labeler(_ctx(db, eng, clock), "scheduled check", _router("poi_labeler_campus_alert")))
    assert out == "Labeled 1 candidate."
    (poi,) = db.list_pois("poi-app")
    assert poi.label == "threat origin" and poi.candidate_id == cand.id
    assert (poi.lat, poi.lon, poi.radius_m) == (cand.lat, cand.lon, cand.radius_m)  # never invented
    assert poi.visibility == "authority"  # private layer
    closing = [t for t in db.list_trace("poi-app") if t.agent == "poi_labeler" and t.action == "output"][-1]
    # the closing trace lists the change from the successful create_poi call, not the model's word
    assert closing.result.startswith("1 state change(s): create_poi") and poi.id in closing.result
    assert closing.why.startswith("agent summary") and "Labeled 1 candidate." in closing.why


def test_auto_mode_can_create_nothing_and_says_why():
    db, eng, clock = _engine(_spec(mode="auto"))
    out = asyncio.run(run_poi_labeler(_ctx(db, eng, clock), "scheduled check", _router("poi_labeler_nothing")))
    assert "nothing created" in out
    assert db.list_pois("poi-app") == []


def test_mode_off_never_calls_the_model():
    db, eng, clock = _engine(_spec(mode="off"))
    router = _router({"scenario": "s", "description": "d", "steps": []})  # any call would fail the script
    assert asyncio.run(run_poi_labeler(_ctx(db, eng, clock), "scheduled check", router)) is None
    (t,) = [t for t in db.list_trace("poi-app") if t.agent == "poi_labeler"]
    assert t.skipped_reason == "poi_labels.mode is off"


def test_label_outside_the_label_set_is_rejected_by_the_tools():
    db, eng, clock = _engine(_spec())
    script = {
        "scenario": "s",
        "description": "d",
        "steps": [
            {"response": {"tool_calls": [{"id": "c1", "name": "get_poi_candidates", "arguments": {}}]}},
            {
                "response": {
                    "tool_calls": [
                        {
                            "id": "c2",
                            "name": "create_poi",
                            "arguments": {
                                "candidate_id": "${tool_result:c1:candidates.0.id}",
                                "label": "made-up label",
                                "confidence": 0.9,
                                "evidence": [],
                            },
                        }
                    ]
                }
            },
            {"response": {"content": "Could not label; suggest a new label type 'made-up label'."}},
        ],
    }
    out = asyncio.run(run_poi_labeler(_ctx(db, eng, clock), "scheduled check", _router(script)))
    assert "suggest" in out and db.list_pois("poi-app") == []


def test_claimed_label_after_a_rejected_create_poi_traces_no_state_change():
    """Issue #246: the closing text claims a label, but the run's only create_poi was rejected."""
    db, eng, clock = _engine(_spec())
    script = {
        "scenario": "s",
        "description": "d",
        "steps": [
            {"response": {"tool_calls": [{"id": "c1", "name": "get_poi_candidates", "arguments": {}}]}},
            {
                "response": {
                    "tool_calls": [
                        {
                            "id": "c2",
                            "name": "create_poi",
                            "arguments": {
                                "candidate_id": "${tool_result:c1:candidates.0.id}",
                                "label": "gathering point",
                                "confidence": 0.1,  # below min_confidence: rejected
                                "evidence": [],
                            },
                        }
                    ]
                }
            },
            {"response": {"content": "I labeled candidate c1 as a gathering point."}},
        ],
    }
    out = asyncio.run(run_poi_labeler(_ctx(db, eng, clock), "scheduled check", _router(script)))
    assert out == "I labeled candidate c1 as a gathering point."
    assert db.list_pois("poi-app") == []
    closing = [t for t in db.list_trace("poi-app") if t.agent == "poi_labeler"][-1]
    assert closing.action == "output"
    assert closing.result.startswith("no state change | 1 rejected/failed: create_poi")
    assert closing.why.startswith("agent summary") and "I labeled candidate c1" in closing.why


def test_task_prompt_lists_labels_and_mode_without_coordinates():
    spec = _spec(mode="auto")
    p = task_prompt(spec, "scheduled check")
    assert "threat origin" in p and "gathering point" in p and "auto" in p and "0.6" in p
    assert "lat" not in p.lower() and "lon" not in p.lower()
    empty = AppSpec.model_validate({**spec.model_dump(), "outputs": {"poi_labels": {"mode": "auto"}}})
    assert "create nothing" in task_prompt(empty, "x")


def test_prompt_is_domain_neutral():
    words = ("people", "person", "bird", "police", "wind", "weather", "news", "campus", "threat")
    assert not any(w in pl.SYSTEM_PROMPT.lower() for w in words)


# -- scheduling -----------------------------------------------------------------------


def test_persist_trigger_fires_once_per_candidate():
    db, eng, clock = _engine(_spec(), ticks=PERSIST_TRIGGER_TICKS + 1)
    result = eng.latest("poi-app")
    assert result.candidates[0].persisted_ticks >= PERSIST_TRIGGER_TICKS
    st = _AppState(last_run_at=T0)
    assert persist_trigger(st, result, labeled=set()) is not None
    assert persist_trigger(st, result, labeled=set()) is None  # already triggered
    assert persist_trigger(_AppState(last_run_at=T0), result, labeled={result.candidates[0].id}) is None


def test_scheduler_runs_on_persistence_then_waits_for_the_interval(monkeypatch):
    db, eng, clock = _engine(_spec(), ticks=PERSIST_TRIGGER_TICKS + 1)
    runs = []

    async def fake_run(ctx, reason, llm=None):
        runs.append(reason)

    monkeypatch.setattr(pl, "run_poi_labeler", fake_run)
    sched = PoiLabelerScheduler(db, eng, llm=_router({"scenario": "s", "description": "d", "steps": []}))
    asyncio.run(sched.check_once())
    assert len(runs) == 1 and "persisted" in runs[0]
    asyncio.run(sched.check_once())  # same candidate, no interval elapsed: nothing
    assert len(runs) == 1
    clock.advance(pl.MIN_INTERVAL_S)
    eng.tick_once()
    asyncio.run(sched.check_once())
    assert runs[-1] == "scheduled check"


def test_scheduler_skips_mode_off_and_apps_without_candidates(monkeypatch):
    runs = []

    async def fake_run(ctx, reason, llm=None):
        runs.append(reason)

    monkeypatch.setattr(pl, "run_poi_labeler", fake_run)
    db, eng, clock = _engine(_spec(mode="off"), ticks=PERSIST_TRIGGER_TICKS + 1)
    sched = PoiLabelerScheduler(db, eng, llm=_router({"scenario": "s", "description": "d", "steps": []}))
    clock.advance(pl.MIN_INTERVAL_S * 2)
    eng.tick_once()
    asyncio.run(sched.check_once())
    assert runs == []


def test_scheduler_defers_a_triggered_run_when_the_budget_is_low_then_runs_it_later(monkeypatch):
    db, eng, clock = _engine(_spec(), ticks=PERSIST_TRIGGER_TICKS + 1)
    runs = []

    async def fake_run(ctx, reason, llm=None):
        runs.append(reason)

    monkeypatch.setattr(pl, "run_poi_labeler", fake_run)
    sched = PoiLabelerScheduler(db, eng, llm=_router({"scenario": "s", "description": "d", "steps": []}))
    sched.llm._provider("nvidia").bucket.tokens = 0.0  # #221: nothing is admitted right now

    asyncio.run(sched.check_once())
    assert runs == []  # deferred, not run
    trace = db.list_trace("poi-app")
    assert any(e.agent == "poi_labeler" and e.skipped_reason == "rate budget" for e in trace)

    # budget frees up: the deferred trigger fires without needing a brand new persist trigger
    sched.llm._provider("nvidia").bucket.tokens = sched.llm._provider("nvidia").bucket.capacity
    clock.advance(pl.TRIGGER_COOLDOWN_S + 1.0)
    asyncio.run(sched.check_once())
    assert len(runs) == 1 and "persisted" in runs[0]


def test_scheduler_never_runs_more_than_one_poi_labeler_per_app_at_once(monkeypatch):
    db, eng, clock = _engine(_spec(), ticks=PERSIST_TRIGGER_TICKS + 1)
    runs = []
    release = asyncio.Event()
    entered = asyncio.Event()

    async def slow_run(ctx, reason, llm=None):
        runs.append(reason)
        entered.set()
        await release.wait()

    monkeypatch.setattr(pl, "run_poi_labeler", slow_run)
    sched = PoiLabelerScheduler(db, eng, llm=_router({"scenario": "s", "description": "d", "steps": []}))

    async def scenario():
        first = asyncio.ensure_future(sched.check_once())
        await entered.wait()
        await sched.check_once()  # same app, still in flight: must not start a second run
        release.set()
        await first

    asyncio.run(scenario())
    assert len(runs) == 1
