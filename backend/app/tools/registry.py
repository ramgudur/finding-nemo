"""Tool registry: name -> JSON schema, handler, and which agents may call it.

``agents/loop.py`` uses two functions:
- ``tool_schemas(agent)`` -> OpenAI-style ``tools`` list for that agent's chat call.
- ``call_tool(ctx, name, args)`` -> ``{"ok": True, "result": ...}`` or ``{"ok": False, "error": ...}``.
  It never raises for agent mistakes or guard rejections; the error text is for the trace.

Argument validation is Pydantic (extra keys rejected). Limits live in ``guards.py``, not here
and not in prompts.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from app.tools import scoring_tools as st
from app.tools.context import ToolContext
from app.tools.guards import GuardError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    handler: Callable[[ToolContext, Any], dict[str, Any]]
    agents: frozenset[str]  # agent names allowed to call it; "dev" marks a dev-console action


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in (
        Tool(
            "run_backtest",
            "Backtest a proposed weight or trust change on held-out recent data. Weights are checked "
            "against tier rules first and renormalized; the result says whether the holdout passed. "
            "Only a passing backtest_id can be applied.",
            st.RunBacktestArgs,
            st.run_backtest,
            frozenset({"critic"}),
        ),
        Tool(
            "apply_changes",
            "Apply exactly what a passing backtest tested. Rejected if the holdout failed, the backtest "
            "is stale or already used, or weights changed since.",
            st.ApplyChangesArgs,
            st.apply_changes,
            frozenset({"critic"}),
        ),
        Tool(
            "set_user_trust",
            "Lower one user's trust (to a value in [0, 1]) with a reason. Increases need evidence: use "
            "run_backtest(trust=...) and apply_changes (capped at +0.1 per user per 5 minutes).",
            st.SetUserTrustArgs,
            st.set_user_trust,
            frozenset({"critic"}),
        ),
        Tool(
            "pin_weight",
            "Dev only: pin or unpin a layer weight so no agent change or renormalization moves it.",
            st.PinWeightArgs,
            st.pin_weight,
            frozenset({"dev"}),
        ),
    )
}


def tool_schemas(agent: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.args.model_json_schema()},
        }
        for t in TOOLS.values()
        if agent in t.agents
    ]


def call_tool(ctx: ToolContext, name: str, args: dict[str, Any] | None) -> dict[str, Any]:
    tool = TOOLS.get(name)
    if tool is None:
        return {"ok": False, "error": f"unknown tool {name!r}"}
    caller = "dev" if ctx.actor == "dev" else (ctx.agent or "")
    if caller not in tool.agents:
        return {"ok": False, "error": f"tool {name!r} is not available to {caller or 'this caller'}"}
    try:
        parsed = tool.args.model_validate(args or {})
    except ValidationError as e:
        errs = "; ".join(f"{'.'.join(map(str, x['loc'])) or 'args'}: {x['msg']}" for x in e.errors())
        return {"ok": False, "error": f"invalid arguments: {errs}"}
    try:
        return {"ok": True, "result": tool.handler(ctx, parsed)}
    except GuardError as e:
        return {"ok": False, "error": "rejected: " + "; ".join(e.reasons)}
    except LookupError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001 - a tool bug must not crash the agent loop
        log.exception("tool %s failed", name)
        return {"ok": False, "error": f"internal error in {name}: {type(e).__name__}"}
