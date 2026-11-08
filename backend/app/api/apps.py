"""`api/apps.py`: create, compile, and activate an app.

A spec reaches `pending_confirmation` either hand-written (`POST /{app}`, e.g. the
campus-alert spec file) or via the Compiler (`POST /{app}/compile`, `agents.compiler`).
Either way, only `POST /{app}/activate` is dev confirmation: it generates the
ingest/authority keys (shown once), starts the app ticking, and is the only place that
writes the `spec_confirm` Change.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import ValidationError

from app.agents.compiler import CompilerCache, run_compiler
from app.agents.llm import LlmRouter
from app.agents.source_suggester import suggest_source
from app.api.deps import authority_key_header, get_compiler_cache, get_db, get_engine, get_llm
from app.connectors import engine as connector_engine
from app.connectors import url_guard
from app.core import clock as clock_mod
from app.core.access import is_authority
from app.core.db import Db
from app.core.models import (
    AppSpec,
    Change,
    CompileFailure,
    CompileRequest,
    CompileStatus,
    Model,
    SourceSuggestion,
    SuggestSourceRequest,
    TraceEntry,
)
from app.engine.tick import TickEngine
from app.tools.context import ToolContext
from app.tools.registry import call_tool, tool_schemas

router = APIRouter(prefix="/v1/apps", tags=["apps"])

# Fixed step kinds the status route may report. Anything else becomes "step", and a tool name
# the model made up becomes "tool_calls", so no model-written text reaches a public response.
_STEP_KINDS = frozenset({"tool_calls", "output", "error", "skipped", "gave up"})


@dataclass
class _CompileRun:
    started_at: float
    running: bool = True


def _compile_runs(request: Request) -> dict[str, _CompileRun]:
    """In-process record of the latest compile per app, for `GET /{app}/compile/status`."""
    runs = getattr(request.app.state, "compile_runs", None)
    if runs is None:
        runs = request.app.state.compile_runs = {}
    return runs


def _compile_traces(db: Db, app: str, since: float) -> list[TraceEntry]:
    """The Compiler's own trace entries since `since` (not the Builder's source suggestions,
    which share the agent name)."""
    return [
        t
        for t in db.list_trace(app, since=since - 1e-6)
        if t.agent == "compiler" and t.action != "suggest_source" and not t.why.startswith("suggest_source")
    ]


def _retry_after_s(llm: LlmRouter) -> int:
    """Seconds until the Compiler's provider bucket refills one request (60 / RPM, rounded up)."""
    rpm = llm.settings.rpm(llm.settings.agent_model("compiler").provider)
    return max(1, math.ceil(60.0 / max(1, rpm)))


def _last_action(entry: TraceEntry) -> str:
    if entry.action == "tool_calls" and entry.tool_calls:
        names = {s["function"]["name"] for s in tool_schemas("compiler")}
        name = entry.tool_calls[-1].name
        return name if name in names else "tool_calls"
    return entry.action if entry.action in _STEP_KINDS else "step"


def _plain_error(err: Any) -> str:
    where = ".".join(str(p) for p in err.get("loc", ())) or "spec"
    msg = str(err.get("msg", "invalid")).removeprefix("Value error, ")
    return f"{where}: {msg}"


def _compile_failure(ctx: ToolContext, traces: list[TraceEntry]) -> CompileFailure:
    """Plain-language reason for a compile that never finalized, plus the errors and warnings of
    the last spec the Compiler proposed (re-checked with the read-only `validate_spec`)."""
    last = traces[-1] if traces else None
    if last is None:
        message = "The Compiler did not finalize a spec."
    elif last.action == "gave up":
        message = f"The Compiler used all its steps without a valid spec ({last.why})."
    elif last.action == "error":
        message = f"The model call failed ({last.why})."
    else:
        message = "The Compiler stopped without finalizing a valid spec."

    errors: list[str] = []
    warnings: list[str] = []
    calls = [c for t in traces for c in t.tool_calls]
    proposed = next(
        (c.args["spec"] for c in reversed(calls) if c.name in ("validate_spec", "finalize_app") and "spec" in c.args),
        None,
    )
    if isinstance(proposed, dict):
        try:  # shape errors first, as plain "where: what" lines (no pydantic boilerplate or input echo)
            AppSpec.model_validate(proposed)
        except ValidationError as e:
            errors = [_plain_error(x) for x in e.errors()]
        checked = call_tool(ctx, "validate_spec", {"spec": proposed}) if not errors else {}
        if checked.get("ok"):
            errors = [str(e) for e in checked["result"].get("errors", [])]
            warnings = [str(w) for w in checked["result"].get("warnings", [])]
    if not errors:  # e.g. a create_input or finalize_app guard that isn't a spec-shape error
        last_calls = next((t.tool_calls for t in reversed(traces) if t.tool_calls), [])
        errors = [f"{c.name}: {c.result_summary}" for c in last_calls if not c.ok]
    return CompileFailure(message=message, errors=errors, warnings=warnings)


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
    request: Request,
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
    run = _CompileRun(started_at=start_t)
    _compile_runs(request)[app] = run
    ctx = ToolContext(app_id=app, db=db, engine=engine, actor="agent", agent="compiler")
    try:
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
    finally:
        run.running = False
    if spec is not None:
        return spec

    traces = _compile_traces(db, app, start_t)
    last = traces[-1] if traces else None
    if last is not None and last.skipped_reason:
        retry_s = _retry_after_s(llm)
        raise HTTPException(
            status_code=503,
            detail=f"{last.skipped_reason}: try again in about {retry_s} s",
            headers={"Retry-After": str(retry_s)},
        )
    raise HTTPException(status_code=422, detail=_compile_failure(ctx, traces).model_dump())


@router.get("/{app}/compile/status", response_model=CompileStatus)
def compile_status(app: str, request: Request, db: Db = Depends(get_db)) -> CompileStatus:
    """Public-safe compile progress for the Builder (a pending app has no authority key yet).
    Only for apps that are not active: 404 once active, or when nothing is known about the app.
    Counts and a step name only, never trace content or tool arguments (rule 8)."""
    spec = db.get_app(app)
    run = _compile_runs(request).get(app)
    if (spec is not None and spec.status == "active") or (spec is None and run is None):
        raise HTTPException(status_code=404, detail="no compile for this app")
    if run is None:
        return CompileStatus(app_id=app, running=False)
    traces = _compile_traces(db, app, run.started_at)
    return CompileStatus(
        app_id=app,
        running=run.running,
        steps=len(traces),
        last_action=_last_action(traces[-1]) if traces else None,
        started_at=datetime.fromtimestamp(run.started_at, tz=UTC),
    )


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
