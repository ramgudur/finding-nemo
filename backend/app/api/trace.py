"""Authority-only console reads (trace, changes, weights, verification), the dev's weight pin and
the dev's revert of a Change.

Every route needs the app's `X-Authority-Key`: rule 8 keeps the trace, the Change log and weights in the
authority view. A missing or wrong key is 401. Writes go through the tool layer (`pin_weight` with
`actor="dev"`), so guards still decide and a `Change` is recorded; nothing here writes state directly.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import authority_key_header, get_db, get_engine
from app.core.db import Db
from app.core.models import Change, ChangeKind, Tier, TraceEntry, UtcDatetime, VerificationMethod
from app.engine.tick import TickEngine
from app.tools import guards, read_tools, registry, revert_tools
from app.tools.context import ToolContext

router = APIRouter(prefix="/v1/apps", tags=["console"])

MAX_TRACE = 500


# --- contracts (mirrored in frontend/src/api/types.ts) --------------------------------


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LayerWeight(_Out):
    layer_id: str
    weight: float
    tier: Tier
    pinned: bool


class WeightsView(_Out):
    app_id: str
    t: UtcDatetime
    layers: list[LayerWeight]
    flexible_total: float = Field(description="Sum of flexible weights; renormalization keeps it fixed.")


class MethodScore(_Out):
    method: VerificationMethod
    priority: int
    has_data: bool
    value: float | None


class VerificationView(_Out):
    app_id: str
    window_s: float
    ticks: int
    methods: list[MethodScore]
    cold_start: bool = Field(description="Fewer than two methods have data: flexible weight changes are rejected.")


class PinRequest(_Out):
    pinned: bool = True
    reason: str = Field(default="pinned by dev", min_length=1, max_length=500)


# --- helpers ---------------------------------------------------------------------------


def _authority(db: Db, app_id: str, key: str | None) -> None:
    if db.get_app(app_id) is None:
        raise HTTPException(status_code=404, detail="app not found")
    if key is None or not db.check_authority_key(app_id, key):
        raise HTTPException(status_code=401, detail="authority key required")


def _ctx(app_id: str, db: Db, engine: TickEngine, actor: str = "agent") -> ToolContext:
    if app_id not in engine.apps:
        raise HTTPException(status_code=409, detail="app is not active")
    return ToolContext(app_id=app_id, db=db, engine=engine, actor=actor)  # type: ignore[arg-type]


def _weights(ctx: ToolContext) -> WeightsView:
    rt = ctx.runtime
    tiers, pins = guards.layer_tiers(ctx), guards.pinned_layers(ctx)
    return WeightsView(
        app_id=ctx.app_id,
        t=ctx.now_dt(),
        layers=[
            LayerWeight(layer_id=k, weight=round(float(w), 6), tier=tiers[k], pinned=k in pins)  # type: ignore[arg-type]
            for k, w in sorted(rt.weights.items())
        ],
        flexible_total=round(float(guards.flexible_total(ctx)), 6),
    )


# --- routes ----------------------------------------------------------------------------


@router.get("/{app}/trace", response_model=list[TraceEntry])
def get_trace(
    app: str,
    since_s: float | None = Query(default=None, description="Only entries at or after this POSIX time."),
    limit: int = Query(default=100, ge=1, le=MAX_TRACE),
    db: Db = Depends(get_db),
    authority_key: str | None = Depends(authority_key_header),
) -> list[TraceEntry]:
    _authority(db, app, authority_key)
    entries = db.list_trace(app, since=since_s)
    return entries[-limit:]


@router.get("/{app}/changes", response_model=list[Change])
def get_changes(
    app: str,
    kind: ChangeKind | None = None,
    since_s: float | None = Query(default=None),
    db: Db = Depends(get_db),
    authority_key: str | None = Depends(authority_key_header),
) -> list[Change]:
    _authority(db, app, authority_key)
    return db.list_changes(app, kind=kind, since=since_s)


@router.get("/{app}/weights", response_model=WeightsView)
def get_weights(
    app: str,
    db: Db = Depends(get_db),
    engine: TickEngine = Depends(get_engine),
    authority_key: str | None = Depends(authority_key_header),
) -> WeightsView:
    _authority(db, app, authority_key)
    return _weights(_ctx(app, db, engine))


@router.get("/{app}/verification", response_model=VerificationView)
def get_verification(
    app: str,
    window_s: float = Query(default=300.0, gt=0, le=600),
    db: Db = Depends(get_db),
    engine: TickEngine = Depends(get_engine),
    authority_key: str | None = Depends(authority_key_header),
) -> VerificationView:
    _authority(db, app, authority_key)
    try:
        rep: dict[str, Any] = read_tools.get_verification_report(
            _ctx(app, db, engine), read_tools.WindowArgs(window_s=window_s)
        )
    except guards.GuardError:  # fewer than two ticks cached yet
        return VerificationView(app_id=app, window_s=window_s, ticks=0, methods=[], cold_start=True)
    return VerificationView(
        app_id=app,
        window_s=window_s,
        ticks=int(rep["window"]["ticks"]),
        methods=[
            MethodScore(method=m, priority=v["priority"], has_data=v["has_data"], value=v["value"])
            for m, v in rep["methods"].items()
        ],
        cold_start=bool(rep["cold_start"]),
    )


@router.post("/{app}/weights/{layer_id}/pin", response_model=WeightsView)
def pin_weight(
    app: str,
    layer_id: str,
    body: PinRequest,
    db: Db = Depends(get_db),
    engine: TickEngine = Depends(get_engine),
    authority_key: str | None = Depends(authority_key_header),
) -> WeightsView:
    """Dev action: pin or unpin a layer weight through the `pin_weight` tool (records a Change)."""
    _authority(db, app, authority_key)
    ctx = _ctx(app, db, engine, actor="dev")
    out = registry.call_tool(ctx, "pin_weight", {"layer_id": layer_id, "pinned": body.pinned, "reason": body.reason})
    if not out["ok"]:
        raise HTTPException(status_code=400, detail=out["error"])
    return _weights(ctx)


@router.post("/{app}/changes/{change_id}/revert", response_model=Change)
def revert_change(
    app: str,
    change_id: str,
    db: Db = Depends(get_db),
    engine: TickEngine = Depends(get_engine),
    authority_key: str | None = Depends(authority_key_header),
) -> Change:
    """Dev action: put a Change's `before` back through the same tools and guards (`actor="dev"`).

    Returns the new Change (`reverts` = the reverted id). 404 unknown change; 409 if the target no
    longer holds that Change's `after` (or the app isn't running); 422 for spec_confirm / spec_edit
    and when a guard rejects the revert (see `tools/revert_tools.py` for what a dev revert skips)."""
    _authority(db, app, authority_key)
    ctx = ToolContext(app_id=app, db=db, engine=engine, actor="dev")
    try:
        return revert_tools.revert_change(ctx, change_id)
    except revert_tools.RevertNotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    except revert_tools.RevertStale as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except guards.GuardError as e:
        raise HTTPException(status_code=422, detail="rejected: " + "; ".join(e.reasons)) from None
    except LookupError as e:  # the app's runtime isn't loaded (not active)
        raise HTTPException(status_code=409, detail=str(e)) from None
