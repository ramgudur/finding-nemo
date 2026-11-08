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
    assert fake.calls[0]["model"] == "nvidia/nemotron-3.5-super-vl-120b-a12b"
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


def test_token_bucket_uses_an_injected_clock_over_time_monotonic(monkeypatch):
    # time.monotonic must be ignored once a clock is injected (#221: LlmRouter's fake-clock tests)
    monkeypatch.setattr(llm_mod.time, "monotonic", lambda: 999_999.0)
    fake_now = [0.0]
    b = _TokenBucket(rpm=60.0, clock=lambda: fake_now[0])
    for _ in range(60):
        assert b.try_take() is True
    assert b.try_take() is False
    fake_now[0] += 2.0
    assert b.available() == pytest.approx(2.0)


def test_admit_requires_headroom_by_priority():
    router = LlmRouter(_settings(nvidia_rpm=60))
    # capacity 60; interactive needs no headroom, triggered needs 15% (9), scheduled needs 40% (24)
    router._provider("nvidia").bucket.tokens = 20.0
    assert router.admit("critic", 6, "interactive") is True  # needs 6 <= 20
    assert router.admit("critic", 6, "triggered") is True  # needs 6 + 9 = 15 <= 20
    assert router.admit("critic", 6, "scheduled") is False  # needs 6 + 24 = 30 > 20


def test_admit_does_not_take_tokens():
    router = LlmRouter(_settings(nvidia_rpm=60))
    before = router._provider("nvidia").bucket.tokens
    assert router.admit("critic", 6, "interactive") is True
    assert router._provider("nvidia").bucket.tokens == before


def test_admit_false_when_the_bucket_is_too_low_for_the_whole_run():
    router = LlmRouter(_settings(nvidia_rpm=60))
    router._provider("nvidia").bucket.tokens = 3.0
    assert router.admit("critic", 6, "interactive") is False


def test_rpm_reads_the_providers_configured_rate():
    router = LlmRouter(_settings(nvidia_rpm=17))
    assert router.rpm("critic") == 17


def test_log_startup_models_never_logs_the_api_key(caplog):
    router = LlmRouter(_settings())
    with caplog.at_level("INFO"):
        router.log_startup_models(["critic", "poi"])
    text = caplog.text
    assert "super-secret-key" not in text
    assert "nvidia/nemotron-3.5-super-vl-120b-a12b" in text
