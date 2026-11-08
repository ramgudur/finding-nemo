import asyncio

from fake_llm import FakeAsyncOpenAI
from pydantic import BaseModel

from app.agents.llm import LlmRouter
from app.agents.loop import run_agent
from app.core.config import Settings
from app.core.db import Db
from app.tools.context import ToolContext


class Label(BaseModel):
    label: str
    confidence: float


def _ctx() -> ToolContext:
    return ToolContext(app_id="test-app", db=Db(":memory:"), engine=object(), agent="critic")


def _router(script, **settings_over) -> LlmRouter:
    settings = Settings(_env_file=None, **settings_over)
    return LlmRouter(settings, clients={"nvidia": FakeAsyncOpenAI(script), "openrouter": FakeAsyncOpenAI(script)})


def _run(ctx, llm, output_model=Label, **kw):
    return asyncio.run(
        run_agent(
            ctx, agent="critic", model_field="critic", system="s", task="t", output_model=output_model, llm=llm, **kw
        )
    )


def test_final_answer_with_no_tool_calls_is_returned():
    ctx = _ctx()
    script = {
        "scenario": "s",
        "description": "d",
        "steps": [{"response": {"content": '{"label": "x", "confidence": 0.9}'}}],
    }
    result = _run(ctx, _router(script))
    assert result == Label(label="x", confidence=0.9)
    traces = ctx.db.list_trace("test-app")
    assert traces[-1].action == "output"


def test_tool_call_then_final_answer_runs_the_real_annotate_tool():
    ctx = _ctx()
    script = {
        "scenario": "s",
        "description": "d",
        "steps": [
            {"response": {"tool_calls": [{"id": "call_1", "name": "annotate", "arguments": {"text": "noted"}}]}},
            {"response": {"content": '{"label": "x", "confidence": 0.5}'}},
        ],
    }
    result = _run(ctx, _router(script))
    assert result == Label(label="x", confidence=0.5)
    traces = ctx.db.list_trace("test-app")
    # the real annotate handler runs inside the tool call and writes its own trace entry
    # first; the loop's "tool_calls" summary for that step follows it
    assert traces[0].action == "annotate" and traces[0].why == "noted"
    tool_call_trace = next(t for t in traces if t.action == "tool_calls")
    assert tool_call_trace.tool_calls[0].name == "annotate"
    assert tool_call_trace.tool_calls[0].ok is True


def test_bad_tool_arguments_are_reported_to_the_model_not_raised():
    ctx = _ctx()
    # max_iterations=1: stop right after the bad tool call, before the fixture's second
    # step (plain text "done", not JSON) would hit output validation and exhaust the script.
    result = _run(ctx, _router("bad_tool_arguments"), max_iterations=1)
    assert result is None
    traces = ctx.db.list_trace("test-app")
    assert traces[0].action == "tool_calls"
    assert traces[0].tool_calls[0].ok is False
    assert "invalid JSON arguments" in traces[0].tool_calls[0].result_summary


def test_rate_limited_before_any_call_returns_none_and_traces():
    ctx = _ctx()
    router = _router({"scenario": "s", "description": "d", "steps": [{"response": {"content": "unused"}}]})
    router._provider("nvidia").bucket.tokens = 0.0
    result = _run(ctx, router)
    assert result is None
    traces = ctx.db.list_trace("test-app")
    assert traces[-1].skipped_reason == "rate budget"


def test_invalid_final_answer_is_repaired():
    ctx = _ctx()
    # critic's model routes to "nvidia" and repair's to "openrouter" by default; one
    # shared fake client across both providers replays the script's two steps in call
    # order (the agent's bad reply, then the repair model's good one) regardless of
    # which provider name each call is routed through.
    fake = FakeAsyncOpenAI("malformed_then_repair")
    router = LlmRouter(Settings(_env_file=None), clients={"nvidia": fake, "openrouter": fake})
    result = _run(ctx, router)
    assert result == Label(label="example", confidence=0.7)
    traces = ctx.db.list_trace("test-app")
    assert traces[-1].why == "validated after repair"


def test_default_output_is_the_final_text_not_json_validated():
    """Every real agent acts through tool calls and ends with a free-text summary, not
    structured JSON (confirmed against every fixture in tests/fixtures/llm/); the
    default (no output_model) must return that text as-is, never attempt repair."""
    ctx = _ctx()
    script = {"scenario": "s", "description": "d", "steps": [{"response": {"content": "All good, no changes."}}]}
    result = asyncio.run(
        run_agent(ctx, agent="critic", model_field="critic", system="s", task="t", llm=_router(script))
    )
    assert result == "All good, no changes."
    traces = ctx.db.list_trace("test-app")
    assert traces[-1].action == "output" and traces[-1].result == "All good, no changes."


def test_critic_cold_start_fixture_reads_the_report_and_stops():
    ctx = _ctx()
    result = asyncio.run(
        run_agent(ctx, agent="critic", model_field="critic", system="s", task="t", llm=_router("critic_cold_start"))
    )
    assert result == "Cold start: fewer than two verification methods have data, so no weight changes yet."
    traces = ctx.db.list_trace("test-app")
    assert traces[0].tool_calls[0].name == "get_verification_report"


def test_endless_tool_calls_gives_up_at_max_iterations():
    ctx = _ctx()
    result = _run(ctx, _router("endless_tool_calls"), max_iterations=5)
    assert result is None
    traces = ctx.db.list_trace("test-app")
    assert traces[-1].action == "gave up"
    assert "5 iterations" in traces[-1].why


def test_a_slow_tool_does_not_block_the_event_loop(monkeypatch):
    """Tools may block on network or CPU; they run in a worker thread so the server's other coroutines
    (requests, WebSocket streams, tick scheduling) keep running meanwhile."""
    import time

    from app.agents import loop as loop_mod

    def slow_call_tool(ctx, name, args):
        time.sleep(0.4)  # e.g. an external lookup that is slow to answer
        return {"ok": True, "result": "done"}

    monkeypatch.setattr(loop_mod, "call_tool", slow_call_tool)
    script = {
        "scenario": "s",
        "description": "d",
        "steps": [
            {"response": {"tool_calls": [{"id": "call_1", "name": "annotate", "arguments": {"text": "x"}}]}},
            {"response": {"content": "finished"}},
        ],
    }

    async def scenario():
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        hb = asyncio.create_task(heartbeat())
        out = await run_agent(_ctx(), agent="critic", model_field="critic", system="s", task="t", llm=_router(script))
        hb.cancel()
        return out, ticks

    out, ticks = asyncio.run(scenario())
    assert out == "finished"
    assert ticks >= 10  # about 20 beats in 0.4 s; zero if the tool blocked the loop
