"""FastAPI entry point: wires the DB-backed event store into the engine tick loop and
mounts the routers. `create_app` is a factory so tests can swap in an isolated `Db` and
control whether the tick loop auto-starts; `app` is the module-level instance uvicorn runs."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.agents.critic import CriticScheduler
from app.agents.poi_labeler import PoiLabelerScheduler
from app.api import apps as apps_api
from app.api import debug as debug_api
from app.api import ingest as ingest_api
from app.api import outputs as outputs_api
from app.api import trace as trace_api
from app.api import ws as ws_api
from app.connectors.engine import ConnectorEngine
from app.core.config import get_settings
from app.core.db import Db
from app.engine.operators.base import Events
from app.engine.tick import TickEngine, empty_events

log = logging.getLogger(__name__)


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


def create_app(
    *,
    db: Db | None = None,
    engine: TickEngine | None = None,
    critic_scheduler: CriticScheduler | None = None,
    poi_labeler_scheduler: PoiLabelerScheduler | None = None,
    connector_engine: ConnectorEngine | None = None,
    autostart_engine: bool = True,
) -> FastAPI:
    db = db if db is not None else Db(get_settings().sqlite_path)
    engine = engine if engine is not None else TickEngine(DbEventSource(db), trust=db.list_trust)
    critic_scheduler = critic_scheduler if critic_scheduler is not None else CriticScheduler(db, engine)
    # One LlmRouter for every agent: its per-provider token buckets are the shared rate budget.
    poi_labeler_scheduler = (
        poi_labeler_scheduler
        if poi_labeler_scheduler is not None
        else PoiLabelerScheduler(db, engine, llm=critic_scheduler.llm)
    )
    connector_engine = connector_engine if connector_engine is not None else ConnectorEngine(db)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if autostart_engine:
            for spec in db.list_apps():
                if spec.status == "active":
                    engine.add_app(spec)
            critic_scheduler.llm.log_startup_models(
                ["compiler", "critic", "scout", "extract", "poi", "repair", "safety"]
            )
            engine.start()
            critic_scheduler.start()
            poi_labeler_scheduler.start()
            connector_engine.start()
        yield
        if autostart_engine:
            # Reverse order; one loop failing to stop must not leave the others running.
            for loop_ in (connector_engine, poi_labeler_scheduler, critic_scheduler, engine):
                try:
                    await loop_.stop()
                except Exception:  # noqa: BLE001
                    log.exception("failed to stop %s", type(loop_).__name__)

    fastapi_app = FastAPI(title="Geo heat-map platform", version="0.1.0", lifespan=lifespan)
    fastapi_app.state.db = db
    fastapi_app.state.engine = engine
    fastapi_app.state.critic_scheduler = critic_scheduler
    fastapi_app.state.poi_labeler_scheduler = poi_labeler_scheduler
    fastapi_app.state.connector_engine = connector_engine
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
