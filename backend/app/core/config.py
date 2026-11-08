"""Settings loaded from the environment (``backend/.env``), via pydantic-settings.

Canonical list of variables: CLAUDE.md "Environment". Per-agent ``*_MODEL`` values are
``"<provider>::<model-id>"``; agent code never hardcodes a model, it reads one of these
through :meth:`Settings.agent_model`.

Never log API keys. :meth:`Settings.masked` returns a dict safe to log or print; the
default ``repr``/``str`` are also redacted for the same reason.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal, NamedTuple

from pydantic_settings import BaseSettings, SettingsConfigDict

AgentField = Literal["compiler", "critic", "scout", "extract", "poi", "repair", "safety"]

_SECRET_FIELDS = frozenset({"nvidia_api_key", "openrouter_api_key", "tavily_api_key"})


class AgentModel(NamedTuple):
    provider: str
    model_id: str


def _parse_agent_model(raw: str) -> AgentModel:
    provider, sep, model_id = raw.partition("::")
    if not sep or not provider or not model_id:
        raise ValueError(f"expected '<provider>::<model-id>', got {raw!r}")
    return AgentModel(provider, model_id)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    compiler_model: str = "nvidia::nvidia/nemotron-3.5-super-vl-120b-a12b"
    critic_model: str = "nvidia::nvidia/nemotron-3.5-super-vl-120b-a12b"
    scout_model: str = "nvidia::nvidia/nemotron-3.5-super-vl-120b-a12b"
    extract_model: str = "nvidia::nvidia/nemotron-3.5-lightning-30b-a3b"
    poi_model: str = "nvidia::nvidia/nemotron-3.5-lightning-30b-a3b"
    repair_model: str = "nvidia::nvidia/nemotron-3.5-lightning-30b-a3b"
    safety_model: str = "nvidia::nvidia/llama-3.1-nemotron-safety-guard-8b-v3"

    nvidia_rpm: int = 35
    openrouter_rpm: int = 18

    tavily_api_key: str = ""
    nominatim_user_agent: str = ""

    sqlite_path: str = "./data/app.db"
    # Data retention (#213), all optional. Events of push inputs are kept for at least
    # event_retention_s (more when an operator's horizon needs it); trace keeps the newest
    # trace_keep_per_app entries per app; cleanup runs every retention_interval_s (0 = off).
    event_retention_s: float = 86_400.0
    trace_keep_per_app: int = 20_000
    retention_interval_s: float = 300.0
    demo_mode: bool = False

    # Per-app gap between compiles (seconds; 0 disables the gap, one-in-flight still holds). Optional.
    compile_min_gap_s: float = 10.0

    def agent_model(self, agent: AgentField) -> AgentModel:
        """The ``(provider, model_id)`` an agent should use, from its ``*_MODEL`` setting."""
        return _parse_agent_model(getattr(self, f"{agent}_model"))

    def rpm(self, provider: str) -> int:
        """Requests-per-minute budget for a provider name (``"nvidia"`` / ``"openrouter"``)."""
        attr = f"{provider}_rpm"
        if not hasattr(self, attr):
            raise ValueError(f"unknown provider {provider!r}")
        return getattr(self, attr)

    def masked(self) -> dict[str, Any]:
        """``model_dump()`` with API keys redacted. Safe to log."""
        return {k: ("***" if k in _SECRET_FIELDS and v else v) for k, v in self.model_dump().items()}

    def __repr__(self) -> str:
        fields = ", ".join(f"{k}={v!r}" for k, v in self.masked().items())
        return f"Settings({fields})"

    __str__ = __repr__


@lru_cache
def get_settings() -> Settings:
    return Settings()
