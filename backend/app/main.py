"""FastAPI entry point: wires the DB-backed event store into the engine tick loop and
mounts the routers. `create_app` is a factory so tests can swap in an isolated `Db` and
control whether the tick loop auto-starts; `app` is the module-level instance uvicorn runs."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import apps as apps_api
from app.api import debug as debug_api
from app.api import ingest as ingest_api
from app.api import outputs as outputs_api
from app.api import trace as trace_api
from app.api import ws as ws_api
from app.core.config import get_settings
from app.core.db import Db
from app.engine.operators.base import Events
from app.engine.tick import TickEngine, empty_events


class DbEventSource:
    """Adapts `core/db.py`'s event log to the `EventSource` protocol the tick loop reads."""

    def __init__(self, db: Db) -> None:
        self.db = db

    def events(self, app_id: str, input_id: str, since: float, until: float) -> Events:
        rows = self.db.list_events(app_id, input_id, since=since)
        records = [
            {"user_id": e.user_id, "t": e.ts.timestamp(), "lat": e.lat, "lon": e.lon, **(e.model_extra or {})}
            for e in rows
            if e.ts.timestamp() <= until
        ]
        return Events.from_records(records) if records else empty_events()


def create_app(*, db: Db | None = None, engine: TickEngine | None = None, autostart_engine: bool = True) -> FastAPI:
    db = db if db is not None else Db(get_settings().sqlite_path)
    engine = engine if engine is not None else TickEngine(DbEventSource(db), trust=db.list_trust)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if autostart_engine:
            for spec in db.list_apps():
                if spec.status == "active":
                    engine.add_app(spec)
            engine.start()
        yield
        if autostart_engine:
            await engine.stop()

    fastapi_app = FastAPI(title="Geo heat-map platform", version="0.1.0", lifespan=lifespan)
    fastapi_app.state.db = db
    fastapi_app.state.engine = engine
    fastapi_app.include_router(apps_api.router)
    fastapi_app.include_router(ingest_api.router)
    fastapi_app.include_router(outputs_api.router)
    fastapi_app.include_router(ws_api.router)
    fastapi_app.include_router(trace_api.router)  # authority-only console reads + dev pin
    if get_settings().demo_mode:  # /v1/debug/* exists only in demo mode; it alone reads sim ground truth (rule 9)
        debug_api.install(fastapi_app, engine)

    @fastapi_app.get("/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return fastapi_app


app = create_app()
