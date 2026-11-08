"""Shared FastAPI dependencies: reach the app-wide ``Db`` and ``TickEngine`` (set on
``app.state`` by ``main.create_app``), and read the standard headers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Header, Request

from app.agents.llm import LlmRouter
from app.core.db import Db
from app.engine.tick import TickEngine

if TYPE_CHECKING:
    # app.agents.compiler -> app.agents.loop -> app.tools.registry -> app.tools.compiler_tools
    # -> app.connectors.engine -> app.api.ingest -> app.api.deps: a real import here is circular.
    from app.agents.compiler import CompilerCache


def get_db(request: Request) -> Db:
    return request.app.state.db


def get_engine(request: Request) -> TickEngine:
    return request.app.state.engine


def get_llm(request: Request) -> LlmRouter:
    return request.app.state.llm


def get_compiler_cache(request: Request) -> CompilerCache:
    return request.app.state.compiler_cache


def authority_key_header(x_authority_key: str | None = Header(default=None, alias="X-Authority-Key")) -> str | None:
    return x_authority_key


def ingest_key_header(x_ingest_key: str | None = Header(default=None, alias="X-Ingest-Key")) -> str | None:
    return x_ingest_key
