"""`agents/compiler.py`: turns a dev's intent and raw inputs into a proposed `AppSpec`.

The dev supplies `app_id`, `intent`, `grid`, and each input's `id`/`kind`/`shape`/
`meaning`/`fields` (push) or `id`/`kind`/`shape`/`meaning` (pull, before Scout configures
a connector) -- no tiers, operators or plausibility: picking those is the Compiler's job,
via `tools.compiler_tools`'s `list_operators`/`validate_spec`/`finalize_app` (the agent
never writes state directly, rule 1). `finalize_app`'s own Pydantic + platform validation
is where a bad proposal gets caught and fed back to the model to retry within the same
run; the loop's separate `output_model`/`REPAIR_MODEL` path is for an agent whose final
*message* must itself be structured (e.g. Scout's article extraction) -- the Compiler's
final message is a free-text summary, like every other tool-driven agent (`agents/loop.py`).

Compiled specs are cached by a hash of `(intent, inputs)`: a repeat compile for a
different `app_id`/`grid` with the same intent and inputs skips the LLM entirely and
reuses the proposed inputs/verification/outputs/routing as-is.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.agents.llm import LlmRouter
from app.agents.loop import run_agent
from app.core.models import AppSpec, TraceEntry
from app.tools.context import ToolContext
from app.tools.registry import call_tool

MAX_ITERATIONS = 8
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "compiler.md").read_text(encoding="utf-8")


def _cache_key(intent: str, inputs: list[dict[str, Any]]) -> str:
    payload = json.dumps({"intent": intent, "inputs": inputs}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class CompilerCache:
    """In-process cache of compiled specs, keyed by `(intent, inputs)`. Not persisted:
    a restart simply recompiles, same as a first-ever request (safe default)."""

    _by_key: dict[str, AppSpec] = field(default_factory=dict)

    def get(self, intent: str, inputs: list[dict[str, Any]]) -> AppSpec | None:
        return self._by_key.get(_cache_key(intent, inputs))

    def put(self, intent: str, inputs: list[dict[str, Any]], spec: AppSpec) -> None:
        self._by_key[_cache_key(intent, inputs)] = spec


COMPILER_CACHE = CompilerCache()


def _task_prompt(app_id: str, intent: str, grid: dict[str, Any], inputs: list[dict[str, Any]]) -> str:
    given = {"app_id": app_id, "intent": intent, "grid": grid, "inputs": inputs}
    return (
        "Compile this into a complete proposed AppSpec, exactly as given below plus your "
        "own choices of tier, operators, plausibility, verification, outputs and routing "
        f"for the inputs:\n{json.dumps(given, indent=2)}"
    )


async def run_compiler(
    ctx: ToolContext,
    *,
    app_id: str,
    intent: str,
    grid: dict[str, Any],
    inputs: list[dict[str, Any]],
    llm: LlmRouter | None = None,
    cache: CompilerCache | None = None,
) -> AppSpec | None:
    """Compile `app_id` from `intent` and `inputs`. Returns the proposed spec (already
    saved as `pending_confirmation`), or `None` if the run was skipped, rate-limited, or
    never reached a valid `finalize_app` call."""
    cache = COMPILER_CACHE if cache is None else cache
    cached = cache.get(intent, inputs)
    if cached is not None:
        # Through finalize_app (not a direct db.upsert_app), so a cache hit gets the same
        # guards as a fresh compile -- in particular, it can't silently demote an already
        # active app back to pending_confirmation (rule 4: locked stays locked).
        spec_dict = {**cached.model_dump(mode="json"), "app_id": app_id, "grid": grid}
        result = call_tool(ctx, "finalize_app", {"spec": spec_dict})
        ctx.db.add_trace(
            TraceEntry(
                t=ctx.now_dt(),
                app_id=app_id,
                agent="compiler",
                action="output" if result["ok"] else "error",
                why="cache hit, no LLM",
                result=str(result["result"] if result["ok"] else result["error"])[:500],
            )
        )
        return ctx.db.get_app(app_id) if result["ok"] else None

    await run_agent(
        ctx,
        agent="compiler",
        model_field="compiler",
        system=SYSTEM_PROMPT,
        task=_task_prompt(app_id, intent, grid, inputs),
        llm=llm,
        max_iterations=MAX_ITERATIONS,
        enable_thinking=True,
    )
    spec = ctx.db.get_app(app_id)
    if spec is None or spec.status != "pending_confirmation":
        return None
    cache.put(intent, inputs, spec)
    return spec
