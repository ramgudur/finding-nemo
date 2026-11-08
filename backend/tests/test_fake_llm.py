"""The recorded LLM fixtures and the fake client that replays them (tests/fake_llm.py)."""

import asyncio
import json

import openai
import pytest
from fake_llm import _PLACEHOLDER as PLACEHOLDER
from fake_llm import (
    SPECS,
    ExpectationFailed,
    FakeAsyncOpenAI,
    FakeOpenAI,
    ScriptExhausted,
    load_script,
    scripts,
)
from pydantic import ValidationError

from app.tools import registry

THINK = {"chat_template_kwargs": {"enable_thinking": True}}
NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}


def _tools(*names):
    return [{"type": "function", "function": {"name": n, "description": "", "parameters": {}}} for n in names]


def _offer_all(script):
    names = set()
    for step in script["steps"]:
        names |= set(step.get("expect", {}).get("tools_include", []))
    return _tools(*sorted(names))


def _tool_msg(call_id, payload):
    return {"role": "tool", "tool_call_id": call_id, "content": json.dumps(payload)}


@pytest.mark.parametrize("name", scripts())
def test_every_script_is_well_formed(name):
    s = load_script(name)
    assert s["scenario"] == name and s["description"] and s["steps"]
    for step in s["steps"]:
        assert ("response" in step) != ("error" in step), step
        for tc in (step.get("response") or {}).get("tool_calls") or []:
            assert tc["name"] and ("arguments" in tc or "arguments_raw" in tc)


def _resolve_for_schema_check(v):
    """A ``${spec:name}`` placeholder resolves to the real spec dict (we know it statically,
    unlike a ``${tool_result:...}`` placeholder, which needs a live conversation); anything
    else just becomes a type-agnostic marker string."""
    if isinstance(v, str) and (m := PLACEHOLDER.match(v)):
        kind, ref = m.groups()
        if kind == "spec":
            return json.loads((SPECS / f"{ref}.json").read_text(encoding="utf-8"))
        return "placeholder"
    return v


@pytest.mark.parametrize("name", scripts())
def test_registered_tool_calls_validate_against_the_real_arg_models(name):
    """Recordings must not drift from tools/registry.py. Tools not registered yet are checked once they are."""
    for step in load_script(name)["steps"]:
        for tc in (step.get("response") or {}).get("tool_calls") or []:
            tool = registry.TOOLS.get(tc["name"])
            if tool is None or "arguments_raw" in tc:
                continue
            args = {k: _resolve_for_schema_check(v) for k, v in tc["arguments"].items()}
            try:
                tool.args.model_validate(args)
            except ValidationError as e:
                pytest.fail(f"{name}: {tc['name']} args invalid: {e}")


def test_critic_replay_resolves_the_backtest_id_from_the_tool_result():
    client = FakeOpenAI("critic_campus_alert")
    tools = _offer_all(load_script("critic_campus_alert"))
    msgs = [{"role": "user", "content": "run"}]

    r1 = client.chat.completions.create(model="m", messages=msgs, tools=tools, extra_body=THINK)
    m1 = r1.choices[0].message
    assert r1.choices[0].finish_reason == "tool_calls"
    assert [t.function.name for t in m1.tool_calls] == ["get_map_summary", "get_suspicious_users"]
    assert m1.reasoning_content  # extra field survives, like the real NVIDIA endpoint

    r2 = client.chat.completions.create(model="m", messages=msgs, tools=tools, extra_body=THINK)
    calls = {t.id: t for t in r2.choices[0].message.tool_calls}
    assert json.loads(calls["call_c4"].function.arguments)["weights"] == {
        "panic.flow_source": 0.65,
        "panic.density": 0.35,
    }

    msgs += [_tool_msg("call_c4", {"ok": True, "result": {"backtest_id": "bt-123", "passed": True}})]
    r3 = client.chat.completions.create(model="m", messages=msgs, tools=tools, extra_body=THINK)
    args = json.loads(r3.choices[0].message.tool_calls[0].function.arguments)
    assert args["backtest_id"] == "bt-123"

    r4 = client.chat.completions.create(model="m", messages=msgs, tools=tools, extra_body=THINK)
    assert r4.choices[0].finish_reason == "stop" and r4.choices[0].message.content
    assert client.remaining == 0 and len(client.calls) == 4


def test_missing_tool_result_for_a_placeholder_fails_loudly():
    client = FakeOpenAI("critic_campus_alert")
    for _ in range(2):
        client.chat.completions.create(
            model="m", messages=[], tools=_offer_all(load_script("critic_campus_alert")), extra_body=THINK
        )
    with pytest.raises(ExpectationFailed, match="call_c4"):
        client.chat.completions.create(model="m", messages=[], extra_body=THINK)


def test_spec_placeholder_inlines_the_spec():
    client = FakeOpenAI("compiler_campus_alert")
    tools = _offer_all(load_script("compiler_campus_alert"))
    client.chat.completions.create(model="m", messages=[], tools=tools, extra_body=THINK)
    r = client.chat.completions.create(model="m", messages=[], tools=tools, extra_body=THINK)
    spec = json.loads(r.choices[0].message.tool_calls[0].function.arguments)["spec"]
    assert spec["app_id"] == "campus-alert" and spec["inputs"]


def test_expectations_catch_wrong_requests():
    with pytest.raises(ExpectationFailed, match="enable_thinking"):
        FakeOpenAI("extract_article").chat.completions.create(model="m", messages=[], extra_body=THINK)
    with pytest.raises(ExpectationFailed, match="not offered"):
        FakeOpenAI("critic_campus_alert").chat.completions.create(
            model="m", messages=[], tools=_tools("annotate"), extra_body=THINK
        )


@pytest.mark.parametrize(
    ("name", "errors"),
    [
        ("rate_limited_then_ok", [openai.RateLimitError]),
        ("server_errors_then_ok", [openai.InternalServerError, openai.InternalServerError]),
    ],
)
def test_scripted_errors_raise_real_openai_exceptions(name, errors):
    client = FakeOpenAI(name)
    for exc, status in zip(
        errors, [s["error"]["status"] for s in load_script(name)["steps"] if "error" in s], strict=True
    ):
        with pytest.raises(exc) as info:
            client.chat.completions.create(model="m", messages=[])
        assert info.value.status_code == status
    assert client.chat.completions.create(model="m", messages=[]).choices[0].message.content == "ok"


def test_malformed_then_repair_and_raw_arguments():
    client = FakeOpenAI("malformed_then_repair")
    bad = client.chat.completions.create(model="agent", messages=[]).choices[0].message.content
    with pytest.raises(json.JSONDecodeError):
        json.loads(bad)
    good = client.chat.completions.create(model="repair", messages=[]).choices[0].message.content
    assert json.loads(good) == {"label": "example", "confidence": 0.7}
    assert [c["model"] for c in client.calls] == ["agent", "repair"]

    raw = FakeOpenAI("bad_tool_arguments").chat.completions.create(model="m", messages=[])
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw.choices[0].message.tool_calls[0].function.arguments)


def test_exhaustion_and_recorded_calls():
    client = FakeOpenAI("rate_limited_then_ok")
    with pytest.raises(openai.RateLimitError):
        client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}], extra_body=NO_THINK)
    client.chat.completions.create(model="m", messages=[])
    with pytest.raises(ScriptExhausted):
        client.chat.completions.create(model="m", messages=[])
    assert client.calls[0]["extra_body"] == NO_THINK and client.calls[0]["messages"][0]["content"] == "hi"


def test_replay_is_deterministic():
    def run():
        c = FakeOpenAI("poi_labeler_campus_alert")
        tools = _offer_all(load_script("poi_labeler_campus_alert"))
        msgs = [_tool_msg("call_l1", {"ok": True, "result": {"candidates": [{"id": "cand-7"}]}})]
        return [c.chat.completions.create(model="m", messages=msgs, tools=tools).model_dump() for _ in range(4)]

    first, second = run(), run()
    assert first == second
    args = json.loads(first[2]["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])
    assert args["candidate_id"] == "cand-7" and args["label"] == "threat origin"


def test_async_client_replays_the_same_script():
    async def go():
        c = FakeAsyncOpenAI("extract_article")
        r = await c.chat.completions.create(model="m", messages=[], extra_body=NO_THINK)
        return json.loads(r.choices[0].message.content)

    out = asyncio.run(go())
    assert out["relevant"] is True and 0 <= out["confidence"] <= 1
