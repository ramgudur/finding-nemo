"""Shared FastAPI dependencies: reach the app-wide ``Db`` and ``TickEngine`` (set on
``app.state`` by ``main.create_app``), and read the standard headers."""

from __future__ import annotations

from fastapi import Header, Request

from app.core.db import Db
from app.engine.tick import TickEngine


def get_db(request: Request) -> Db:
    return request.app.state.db


def get_engine(request: Request) -> TickEngine:
    return request.app.state.engine


def authority_key_header(x_authority_key: str | None = Header(default=None, alias="X-Authority-Key")) -> str | None:
    return x_authority_key


def ingest_key_header(x_ingest_key: str | None = Header(default=None, alias="X-Ingest-Key")) -> str | None:
    return x_ingest_key
