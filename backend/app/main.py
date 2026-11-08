"""FastAPI entry point: wires the DB-backed event store into the engine tick loop and
mounts the routers. `create_app` is a factory so tests can swap in an isolated `Db` and
control whether the tick loop auto-starts; `app` is the module-level instance uvicorn runs."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.agents.compiler import CompilerCache
from app.agents.critic import CriticScheduler
from app.agents.llm import LlmRouter
from app.agents.poi_labeler import PoiLabelerScheduler
from app.agents.scout import ScoutRunner, article_extractor
from app.api import apps as apps_api
from app.api import catalog as catalog_api
from app.api import debug as debug_api
from app.api import ingest as ingest_api
from app.api import instructions as instructions_api
from app.api import labels as labels_api
from app.api import outputs as outputs_api
from app.api import scout as scout_api
from app.api import simulate as simulate_api
from app.api import trace as trace_api
from app.api import webhooks as webhooks_api
from app.api import ws as ws_api
from app.api.compile_gate import CompileGate
from app.api.instruction_trace import InstructionTraceDedup
from app.connectors.engine import ConnectorEngine, make_extract_locator
from app.core.config import get_settings
from app.core.db import Db
from app.engine.operators.base import Events
from app.engine.retention import RetentionRunner
from app.engine.tick import TickEngine, empty_events
from app.tools.geo_tools import geocode_place

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
    llm: LlmRouter | None = None,
    compiler_cache: CompilerCache | None = None,
    poi_labeler_scheduler: PoiLabelerScheduler | None = None,
    connector_engine: ConnectorEngine | None = None,
    scout_runner: ScoutRunner | None = None,
    compile_gate: CompileGate | None = None,
    retention_runner: RetentionRunner | None = None,
    autostart_engine: bool = True,
) -> FastAPI:
    db = db if db is not None else Db(get_settings().sqlite_path)
    engine = engine if engine is not None else TickEngine(DbEventSource(db), trust=db.list_trust)
    critic_scheduler = critic_scheduler if critic_scheduler is not None else CriticScheduler(db, engine)
    # One LlmRouter for every agent (its per-provider token buckets are the shared rate budget):
    # the Critic's by default, unless a caller (tests, mainly) injects its own.
    llm = llm if llm is not None else critic_scheduler.llm
    # A fresh cache per app instance (never the module-level default) so tests never leak
    # a compiled spec across unrelated test functions sharing the same process.
    compiler_cache = compiler_cache if compiler_cache is not None else CompilerCache()
    poi_labeler_scheduler = (
        poi_labeler_scheduler if poi_labeler_scheduler is not None else PoiLabelerScheduler(db, engine, llm=llm)
    )
    # RSS items without coordinates: the Scout's per-article extraction + an area-bounded geocoder (#135)
    connector_engine = (
        connector_engine
        if connector_engine is not None
        else ConnectorEngine(db, locate=make_extract_locator(article_extractor(db, engine, llm=llm), geocode_place))
    )
    scout_runner = scout_runner if scout_runner is not None else ScoutRunner(db, engine, llm=llm)
    if retention_runner is None:
        settings = get_settings()
        retention_runner = RetentionRunner(
            db,
            engine,
            interval_s=settings.retention_interval_s,
            event_floor_s=settings.event_retention_s,
            trace_keep=settings.trace_keep_per_app,
        )

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
            scout_runner.start()
            retention_runner.start()  # data retention (#213): a background pass, never in the tick
        yield
        if autostart_engine:
            # Reverse order; one loop failing to stop must not leave the others running.
            for loop_ in (
                retention_runner,
                scout_runner,
                connector_engine,
                poi_labeler_scheduler,
                critic_scheduler,
                engine,
            ):
                try:
                    await loop_.stop()
                except Exception:  # noqa: BLE001
                    log.exception("failed to stop %s", type(loop_).__name__)

    fastapi_app = FastAPI(title="Geo heat-map platform", version="0.1.0", lifespan=lifespan)
    fastapi_app.state.db = db
    fastapi_app.state.engine = engine
    fastapi_app.state.critic_scheduler = critic_scheduler
    fastapi_app.state.llm = llm
    fastapi_app.state.compiler_cache = compiler_cache
    fastapi_app.state.poi_labeler_scheduler = poi_labeler_scheduler
    fastapi_app.state.connector_engine = connector_engine
    fastapi_app.state.scout_runner = scout_runner
    # Per-app compile cooldown (#169): one in flight, COMPILE_MIN_GAP_S apart (optional, default 10 s).
    fastapi_app.state.compile_gate = (
        compile_gate if compile_gate is not None else CompileGate(get_settings().compile_min_gap_s)
    )
    fastapi_app.state.instruction_trace_dedup = InstructionTraceDedup()  # per app instance (#169)
    fastapi_app.state.retention_runner = retention_runner
    fastapi_app.include_router(apps_api.router)
    fastapi_app.include_router(ingest_api.router)
    fastapi_app.include_router(outputs_api.router)
    fastapi_app.include_router(ws_api.router)
    fastapi_app.include_router(instructions_api.router)  # public end-user instructions
    fastapi_app.include_router(trace_api.router)  # authority-only console reads + dev pin
    fastapi_app.include_router(scout_api.router)  # dev-requested Scout runs (authority only)
    fastapi_app.include_router(labels_api.router)  # authority-only label proposals (accept/dismiss)
    fastapi_app.include_router(webhooks_api.router)  # real webhook receiver (#222)
    fastapi_app.include_router(catalog_api.router)  # public operator catalogue
    fastapi_app.include_router(simulate_api.router)  # dev-requested simulated events (authority only, #268)
    if get_settings().demo_mode:  # /v1/debug/* exists only in demo mode; it alone reads sim ground truth (rule 9)
        debug_api.install(fastapi_app, engine)

    @fastapi_app.get("/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return fastapi_app


app = create_app()
