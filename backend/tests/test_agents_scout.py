import asyncio
import json

import pytest
from fake_llm import FakeAsyncOpenAI
from pydantic import ValidationError

from app.agents import scout as scout_mod
from app.agents.llm import LlmRouter
from app.agents.scout import ArticleExtract, ScoutRunner, extract_article, pending_requests, run_scout
from app.core.clock import SimClock
from app.core.config import Settings
from app.core.db import Db
from app.core.models import AppSpec, TraceEntry
from app.engine.tick import InMemoryEventSource, TickEngine
from app.tools import registry
from app.tools.context import ToolContext

T0 = 1_800_000_000.0
BBOX = {"min_lat": 32.77, "min_lon": -96.77, "max_lat": 32.79, "max_lon": -96.75}


def _spec() -> AppSpec:
    return AppSpec.model_validate(
        {
            "app_id": "scout-app",
            "intent": "map where incidents are reported",
            "status": "active",
            "grid": {"cell_m": 25, "bbox": BBOX},
            "inputs": [
                {
                    "id": "local_news",
                    "kind": "pull",
                    "shape": "document",
                    "tier": "stiff",
                    "meaning": "local reports of incidents in the area",
                    "operators": [{"op": "event_points", "weight": 0.3}],
                },
                {
                    "id": "presses",
                    "kind": "push",
                    "shape": "point",
                    "tier": "flexible",
                    "meaning": "a report",
                    "operators": [{"op": "density", "weight": 1.0}],
                },
            ],
        }
    )


def _ctx():
    db = Db(":memory:")
    spec = _spec()
    db.upsert_app(spec, t=T0)
    db.create_keys(spec.app_id, t=T0)
    clock = SimClock(start=T0)
    eng = TickEngine(InMemoryEventSource(), clock=clock)
    eng.add_app(spec)
    eng.tick_once()
    return ToolContext(app_id="scout-app", db=db, engine=eng, agent="scout", clock=clock), clock


def _router(script) -> LlmRouter:
    fake = FakeAsyncOpenAI(script)
    return LlmRouter(Settings(_env_file=None), clients={"nvidia": fake, "openrouter": fake})


def _script(*contents):
    return {"scenario": "s", "description": "d", "steps": [{"response": {"content": c}} for c in contents]}


# -- extraction -------------------------------------------------------------------------


def test_extract_article_fixture_validates_with_reasoning_off():
    ctx, _ = _ctx()
    out = asyncio.run(extract_article(ctx, "Report", "Something at the hall.", "incidents", _router("extract_article")))
    assert isinstance(out, ArticleExtract) and out.relevant
    assert (out.lat, out.lon, out.confidence) == (32.7796, -96.76, 0.8)
    assert any(t.agent == "scout" and t.action == "extract" for t in ctx.db.list_trace("scout-app"))


def test_extract_article_repairs_once():
    ctx, _ = _ctx()
    good = json.dumps({"relevant": False, "confidence": 0.1, "summary": "Unrelated."})
    out = asyncio.run(extract_article(ctx, "t", "x", "m", _router(_script('{"relevant": true,', good))))
    assert out is not None and out.relevant is False
    assert any(t.why == "validated after repair" for t in ctx.db.list_trace("scout-app"))


def test_extract_article_gives_up_after_repair_fails():
    ctx, _ = _ctx()
    out = asyncio.run(extract_article(ctx, "t", "x", "m", _router(_script("not json", "{still not json"))))
    assert out is None
    assert any(t.action == "gave up" for t in ctx.db.list_trace("scout-app"))


def test_extract_article_rate_limited_is_traced():
    ctx, _ = _ctx()
    router = _router(_script("unused"))
    for p in ("nvidia", "openrouter"):
        router._provider(p).bucket.tokens = 0.0
    assert asyncio.run(extract_article(ctx, "t", "x", "m", router)) is None
    assert any(t.skipped_reason == "rate budget" for t in ctx.db.list_trace("scout-app"))


def test_article_extract_never_takes_half_a_location_or_a_placeless_relevant_doc():
    with pytest.raises(ValidationError):
        ArticleExtract(relevant=True, lat=32.0, confidence=0.5, summary="s")
    with pytest.raises(ValidationError):
        ArticleExtract(relevant=True, confidence=0.5, summary="s")
    assert ArticleExtract(relevant=True, place="the hall", confidence=0.5, summary="s").lat is None
    assert ArticleExtract(relevant=False, confidence=0.0, summary="s").place is None


# -- on request only -----------------------------------------------------------------------


def _request(ctx, input_id, t, why="find a feed"):
    ctx.db.add_trace(
        TraceEntry(
            t=ctx.now_dt().fromtimestamp(t, ctx.now_dt().tzinfo),
            app_id=ctx.app_id,
            agent="compiler",
            action="request_scout",
            saw=input_id,
            why=why,
        )
    )


def test_pending_requests_until_handled():
    ctx, clock = _ctx()
    assert pending_requests(ctx.db, "scout-app") == []
    _request(ctx, "local_news", T0 - 10, "first")
    _request(ctx, "local_news", T0 - 5, "second")
    (req,) = pending_requests(ctx.db, "scout-app")  # one run covers both
    assert req.why == "second"
    asyncio.run(run_scout(ctx, "presses", "", _router(_script("unused"))))  # push input: skipped
    assert len(pending_requests(ctx.db, "scout-app")) == 1
    asyncio.run(run_scout(ctx, "local_news", "x", _router(_script("No source found; configured none."))))
    assert pending_requests(ctx.db, "scout-app") == []
    clock.advance(5)
    _request(ctx, "local_news", T0 + 5, "again")
    assert [r.why for r in pending_requests(ctx.db, "scout-app")] == ["again"]


def test_runner_runs_each_pending_request_once(monkeypatch):
    ctx, clock = _ctx()
    _request(ctx, "local_news", T0 - 1)
    calls = []

    async def fake_run(c, input_id, instructions="", llm=None):
        calls.append((input_id, instructions))
        c.db.add_trace(TraceEntry(t=c.now_dt(), app_id=c.app_id, agent="scout", action="request_handled", saw=input_id))

    monkeypatch.setattr(scout_mod, "run_scout", fake_run)
    runner = ScoutRunner(ctx.db, ctx.engine, llm=_router(_script("unused")))
    asyncio.run(runner.check_once())
    clock.advance(1)
    asyncio.run(runner.check_once())
    assert calls == [("local_news", "find a feed")]


def test_prompt_is_domain_neutral():
    words = ("people", "person", "bird", "police", "wind", "weather", "campus", "threat")
    assert not any(w in scout_mod.SYSTEM_PROMPT.lower() for w in words)
    assert not any(w in scout_mod.EXTRACT_SYSTEM.lower() for w in words)


# -- full episode on the recorded fixture (needs seat a's Scout tools) ----------------------

SCOUT_TOOLS = {"web_search", "probe_feed", "create_connector", "test_connector"}


def test_scout_rss_episode_configures_and_tests_a_connector():
    offered = {s["function"]["name"] for s in registry.tool_schemas("scout")}
    if not SCOUT_TOOLS <= offered:
        pytest.skip(f"Scout tools not registered yet: {sorted(SCOUT_TOOLS - offered)}")
    ctx, _ = _ctx()
    out = asyncio.run(run_scout(ctx, "local_news", "find a feed", _router("scout_rss")))
    assert out and "connector" in out.lower()
    assert any(t.action == "request_handled" for t in ctx.db.list_trace("scout-app"))


def test_article_extractor_adapts_for_the_connector_locator():
    from app.agents.scout import article_extractor
    from app.connectors.engine import make_extract_locator

    ctx, _ = _ctx()
    extract = article_extractor(ctx.db, ctx.engine, _router("extract_article"))

    async def no_geocode(place, spec):
        return None

    locate = make_extract_locator(extract, no_geocode)
    inp = next(i for i in ctx.spec.inputs if i.id == "local_news")
    found = asyncio.run(locate(ctx.spec, inp, {"title": "Report", "description": "At the hall."}))
    assert found == (32.7796, -96.76, 0.8)
