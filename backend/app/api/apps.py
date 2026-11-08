"""`api/apps.py`: create, compile, and activate an app.

A spec reaches `pending_confirmation` either hand-written (`POST /{app}`, e.g. the
campus-alert spec file) or via the Compiler (`POST /{app}/compile`, `agents.compiler`).
Either way, only `POST /{app}/activate` is dev confirmation: it generates the
ingest/authority keys (shown once), starts the app ticking, and is the only place that
writes the `spec_confirm` Change.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from app.agents.compiler import CompilerCache, run_compiler
from app.agents.llm import LlmRouter
from app.agents.source_suggester import suggest_source
from app.api.deps import authority_key_header, get_compiler_cache, get_db, get_engine, get_llm
from app.connectors import engine as connector_engine
from app.connectors import url_guard
from app.core import clock as clock_mod
from app.core.access import is_authority
from app.core.db import Db
from app.core.models import AppSpec, Change, CompileRequest, Model, SourceSuggestion, SuggestSourceRequest
from app.engine.tick import TickEngine
from app.tools.context import ToolContext

router = APIRouter(prefix="/v1/apps", tags=["apps"])


class ActivateResponse(Model):
    app_id: str
    ingest_key: str
    authority_key: str


@router.post("", response_model=AppSpec, status_code=201)
def create_app(spec: AppSpec, db: Db = Depends(get_db)) -> AppSpec:
    if db.get_app(spec.app_id) is not None:
        raise HTTPException(status_code=409, detail=f"app {spec.app_id!r} already exists")
    if spec.status == "active":
        raise HTTPException(status_code=422, detail="a new app cannot be created already active")
    db.upsert_app(spec, t=clock_mod.now())
    return spec


@router.get("/{app}", response_model=AppSpec)
def get_app(
    app: str,
    db: Db = Depends(get_db),
    authority_key: str | None = Depends(authority_key_header),
) -> AppSpec:
    spec = db.get_app(app)
    if spec is None:
        raise HTTPException(status_code=404, detail="app not found")
    if not is_authority(db, app, authority_key):
        raise HTTPException(status_code=401, detail="authority key required")
    return spec


@router.post("/{app}/activate", response_model=ActivateResponse)
def activate_app(
    app: str,
    db: Db = Depends(get_db),
    engine: TickEngine = Depends(get_engine),
) -> ActivateResponse:
    spec = db.get_app(app)
    if spec is None:
        raise HTTPException(status_code=404, detail="app not found")
    if spec.status == "active":
        raise HTTPException(status_code=409, detail="app is already active")
    t = clock_mod.now()
    before_status = spec.status
    spec.status = "active"
    db.upsert_app(spec, t=t)
    keys = db.create_keys(app, t=t)
    engine.add_app(spec)
    db.add_change(
        Change(
            id=str(uuid.uuid4()),
            app_id=app,
            kind="spec_confirm",
            target=app,
            before=before_status,
            after="active",
            reason="dev confirmed the spec at activation",
            actor="dev",
            t=datetime.fromtimestamp(t, tz=UTC),
        )
    )
    return ActivateResponse(app_id=app, ingest_key=keys.ingest_key, authority_key=keys.authority_key)


@router.post("/{app}/compile", response_model=AppSpec)
async def compile_app(
    app: str,
    body: CompileRequest,
    db: Db = Depends(get_db),
    engine: TickEngine = Depends(get_engine),
    llm: LlmRouter = Depends(get_llm),
    cache: CompilerCache = Depends(get_compiler_cache),
) -> AppSpec:
    existing = db.get_app(app)
    if existing is not None and existing.status == "active":
        raise HTTPException(status_code=409, detail="app is already active")

    for inp in body.inputs:  # refuse an unsafe source before spending model budget on it
        url = connector_engine.connector_url(inp.connector) if inp.connector is not None else None
        if url is not None:
            try:
                url_guard.check_url(url)
            except url_guard.UnsafeUrl as e:
                raise HTTPException(status_code=422, detail=f"{inp.id}: connector URL refused: {e}") from None

    start_t = clock_mod.now()
    ctx = ToolContext(app_id=app, db=db, engine=engine, actor="agent", agent="compiler")
    spec = await run_compiler(
        ctx,
        app_id=app,
        intent=body.intent,
        grid=body.grid.model_dump(mode="json"),
        inputs=[i.model_dump(mode="json") for i in body.inputs],
        destination_points=[d.model_dump(mode="json") for d in body.destination_points],
        llm=llm,
        cache=cache,
    )
    if spec is not None:
        return spec

    traces = [t for t in db.list_trace(app, since=start_t - 1e-6) if t.agent == "compiler"]
    last = traces[-1] if traces else None
    if last is not None and last.skipped_reason:
        raise HTTPException(status_code=503, detail=last.skipped_reason)
    raise HTTPException(status_code=422, detail=last.why if last is not None else "compile did not finalize")


@router.post("/{app}/sources/suggest", response_model=SourceSuggestion)
async def suggest_app_source(
    app: str,
    body: SuggestSourceRequest,
    db: Db = Depends(get_db),
    engine: TickEngine = Depends(get_engine),
    llm: LlmRouter = Depends(get_llm),
) -> SourceSuggestion:
    """Builder "Add a source": infer kind, shape and ranked options from the dev's description.
    Always 200: when the model can't answer (rate budget, provider error, invalid twice) it returns
    every option unranked. A suggestion only; it writes no app state (only trace entries)."""
    ctx = ToolContext(app_id=app, db=db, engine=engine, actor="agent", agent="compiler")
    return await suggest_source(ctx, body.intent, body.description, llm)
