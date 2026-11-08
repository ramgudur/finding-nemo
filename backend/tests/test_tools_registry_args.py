"""Tool-argument validation (#260): object-typed arguments sent as JSON strings are decoded, or
rejected with a precise, repairable reason. Generic for every tool and agent."""

import asyncio
import json

import pytest
from fake_llm import FakeAsyncOpenAI

from app.agents.llm import LlmRouter
from app.agents.loop import run_agent
from app.core.config import Settings
from app.core.db import Db
from app.core.models import AppSpec
from app.tools import registry
from app.tools.context import ToolContext

BBOX = {"min_lat": 0.0, "min_lon": 0.0, "max_lat": 0.01, "max_lon": 0.01}


def _draft() -> dict:
    return {"app_id": "draft-app", "intent": "find where things gather", "grid": {"cell_m": 10, "bbox": BBOX}}


@pytest.fixture
def ctx():
    db = Db(":memory:")
    db.upsert_app(AppSpec.model_validate(_draft()), t=1.0)
    return ToolContext(app_id="draft-app", db=db, engine=object(), agent="compiler")


def test_object_arg_sent_as_a_json_string_is_decoded_and_validated(ctx):
    as_dict = registry.call_tool(ctx, "validate_spec", {"spec": _draft()})
    as_string = registry.call_tool(ctx, "validate_spec", {"spec": json.dumps(_draft())})
    assert as_string["ok"] is True
    assert as_string["result"] == as_dict["result"]


def test_truncated_json_string_reports_the_position_and_how_to_fix_it(ctx):
    text = json.dumps(_draft())[:-1]  # one brace short
    r = registry.call_tool(ctx, "validate_spec", {"spec": text})
    assert r["ok"] is False
    err = r["error"]
    assert "spec was a string, not an object" in err
    assert "JSON parse error" in err and f"at char {len(text)}" in err and "line 1 col" in err
    assert "Pass spec as a JSON object." in err


def test_a_string_holding_a_json_list_is_still_rejected(ctx):
    r = registry.call_tool(ctx, "validate_spec", {"spec": "[1, 2]"})
    assert r["ok"] is False
    assert "holding JSON list, not an object" in r["error"] and "Pass spec as a JSON object." in r["error"]


def test_extra_keys_are_still_rejected_after_decoding(ctx):
    r = registry.call_tool(ctx, "validate_spec", {"spec": json.dumps(_draft()), "surprise": 1})
    assert r["ok"] is False and "surprise" in r["error"]


def test_string_args_that_are_not_object_typed_are_left_alone(ctx):
    # only object-typed args are decoded; a plain string arg (annotate.text) stays a string
    names = registry._object_arg_names(registry.TOOLS["validate_spec"].args)
    assert names == {"spec"}
    assert "text" not in registry._object_arg_names(registry.TOOLS["annotate"].args)


def test_non_dict_arguments_are_rejected_not_raised(ctx):
    r = registry.call_tool(ctx, "list_operators", [1, 2])  # type: ignore[arg-type]
    assert r["ok"] is False and "JSON object" in r["error"]


def _loop_run(arguments_raw: str):
    ctx = ToolContext(app_id="test-app", db=Db(":memory:"), engine=object(), agent="critic")
    script = {
        "scenario": "s",
        "description": "d",
        "steps": [{"response": {"tool_calls": [{"id": "c1", "name": "annotate", "arguments_raw": arguments_raw}]}}],
    }
    llm = LlmRouter(Settings(_env_file=None), clients={"nvidia": FakeAsyncOpenAI(script)})
    asyncio.run(run_agent(ctx, agent="critic", model_field="critic", system="s", task="t", llm=llm, max_iterations=1))
    return next(t for t in ctx.db.list_trace("test-app") if t.action == "tool_calls").tool_calls[0]


def test_loop_decodes_double_encoded_arguments_once():
    call = _loop_run(json.dumps(json.dumps({"text": "noted"})))
    assert call.ok is True and call.args == {"text": "noted"}


def test_loop_rejects_non_object_arguments():
    call = _loop_run("[1, 2]")
    assert call.ok is False and "JSON object" in call.result_summary
