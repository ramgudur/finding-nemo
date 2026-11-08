import asyncio
import json as _json
from pathlib import Path as _Path

from fake_llm import FakeAsyncOpenAI

from app.agents.compiler import CompilerCache, run_compiler
from app.agents.llm import LlmRouter
from app.core.config import Settings
from app.core.db import Db
from app.core.models import AppSpec
from app.tools.context import ToolContext

# The dev request that matches the recorded `compiler_campus_alert` fixture, whose finalize_app
# call proposes specs/campus-alert.json: finalize_app pins the dev's inputs, intent and grid (#164),
# so the request has to declare the same inputs the recording proposes.
_FIXTURE_SPEC = _json.loads((_Path(__file__).parents[2] / "specs" / "campus-alert.json").read_text(encoding="utf-8"))
GRID = _FIXTURE_SPEC["grid"]
BBOX = GRID["bbox"]
INTENT = _FIXTURE_SPEC["intent"]
INPUTS = [{k: i[k] for k in ("id", "kind", "shape", "meaning", "fields") if k in i} for i in _FIXTURE_SPEC["inputs"]]


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


DEV_POINTS = [{"lat": 0.004, "lon": 0.006, "label": "Gym (staff)"}, {"lat": 0.009, "lon": 0.001, "label": "Lot C"}]


def test_dev_destination_points_are_copied_verbatim_fresh_and_from_cache():
    cache = CompilerCache()
    spec = _run(_ctx("campus-alert"), _router("compiler_campus_alert"), cache=cache, destination_points=DEV_POINTS)
    assert [d.model_dump() for d in spec.routing.destination_points] == DEV_POINTS

    # served from the cache (another app id), the points are still exactly the request's
    hit = _run(_ctx("a2"), _router("compiler_campus_alert"), cache=cache, app_id="a2", destination_points=DEV_POINTS)
    assert [d.model_dump() for d in hit.routing.destination_points] == DEV_POINTS
    # and a request without points never inherits another request's
    bare = _run(_ctx("campus-alert"), _router("compiler_campus_alert"), cache=cache)
    assert bare.routing.destination_points == []


def test_routing_with_dev_points_but_no_destination_label_passes_finalize():
    from app.tools.registry import call_tool

    base = _run(_ctx("campus-alert"), _router("compiler_campus_alert"), cache=CompilerCache())
    raw = base.model_dump(mode="json")
    for lab in raw["outputs"]["poi_labels"]["label_set"]:
        lab["destination"] = False
    raw["routing"] = {**raw["routing"], "enabled": True, "destination_points": []}

    bare = _ctx("campus-alert")
    refused = call_tool(bare, "finalize_app", {"spec": raw})
    assert refused["ok"] is False and "nothing can be a destination" in refused["error"]

    ctx = _ctx("campus-alert")
    ctx.extra["dev_destination_points"] = DEV_POINTS
    ok = call_tool(ctx, "finalize_app", {"spec": raw})  # the model sent none; the dev's are written in
    assert ok["ok"], ok
    assert [d.model_dump() for d in ctx.db.get_app("campus-alert").routing.destination_points] == DEV_POINTS


def test_recorded_live_neutral_compile_meets_the_164_bar():
    """A real Nemotron run (recorded live on build.nvidia.com) of a neutral example app, replayed:
    it finalizes in 3 model calls (catalogue in the task, no list_operators round trip), names the
    places the intent asks about, and proposes every verification method its inputs can feed."""
    fixture = _json.loads(
        (_Path(__file__).parent / "fixtures" / "llm" / "compiler_neutral_fleet_live.json").read_text(encoding="utf-8")
    )
    req = fixture["input"]
    fake = FakeAsyncOpenAI(fixture)
    llm = LlmRouter(Settings(_env_file=None), clients={"nvidia": fake, "openrouter": fake})
    ctx = _ctx(req["app_id"])
    spec = asyncio.run(
        run_compiler(
            ctx,
            app_id=req["app_id"],
            intent=req["intent"],
            grid=req["grid"],
            inputs=req["inputs"],
            llm=llm,
            cache=CompilerCache(),
        )
    )
    assert spec is not None and spec.status == "pending_confirmation"
    assert len(fake.calls) == 3
    assert spec.outputs.poi_labels.mode != "off" and spec.outputs.poi_labels.label_set
    assert len(spec.verification) >= 2
    assert [i.meaning for i in spec.inputs] == [i["meaning"] for i in req["inputs"]]  # pinned verbatim
    from app.tools.compiler_tools import _spec_warnings

    assert not any("leaves out" in w for w in _spec_warnings(spec))
