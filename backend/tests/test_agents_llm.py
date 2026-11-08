import asyncio

import openai
import pytest
from fake_llm import FakeAsyncOpenAI

from app.agents import llm as llm_mod
from app.agents.llm import LlmRouter, RateLimited, _TokenBucket
from app.core.config import Settings


def _settings(**over) -> Settings:
    return Settings(_env_file=None, nvidia_api_key="super-secret-key", **over)


def test_token_bucket_refills_over_time(monkeypatch):
    wall = [0.0]
    monkeypatch.setattr(llm_mod.time, "monotonic", lambda: wall[0])
    b = _TokenBucket(rpm=60.0)  # 1 token/s, capacity 60, starts full
    for _ in range(60):
        assert b.try_take() is True
    assert b.try_take() is False  # capacity exhausted
    wall[0] += 2.0  # 2 more tokens accrue
    assert b.try_take() is True
    assert b.try_take() is True
    assert b.try_take() is False


def test_chat_raises_rate_limited_without_making_a_request(monkeypatch):
    wall = [0.0]
    monkeypatch.setattr(llm_mod.time, "monotonic", lambda: wall[0])
    fake = FakeAsyncOpenAI({"scenario": "s", "description": "d", "steps": [{"response": {"content": "ok"}}] * 5})
    router = LlmRouter(_settings(nvidia_rpm=1), clients={"nvidia": fake})

    asyncio.run(router.chat("critic", [{"role": "user", "content": "hi"}]))
    with pytest.raises(RateLimited):
        asyncio.run(router.chat("critic", [{"role": "user", "content": "hi"}]))
    assert len(fake.calls) == 1


def test_chat_passes_model_and_reasoning_switch():
    fake = FakeAsyncOpenAI({"scenario": "s", "description": "d", "steps": [{"response": {"content": "ok"}}]})
    router = LlmRouter(_settings(), clients={"nvidia": fake})

    asyncio.run(router.chat("critic", [{"role": "user", "content": "hi"}], enable_thinking=True, low_effort=False))
    assert fake.calls[0]["model"] == "nvidia/nemotron-3-super-120b-a12b"
    assert fake.calls[0]["extra_body"] == {"chat_template_kwargs": {"enable_thinking": True, "low_effort": False}}


def test_chat_shares_one_client_per_provider_across_agents():
    router = LlmRouter(_settings())
    a = router._provider("nvidia")
    b = router._provider("nvidia")
    assert a is b


async def _no_sleep(*_a, **_kw) -> None:
    return None


def test_chat_retries_retryable_status_then_succeeds(monkeypatch):
    monkeypatch.setattr(llm_mod.asyncio, "sleep", _no_sleep)
    fake = FakeAsyncOpenAI("server_errors_then_ok")
    router = LlmRouter(_settings(), clients={"nvidia": fake})

    result = asyncio.run(router.chat("critic", [{"role": "user", "content": "hi"}]))
    assert result.choices[0].message.content == "ok"
    assert len(fake.calls) == 3  # 500, 503, then ok -- MAX_ATTEMPTS=3 covers exactly this


def test_chat_does_not_retry_non_retryable_errors():
    fake = FakeAsyncOpenAI(
        {"scenario": "s", "description": "d", "steps": [{"error": {"status": 400, "message": "bad request"}}]}
    )
    router = LlmRouter(_settings(), clients={"nvidia": fake})

    with pytest.raises(openai.BadRequestError):
        asyncio.run(router.chat("critic", [{"role": "user", "content": "hi"}]))
    assert len(fake.calls) == 1


def test_log_startup_models_never_logs_the_api_key(caplog):
    router = LlmRouter(_settings())
    with caplog.at_level("INFO"):
        router.log_startup_models(["critic", "poi"])
    text = caplog.text
    assert "super-secret-key" not in text
    assert "nvidia/nemotron-3-super-120b-a12b" in text
