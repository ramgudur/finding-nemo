"""`llm.py`: one OpenAI-SDK client per provider, chosen per agent from `*_MODEL` config.

Agent code never hardcodes a model or provider: `LlmRouter.chat(agent, messages, ...)`
looks up `Settings.agent_model(agent)` and dispatches to that provider's client. A token
bucket per provider (`NVIDIA_RPM`, `OPENROUTER_RPM`) throttles requests; `chat()` raises
`RateLimited` instead of blocking when the bucket is empty, so a caller (`agents/loop.py`)
can skip the step rather than stall the tick (rule 2: no LLM in the hot path). 429/5xx
responses get capped, backed-off retries. The reasoning switch is
`extra_body={"chat_template_kwargs": {"enable_thinking": bool, "low_effort"?: bool}}`.

Never log API keys. `log_startup_models` logs only the resolved model IDs.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion

from app.core.config import AgentField, Settings, get_settings

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BASE_BACKOFF_S = 1.0
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class RateLimited(Exception):
    """A provider's token bucket is empty; the caller should skip this step, not wait."""


@dataclass
class _TokenBucket:
    rpm: float
    tokens: float = field(init=False)
    updated_at: float = field(init=False)

    def __post_init__(self) -> None:
        self.tokens = max(1.0, self.rpm)
        self.updated_at = time.monotonic()

    def try_take(self) -> bool:
        capacity = max(1.0, self.rpm)
        now = time.monotonic()
        self.tokens = min(capacity, self.tokens + (now - self.updated_at) * (self.rpm / 60.0))
        self.updated_at = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


@dataclass
class _Provider:
    client: Any  # an AsyncOpenAI, or an async look-alike (tests inject FakeAsyncOpenAI)
    bucket: _TokenBucket


class LlmRouter:
    """One async OpenAI-compatible client + token bucket per provider named in `settings`.

    `clients` lets a caller inject a client per provider instead of a real
    `AsyncOpenAI` -- tests pass `{"nvidia": FakeAsyncOpenAI(script), ...}`
    (`tests/fake_llm.py`) so no network call is ever made and scripted 429/5xx raise the
    real `openai` exceptions, exercising the retry path exactly as with the live API.
    """

    def __init__(self, settings: Settings | None = None, clients: dict[str, Any] | None = None) -> None:
        self.settings = settings or get_settings()
        self._injected = dict(clients or {})
        self._providers: dict[str, _Provider] = {}

    def _provider(self, name: str) -> _Provider:
        if name not in self._providers:
            client = self._injected.get(name)
            if client is None:
                base_url = getattr(self.settings, f"{name}_base_url", None)
                api_key = getattr(self.settings, f"{name}_api_key", "") or "unset"
                client = AsyncOpenAI(base_url=base_url, api_key=api_key)
            self._providers[name] = _Provider(client=client, bucket=_TokenBucket(self.settings.rpm(name)))
        return self._providers[name]

    def log_startup_models(self, agents: list[AgentField]) -> None:
        """Log each agent's resolved (provider, model_id) once at startup. Never logs a key."""
        for a in agents:
            m = self.settings.agent_model(a)
            log.info("agent=%s provider=%s model=%s", a, m.provider, m.model_id)

    async def chat(
        self,
        agent: AgentField,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        enable_thinking: bool = False,
        low_effort: bool | None = None,
        **kwargs: Any,
    ) -> ChatCompletion:
        """One chat completion for `agent`'s configured model.

        Raises `RateLimited` (without making a request) when the provider's bucket is
        empty. Retries 429/5xx up to `MAX_ATTEMPTS` with exponential backoff; any other
        error propagates immediately.
        """
        model = self.settings.agent_model(agent)
        provider = self._provider(model.provider)
        if not provider.bucket.try_take():
            raise RateLimited(f"{model.provider} rate budget is empty")

        chat_template_kwargs: dict[str, Any] = {"enable_thinking": enable_thinking}
        if low_effort is not None:
            chat_template_kwargs["low_effort"] = low_effort

        last_exc: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                return await provider.client.chat.completions.create(
                    model=model.model_id,
                    messages=messages,
                    tools=tools,
                    extra_body={"chat_template_kwargs": chat_template_kwargs},
                    **kwargs,
                )
            except Exception as e:  # noqa: BLE001 - retried only for known-retryable statuses, below
                last_exc = e
                status = getattr(e, "status_code", None)
                if status not in _RETRYABLE_STATUS or attempt == MAX_ATTEMPTS - 1:
                    raise
                await asyncio.sleep(BASE_BACKOFF_S * (2**attempt) + random.uniform(0, 0.25))
        raise last_exc  # pragma: no cover - the loop above always returns or raises
