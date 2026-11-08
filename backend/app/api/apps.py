"""`api/apps.py`: create, compile, and activate an app.

A spec reaches `pending_confirmation` either hand-written (`POST /{app}`, e.g. the
campus-alert spec file) or via the Compiler (`POST /{app}/compile`, `agents.compiler`).
While it is pending, the dev may edit it (`PUT /{app}/spec`, which writes a `spec_edit` Change
and keeps the status). Either way, only `POST /{app}/activate` is dev confirmation: it generates the
ingest/authority keys (shown once), starts the app ticking, and is the only place that
writes the `spec_confirm` Change.
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import ValidationError

from app.agents.compiler import CompilerCache, run_compiler
from app.agents.llm import LlmRouter
from app.agents.source_suggester import suggest_source
from app.api.compile_gate import CompileGate
from app.api.deps import authority_key_header, get_compile_gate, get_compiler_cache, get_db, get_engine, get_llm
from app.connectors import engine as connector_engine
from app.connectors import fields as connector_fields
from app.connectors import json_api, json_list, url_guard
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
    SpecEditResponse,
    SuggestSourceRequest,
    TraceEntry,
    ValidateResult,
)
from app.engine.tick import TickEngine
from app.tools.compiler_tools import _spec_errors, _spec_warnings
from app.tools.context import ToolContext

router = APIRouter(prefix="/v1/apps", tags=["apps"])
log = logging.getLogger(__name__)


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


def _plain_error(err: Any) -> str:
    where = ".".join(str(p) for p in err.get("loc", ())) or "spec"
    msg = str(err.get("msg", "invalid")).removeprefix("Value error, ")
    return f"{where}: {msg}"


def _last_validate(traces: list[TraceEntry]) -> ValidateResult | None:
    """Errors and warnings of the last spec the Compiler passed to `validate_spec` or
    `finalize_app`, re-checked here with the same platform checks. Only platform-written
    strings come back: shape errors as plain "where: what" lines (no pydantic boilerplate,
    no input echo), never the trace's text or the tool arguments themselves."""
    calls = [c for t in traces for c in t.tool_calls]
    proposed = next(
        (c.args["spec"] for c in reversed(calls) if c.name in ("validate_spec", "finalize_app") and "spec" in c.args),
        None,
    )
    if not isinstance(proposed, dict):
        return None
    try:
        spec = AppSpec.model_validate(proposed)
    except ValidationError as e:
        return ValidateResult(errors=[_plain_error(x) for x in e.errors()])
    return ValidateResult(errors=_spec_errors(spec), warnings=_spec_warnings(spec))


def _compile_failure(traces: list[TraceEntry]) -> CompileFailure:
    """Plain-language reason for a compile that never finalized, plus the errors and warnings of
    the last spec the Compiler proposed."""
    last = traces[-1] if traces else None
    if last is None:
        reason = "The Compiler did not finalize a spec."
    elif last.action == "gave up":
        reason = f"The Compiler used all its steps without a valid spec ({last.why})."
    elif last.action == "error":
        reason = f"The model call failed ({last.why})."
    else:
        reason = "The Compiler stopped without finalizing a valid spec."
    checked = _last_validate(traces) or ValidateResult()
    errors = list(checked.errors)
    if not errors:  # e.g. a create_input or finalize_app guard that isn't a spec-shape error
        last_calls = next((t.tool_calls for t in reversed(traces) if t.tool_calls), [])
        errors = [f"{c.name}: {c.result_summary}" for c in last_calls if not c.ok]
    return CompileFailure(reason=reason, errors=errors, warnings=checked.warnings)


class ActivateResponse(Model):
    app_id: str
    ingest_key: str
    authority_key: str


def _custom_config_error(conn: Any) -> str | None:
    """Why a custom JSON source's read config (`response_path`, `fields`) or a `json_list` item
    mapping is refused, or None."""
    if conn is None:
        return None
    try:
        if conn.type == "json_list":
            json_list.check_config(conn.config)
            if connector_engine.connector_url(conn) is None:
                raise ValueError("a json_list connector needs a url")
        elif conn.type == "json_api" and conn.preset is None:
            json_api.check_custom_config(conn.config)
    except ValueError as e:
        return f"connector config refused: {e}"
    return None


@router.post("", response_model=AppSpec, status_code=201)
def create_app(spec: AppSpec, db: Db = Depends(get_db)) -> AppSpec:
    if db.get_app(spec.app_id) is not None:
        raise HTTPException(status_code=409, detail=f"app {spec.app_id!r} already exists")
    if spec.status == "active":
        raise HTTPException(status_code=422, detail="a new app cannot be created already active")
    spec = connector_fields.declare_spec(spec)  # a connector's own fields are declared (#259)
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
    if spec.status != "active":
        # Not confirmed yet: no keys exist and the spec holds only the dev's own words plus the
        # Compiler's proposal (no events, no user data), and compile / PUT /spec / activate are
        # keyless too, so the dev can reopen it from any browser. Rule 8 data starts at activation.
        return spec
    if not is_authority(db, app, authority_key):
        raise HTTPException(status_code=401, detail="authority key required")
    return spec


@router.post("/{app}/activate", response_model=ActivateResponse)
def activate_app(
    app: str,
    request: Request,
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
    _backfill(request, spec)
    return ActivateResponse(app_id=app, ingest_key=keys.ingest_key, authority_key=keys.authority_key)


def _backfill(request: Request, spec: AppSpec) -> None:
    """Backfill at activation (#267): one immediate poll of each pull connector, scheduled on the
    connector engine's own loop, so a pulled source has data without waiting a whole poll_s. It
    never blocks this response and never fails the activation; the poll is a normal one (URL
    guard, status and trace recorded by `record_poll`)."""
    if not any(i.kind == "pull" and i.connector is not None for i in spec.inputs):
        return
    engine = getattr(request.app.state, "connector_engine", None)
    if engine is None:
        return
    try:
        engine.poll_app_now(spec.app_id)
    except Exception:  # noqa: BLE001 - the regular poll loop still picks the app up
        log.exception("scheduling the activation poll of %s failed", spec.app_id)


def _spec_diff(before: AppSpec, after: AppSpec) -> tuple[dict[str, Any], dict[str, Any]]:
    """Only what changed, keyed by a readable path: top-level fields whole, inputs per field
    (`inputs[<id>].<field>`); an added or removed input appears whole on one side."""
    b, a = before.model_dump(mode="json"), after.model_dump(mode="json")
    old: dict[str, Any] = {}
    new: dict[str, Any] = {}
    for key in a:
        if key in ("inputs", "status") or b.get(key) == a[key]:
            continue
        old[key], new[key] = b.get(key), a[key]
    b_in = {i["id"]: i for i in b["inputs"]}
    a_in = {i["id"]: i for i in a["inputs"]}
    for iid in [*b_in, *(i for i in a_in if i not in b_in)]:
        bi, ai = b_in.get(iid), a_in.get(iid)
        if bi is None or ai is None:
            old[f"inputs[{iid}]"], new[f"inputs[{iid}]"] = bi, ai
            continue
        for field in ai:
            if bi.get(field) != ai[field]:
                old[f"inputs[{iid}].{field}"], new[f"inputs[{iid}].{field}"] = bi.get(field), ai[field]
    if not new and b["inputs"] != a["inputs"]:  # only the order of inputs changed
        old["inputs"], new["inputs"] = list(b_in), list(a_in)
    return old, new


@router.put("/{app}/spec", response_model=SpecEditResponse)
def edit_spec(app: str, body: dict[str, Any] = Body(...), db: Db = Depends(get_db)) -> SpecEditResponse:
    """The dev edits a pending spec before confirming it. Body: the full `AppSpec`; the body
    wins for every field (connectors and destinations included). Status stays
    `pending_confirmation`: `/activate` is still the only confirmation (rule 4)."""
    current = db.get_app(app)
    if current is None:
        raise HTTPException(status_code=404, detail="app not found")
    if current.status != "pending_confirmation":
        raise HTTPException(
            status_code=409, detail=f"only a pending_confirmation spec can be edited (status is {current.status!r})"
        )
    if body.get("app_id") != app:
        raise HTTPException(status_code=422, detail=["app_id in the body must match the path"])
    try:
        spec = AppSpec.model_validate(body)
    except ValidationError as e:
        errors = [f"{'.'.join(str(p) for p in err['loc']) or 'spec'}: {err['msg']}" for err in e.errors()]
        raise HTTPException(status_code=422, detail=errors) from None
    if spec.status != "active":  # an "active" body is reported by _spec_errors below
        spec.status = "pending_confirmation"
    spec = connector_fields.declare_spec(spec)  # a connector's own fields are declared (#259)
    errors = _spec_errors(spec)
    for inp in spec.inputs:
        reason = _custom_config_error(inp.connector)
        if reason is not None:
            errors.append(f"input {inp.id!r}: {reason}")
    old_urls = {
        connector_engine.connector_url(i.connector) for i in current.inputs if i.connector is not None
    }  # already checked when they entered the spec; re-check only new or changed URLs
    for inp in spec.inputs:
        url = connector_engine.connector_url(inp.connector) if inp.connector is not None else None
        if url is not None and url not in old_urls:
            try:
                url_guard.check_url(url)
            except url_guard.UnsafeUrl as e:
                errors.append(f"input {inp.id!r}: connector URL refused: {e}")
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    before, after = _spec_diff(current, spec)
    if before or after:
        t = clock_mod.now()
        db.upsert_app(spec, t=t)
        db.add_change(
            Change(
                id=str(uuid.uuid4()),
                app_id=app,
                kind="spec_edit",
                target=app,
                before=before,
                after=after,
                reason=f"dev edited the pending spec: {', '.join(after)}",
                actor="dev",
                t=datetime.fromtimestamp(t, tz=UTC),
            )
        )
    return SpecEditResponse(spec=spec, warnings=_spec_warnings(spec))


@router.post("/{app}/compile", response_model=AppSpec)
async def compile_app(
    app: str,
    body: CompileRequest,
    request: Request,
    db: Db = Depends(get_db),
    engine: TickEngine = Depends(get_engine),
    llm: LlmRouter = Depends(get_llm),
    cache: CompilerCache = Depends(get_compiler_cache),
    gate: CompileGate = Depends(get_compile_gate),
) -> AppSpec:
    existing = db.get_app(app)
    if existing is not None and existing.status == "active":
        raise HTTPException(status_code=409, detail="app is already active")

    # A connector's own fields (an RSS document's doc_id/confidence, a JSON source's readings) are
    # declared on its input before the Compiler sees it, so finalize_app pins them with the dev's (#259).
    body = body.model_copy(update={"inputs": [connector_fields.declare_compile_input(i) for i in body.inputs]})
    for inp in body.inputs:  # refuse an unsafe source before spending model budget on it
        reason = _custom_config_error(inp.connector)
        if reason is not None:
            raise HTTPException(status_code=422, detail=f"{inp.id}: {reason}")
        url = connector_engine.connector_url(inp.connector) if inp.connector is not None else None
        if url is not None:
            try:
                url_guard.check_url(url)
            except url_guard.UnsafeUrl as e:
                raise HTTPException(status_code=422, detail=f"{inp.id}: connector URL refused: {e}") from None

    # Only a compile that would actually reach the model spends a slot (#169): /compile has no key.
    refused = gate.try_acquire(app)
    if refused is not None:
        raise HTTPException(status_code=429, detail=refused.detail, headers={"Retry-After": str(refused.retry_after_s)})
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
        gate.release(app)
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
    raise HTTPException(status_code=422, detail=_compile_failure(traces).model_dump())


@router.get("/{app}/compile/status", response_model=CompileStatus)
def compile_status(app: str, request: Request, db: Db = Depends(get_db)) -> CompileStatus:
    """Public-safe compile progress for the Builder (a pending app has no authority key yet).
    Only for apps that are not active: 404 once active, or when nothing is known about the app.
    A step count, elapsed time and the last validation's errors/warnings only; never trace text
    or tool arguments (rule 8)."""
    spec = db.get_app(app)
    run = _compile_runs(request).get(app)
    if (spec is not None and spec.status == "active") or (spec is None and run is None):
        raise HTTPException(status_code=404, detail="no compile for this app")
    if run is None:
        return CompileStatus(running=False)
    traces = _compile_traces(db, app, run.started_at)
    return CompileStatus(
        running=run.running,
        step=len(traces),
        elapsed_s=round(max(0.0, clock_mod.now() - run.started_at), 1) if run.running else 0.0,
        last_validate=_last_validate(traces),
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
