import asyncio

from fake_llm import FakeAsyncOpenAI

from app.agents.compiler import CompilerCache, run_compiler
from app.agents.llm import LlmRouter
from app.core.config import Settings
from app.core.db import Db
from app.core.models import AppSpec
from app.tools.context import ToolContext

BBOX = {"min_lat": 0.0, "min_lon": 0.0, "max_lat": 0.01, "max_lon": 0.01}
GRID = {"cell_m": 10, "bbox": BBOX}
INTENT = "find where people gather"
INPUTS = [{"id": "pings", "kind": "push", "shape": "point", "meaning": "a ping means someone is here"}]


def _router(script) -> LlmRouter:
    fake = FakeAsyncOpenAI(script)
    return LlmRouter(Settings(_env_file=None), clients={"nvidia": fake, "openrouter": fake})


def _ctx(app_id: str) -> ToolContext:
    return ToolContext(app_id=app_id, db=Db(":memory:"), engine=object(), agent="compiler")


def _run(ctx, llm, cache=None, **over):
    kwargs = {"app_id": ctx.app_id, "intent": INTENT, "grid": GRID, "inputs": INPUTS, "llm": llm, "cache": cache}
    kwargs.update(over)
    return asyncio.run(run_compiler(ctx, **kwargs))


def test_compiler_campus_alert_fixture_produces_a_pending_confirmation_spec():
    ctx = _ctx("campus-alert")
    cache = CompilerCache()
    spec = _run(ctx, _router("compiler_campus_alert"), cache=cache)
    assert spec is not None
    assert spec.status == "pending_confirmation"
    assert spec.app_id == "campus-alert"
    assert len(spec.inputs) == 4
    saved = ctx.db.get_app("campus-alert")
    assert saved == spec


def test_second_compile_with_the_same_intent_and_inputs_hits_the_cache():
    cache = CompilerCache()
    ctx1 = _ctx("campus-alert")
    first = _run(ctx1, _router("compiler_campus_alert"), cache=cache)
    assert first is not None

    ctx2 = _ctx("campus-alert-2")
    router2 = _router("compiler_campus_alert")
    second = _run(ctx2, router2, cache=cache, app_id="campus-alert-2")
    assert second is not None
    assert second.app_id == "campus-alert-2"
    assert second.status == "pending_confirmation"
    assert [i.id for i in second.inputs] == [i.id for i in first.inputs]
    assert router2._provider("nvidia").client.calls == []  # never called: served from cache
    assert ctx2.db.get_app("campus-alert-2").app_id == "campus-alert-2"
    # regression: model_copy(update=...) doesn't coerce a raw grid dict into a GridSpec
    assert second.grid.cell_m == GRID["cell_m"]


def test_a_cache_hit_never_demotes_an_already_active_app():
    cache = CompilerCache()
    ctx1 = _ctx("campus-alert")
    first = _run(ctx1, _router("compiler_campus_alert"), cache=cache)
    assert first is not None

    # the app was since confirmed and activated
    active = ctx1.db.get_app("campus-alert").model_copy(update={"status": "active"})
    ctx1.db.upsert_app(active, t=ctx1.now())

    router2 = _router("compiler_campus_alert")
    result = _run(ctx1, router2, cache=cache)  # same ctx/app_id, same intent+inputs: a cache hit
    assert result is None  # rejected, not silently applied
    assert ctx1.db.get_app("campus-alert").status == "active"  # untouched
    assert router2._provider("nvidia").client.calls == []  # still served from cache, not the LLM
    traces = [t for t in ctx1.db.list_trace("campus-alert") if t.why == "cache hit, no LLM"]
    assert traces and traces[-1].action == "error"


def test_a_different_intent_is_not_served_from_the_same_cache_entry():
    cache = CompilerCache()
    ctx1 = _ctx("campus-alert")
    _run(ctx1, _router("compiler_campus_alert"), cache=cache)

    ctx2 = _ctx("other-app")
    router2 = _router({"scenario": "s", "description": "d", "steps": [{"response": {"content": "no changes"}}]})
    result = _run(ctx2, router2, cache=cache, intent="a different intent entirely")
    assert result is None  # the model never called finalize_app in this scripted run
    assert len(router2._provider("nvidia").client.calls) == 1  # the LLM *was* called: no cache hit


def test_rate_limited_run_returns_none_without_saving_anything():
    ctx = _ctx("campus-alert")
    router = _router({"scenario": "s", "description": "d", "steps": [{"response": {"content": "unused"}}]})
    router._provider("nvidia").bucket.tokens = 0.0
    result = _run(ctx, router)
    assert result is None
    assert ctx.db.get_app("campus-alert") is None


def test_a_run_that_never_finalizes_returns_none():
    ctx = _ctx("campus-alert")
    script = {
        "scenario": "s",
        "description": "d",
        "steps": [{"response": {"content": "I couldn't find a good spec for this."}}],
    }
    result = _run(ctx, _router(script))
    assert result is None
    assert ctx.db.get_app("campus-alert") is None


def test_cache_miss_for_different_inputs_even_with_the_same_intent():
    cache = CompilerCache()
    assert cache.get(INTENT, INPUTS) is None
    spec = AppSpec.model_validate({"app_id": "x", "intent": INTENT, "grid": GRID, "status": "pending_confirmation"})
    cache.put(INTENT, INPUTS, spec)
    assert cache.get(INTENT, INPUTS) == spec
    other_inputs = [{**INPUTS[0], "meaning": "a different meaning"}]
    assert cache.get(INTENT, other_inputs) is None


def test_live_recorded_compile_meets_the_137_bar():
    """Replay of a live Nemotron run (#137) on Campus Alert's raw inputs after the prompt and
    validate_spec fixes: crowd input flexible, authority fixed points locked, a destination label
    with routing on, authority_confirmation first, in at most 6 model calls."""
    import json
    from pathlib import Path

    spec_json = json.load(open(Path(__file__).parents[2] / "specs" / "campus-alert.json"))
    inputs = [
        {k: i[k] for k in ("id", "kind", "shape", "meaning") if k in i}
        | ({"fields": i["fields"]} if i.get("fields") else {})
        for i in spec_json["inputs"]
    ]
    ctx = _ctx("campus-alert")
    router = _router("compiler_campus_alert_v2")
    spec = _run(ctx, router, cache=CompilerCache(), intent=spec_json["intent"], grid=spec_json["grid"], inputs=inputs)
    assert spec is not None and spec.status == "pending_confirmation"
    tiers = {i.id: i.tier for i in spec.inputs}
    assert tiers["panic"] == "flexible"
    assert tiers["room_button"] == tiers["police_marker"] == "locked"
    labels = spec.outputs.poi_labels
    assert labels.mode == "on" and any(lab.destination for lab in labels.label_set)
    assert spec.routing.enabled
    assert min(spec.verification, key=lambda v: v.priority).method == "authority_confirmation"
    assert next(i for i in spec.inputs if i.id == "panic").plausibility is not None
    assert len(router._provider("nvidia").client.calls) <= 6
