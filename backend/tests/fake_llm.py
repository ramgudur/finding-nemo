"""Deterministic stand-in for an OpenAI-compatible client, replaying recorded chat responses.

    from fake_llm import FakeOpenAI
    client = FakeOpenAI("critic_campus_alert")       # a script in tests/fixtures/llm/
    resp = client.chat.completions.create(model=..., messages=..., tools=...)
    client.calls                                      # every create() kwargs, for assertions

`agents/llm.py` takes one client per provider; tests inject `FakeOpenAI` (or `FakeAsyncOpenAI`)
instead of `openai.OpenAI`. Scripted errors raise the real `openai` exceptions so retry/backoff
code is exercised exactly as with the live API. No network.

Script format (JSON): {"scenario", "description", "steps": [step, ...]}, one step per create() call:
  {"response": {"content"?, "reasoning_content"?, "tool_calls"?: [{"name", "arguments" | "arguments_raw"}],
                "finish_reason"?}}
  {"error": {"status": 429 | 500 | 503 | ..., "message"}}
  optional "expect": {"model"?, "tools_include"?: [...], "reasoning"?: bool} checked against the request.

Placeholders inside tool-call arguments are resolved from the request, so a recording can refer to
values only known at run time:
  "${tool_result:<tool_call_id>:<dotted.path>}"  a field of an earlier tool result in `messages`
                                                 (looked up as given, then under "result")
  "${spec:<name>}"                               specs/<name>.json as a dict
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import httpx
import openai
from openai.types.chat import ChatCompletion

FIXTURES = Path(__file__).parent / "fixtures" / "llm"
SPECS = Path(__file__).resolve().parents[2] / "specs"
_PLACEHOLDER = re.compile(r"^\$\{(tool_result|spec):([^}]*)\}$")
_ERRORS: dict[int, type[openai.APIStatusError]] = {
    400: openai.BadRequestError,
    401: openai.AuthenticationError,
    404: openai.NotFoundError,
    429: openai.RateLimitError,
}


class ScriptExhausted(AssertionError):
    """More create() calls than recorded steps."""


class ExpectationFailed(AssertionError):
    """A request did not match the step's "expect" block."""


def load_script(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def scripts() -> list[str]:
    return sorted(p.stem for p in FIXTURES.glob("*.json"))


def _tool_results(messages: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for m in messages or []:
        if m.get("role") == "tool" and m.get("tool_call_id"):
            content = m.get("content")
            try:
                out[m["tool_call_id"]] = json.loads(content) if isinstance(content, str) else content
            except json.JSONDecodeError:
                out[m["tool_call_id"]] = content
    return out


def _dig(obj: Any, path: str) -> Any:
    for part in path.split(".") if path else []:
        obj = obj[int(part)] if isinstance(obj, list) else obj[part]
    return obj


def _resolve(value: Any, results: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {k: _resolve(v, results) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, results) for v in value]
    if not isinstance(value, str) or not (m := _PLACEHOLDER.match(value)):
        return value
    kind, ref = m.groups()
    if kind == "spec":
        return json.loads((SPECS / f"{ref}.json").read_text(encoding="utf-8"))
    call_id, _, path = ref.partition(":")
    if call_id not in results:
        raise ExpectationFailed(f"placeholder {value!r}: no tool result for {call_id!r} in the request messages")
    for p in (path, f"result.{path}" if path else "result"):
        try:
            return _dig(results[call_id], p)
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    raise ExpectationFailed(f"placeholder {value!r}: path {path!r} not in tool result {results[call_id]!r}")


def _check(expect: dict[str, Any], kwargs: dict[str, Any], where: str) -> None:
    if "model" in expect and kwargs.get("model") != expect["model"]:
        raise ExpectationFailed(f"{where}: model {kwargs.get('model')!r} != {expect['model']!r}")
    if "tools_include" in expect:
        offered = {t["function"]["name"] for t in kwargs.get("tools") or []}
        missing = set(expect["tools_include"]) - offered
        if missing:
            raise ExpectationFailed(f"{where}: tools {sorted(missing)} not offered")
    if "reasoning" in expect:
        ctk = (kwargs.get("extra_body") or {}).get("chat_template_kwargs") or {}
        if bool(ctk.get("enable_thinking")) != expect["reasoning"]:
            raise ExpectationFailed(f"{where}: enable_thinking {ctk.get('enable_thinking')!r} != {expect['reasoning']}")


def _error(status: int, message: str) -> openai.APIStatusError:
    req = httpx.Request("POST", "https://fake.invalid/v1/chat/completions")
    resp = httpx.Response(status, request=req, json={"error": {"message": message}})
    cls = _ERRORS.get(status, openai.InternalServerError if status >= 500 else openai.APIStatusError)
    return cls(message, response=resp, body={"error": {"message": message}})


class _Replay:
    def __init__(self, script: str | dict[str, Any]) -> None:
        self.script = load_script(script) if isinstance(script, str) else copy.deepcopy(script)
        self.name = self.script.get("scenario", "inline")
        self.steps: list[dict[str, Any]] = self.script["steps"]
        self.calls: list[dict[str, Any]] = []

    @property
    def remaining(self) -> int:
        return len(self.steps) - len(self.calls)

    def next(self, kwargs: dict[str, Any]) -> ChatCompletion:
        i = len(self.calls)
        self.calls.append(copy.deepcopy(kwargs))
        if i >= len(self.steps):
            raise ScriptExhausted(f"{self.name}: call {i + 1} but only {len(self.steps)} steps recorded")
        step = self.steps[i]
        where = f"{self.name} step {i + 1}"
        _check(step.get("expect", {}), kwargs, where)
        if "error" in step:
            raise _error(int(step["error"]["status"]), step["error"].get("message", "scripted error"))
        return self._completion(step["response"], kwargs, i)

    def _completion(self, r: dict[str, Any], kwargs: dict[str, Any], i: int) -> ChatCompletion:
        results = _tool_results(kwargs.get("messages", []))
        tool_calls = []
        for j, tc in enumerate(r.get("tool_calls") or []):
            if "arguments_raw" in tc:
                args = tc["arguments_raw"]
            else:
                args = json.dumps(_resolve(tc.get("arguments", {}), results), sort_keys=True)
            tool_calls.append(
                {
                    "id": tc.get("id", f"call_{self.name}_{i + 1}_{j + 1}"),
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": args},
                }
            )
        message: dict[str, Any] = {"role": "assistant", "content": r.get("content")}
        if tool_calls:
            message["tool_calls"] = tool_calls
        if "reasoning_content" in r:
            message["reasoning_content"] = r["reasoning_content"]
        finish = r.get("finish_reason") or ("tool_calls" if tool_calls else "stop")
        prompt_chars = len(json.dumps(kwargs.get("messages", []), default=str))
        completion_chars = len(json.dumps(message, default=str))
        return ChatCompletion.model_validate(
            {
                "id": f"chatcmpl-{self.name}-{i + 1}",
                "object": "chat.completion",
                "created": 1_800_000_000 + i,
                "model": kwargs.get("model", "fake-model"),
                "choices": [{"index": 0, "message": message, "finish_reason": finish, "logprobs": None}],
                "usage": {
                    "prompt_tokens": prompt_chars // 4,
                    "completion_tokens": completion_chars // 4,
                    "total_tokens": (prompt_chars + completion_chars) // 4,
                },
            }
        )


class _Completions:
    def __init__(self, replay: _Replay) -> None:
        self._replay = replay

    def create(self, **kwargs: Any) -> ChatCompletion:
        return self._replay.next(kwargs)


class _AsyncCompletions(_Completions):
    async def create(self, **kwargs: Any) -> ChatCompletion:  # type: ignore[override]
        return self._replay.next(kwargs)


class _Chat:
    def __init__(self, completions: _Completions) -> None:
        self.completions = completions


class FakeOpenAI:
    """Sync `openai.OpenAI` look-alike: only `chat.completions.create` is implemented."""

    _completions_cls = _Completions

    def __init__(self, script: str | dict[str, Any]) -> None:
        self._replay = _Replay(script)
        self.chat = _Chat(self._completions_cls(self._replay))

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self._replay.calls

    @property
    def remaining(self) -> int:
        return self._replay.remaining


class FakeAsyncOpenAI(FakeOpenAI):
    """Async `openai.AsyncOpenAI` look-alike."""

    _completions_cls = _AsyncCompletions
