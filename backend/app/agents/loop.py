"""`loop.py`: the generic tool-calling loop every agent runs on top of.

Flow: send the system+task messages with the agent's tool schemas
(`tools.registry.tool_schemas`); for each tool call the model requests, run it through
`tools.registry.call_tool` (which never raises) and feed the result back; repeat until
the model answers with no more tool calls, or `max_iterations` is hit.

Every agent acts through tool calls (`set_user_trust`, `apply_changes`, `create_poi`,
`finalize_app`, ...); the final message is a free-text summary for the trace, not a
structured answer -- `output_model=None` (the default) just returns that text as-is.
Pass `output_model` only for an agent whose final answer IS the structured output (e.g.
Scout's per-article extraction): it is then validated against that Pydantic model, with
one retry via `REPAIR_MODEL` on failure before logging and giving up (`None`).

Every step writes a `TraceEntry`: a model call that returns tool calls, the final
validated output, a failed/gave-up run, or -- when the provider's rate budget is empty --
`skipped_reason='rate budget'`. A skip returns immediately; it never blocks the tick
(rule 2).
"""

from __future__ import annotations

import json
import logging
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.agents.llm import LlmRouter, RateLimited
from app.core.config import AgentField
from app.core.models import AgentName, ToolCall, TraceEntry
from app.tools.context import ToolContext
from app.tools.registry import call_tool, tool_schemas

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_REPAIR_SYSTEM = (
    "The following JSON must validate against the given JSON Schema but does not. "
    "Return ONLY the corrected JSON, no commentary."
)


def _trace(ctx: ToolContext, agent: AgentName, **fields: Any) -> TraceEntry:
    entry = TraceEntry(t=ctx.now_dt(), app_id=ctx.app_id, agent=agent, **fields)
    ctx.db.add_trace(entry)
    return entry


def _assistant_message(message: Any) -> dict[str, Any]:
    tool_calls = getattr(message, "tool_calls", None) or []
    out: dict[str, Any] = {"role": "assistant", "content": message.content}
    if tool_calls:
        out["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            for tc in tool_calls
        ]
    return out


async def _run_tool_calls(
    ctx: ToolContext, agent: AgentName, model_id: str, tool_calls: list[Any]
) -> list[dict[str, Any]]:
    """Executes each call, appends a `TraceEntry`, and returns the `tool` messages to feed back."""
    trace_calls: list[ToolCall] = []
    tool_messages: list[dict[str, Any]] = []
    for tc in tool_calls:
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError as e:
            result: dict[str, Any] = {"ok": False, "error": f"invalid JSON arguments: {e}"}
            args = {}
        else:
            result = call_tool(ctx, tc.function.name, args)
        summary = str(result.get("result") if result.get("ok") else result.get("error"))[:200]
        trace_calls.append(
            ToolCall(name=tc.function.name, args=args, ok=result.get("ok", False), result_summary=summary)
        )
        tool_messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, default=str)})
    _trace(
        ctx, agent, model=model_id, action="tool_calls", tool_calls=trace_calls, why=f"{len(tool_calls)} tool call(s)"
    )
    return tool_messages


async def _repair(
    ctx: ToolContext, llm: LlmRouter, agent: AgentName, output_model: type[T], bad: str, error: str
) -> T | None:
    messages = [
        {"role": "system", "content": f"{_REPAIR_SYSTEM}\nSchema: {json.dumps(output_model.model_json_schema())}"},
        {"role": "user", "content": f"Invalid output:\n{bad}\n\nValidation error:\n{error}"},
    ]
    try:
        resp = await llm.chat("repair", messages, enable_thinking=False)
    except RateLimited:
        _trace(ctx, agent, action="skipped", skipped_reason="rate budget", why="repair call")
        return None
    content = resp.choices[0].message.content or ""
    try:
        return output_model.model_validate_json(content)
    except ValidationError as e:
        _trace(ctx, agent, model="repair", action="gave up", why=f"repair also failed validation: {e}")
        return None


async def run_agent(
    ctx: ToolContext,
    *,
    agent: AgentName,
    model_field: AgentField,
    system: str,
    task: str,
    output_model: type[T] | None = None,
    llm: LlmRouter | None = None,
    max_iterations: int = 8,
    enable_thinking: bool = True,
) -> T | str | None:
    """Run one tool-calling episode for `agent`.

    Returns the agent's final text (`output_model=None`, the default) or the validated
    `output_model` instance, or `None` on a skipped, rate-limited, or ultimately invalid
    run (always traced either way)."""
    llm = llm or LlmRouter()
    model = llm.settings.agent_model(model_field)
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": task}]
    schemas = tool_schemas(agent) or None

    for _ in range(max_iterations):
        try:
            resp = await llm.chat(model_field, messages, tools=schemas, enable_thinking=enable_thinking)
        except RateLimited as e:
            _trace(ctx, agent, model=model.model_id, action="skipped", skipped_reason="rate budget", why=str(e))
            return None
        except Exception as e:  # noqa: BLE001 - a provider failure must not crash the caller
            _trace(ctx, agent, model=model.model_id, action="error", why=f"{type(e).__name__}: {e}")
            return None

        message = resp.choices[0].message
        messages.append(_assistant_message(message))
        tool_calls = getattr(message, "tool_calls", None) or []
        if not tool_calls:
            content = message.content or ""
            if output_model is None:
                _trace(ctx, agent, model=model.model_id, action="output", result=content[:500], why="final answer")
                return content
            try:
                result = output_model.model_validate_json(content)
            except ValidationError as e:
                repaired = await _repair(ctx, llm, agent, output_model, content, str(e))
                if repaired is None:
                    return None
                _trace(ctx, agent, model=model.model_id, action="output", why="validated after repair")
                return repaired
            _trace(ctx, agent, model=model.model_id, action="output", result=content[:500], why="validated")
            return result

        messages.extend(await _run_tool_calls(ctx, agent, model.model_id, tool_calls))

    _trace(ctx, agent, model=model.model_id, action="gave up", why=f"exceeded {max_iterations} iterations")
    return None
