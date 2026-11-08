"""Public end-user instructions: `POST /v1/apps/{app}/instruction` with the caller's own `{lat, lon}`.

The server never looks a position up by user id, so this can't be used to find anyone else. Routing,
the code-built text and every guard check live in `tools/message_guard.py`; on any failure the caller
gets the spec's `fallback_text`. A request writes an authority-only TraceEntry with the checks only when
its outcome (passed, destination label, failing checks) changed for that app and grid cell of the caller's
position (`api/instruction_trace.py`, #169), since pages recompute every tick. Never the position or text.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.deps import get_db, get_engine, get_instruction_trace_dedup
from app.api.instruction_trace import InstructionTraceDedup
from app.core import clock as clock_mod
from app.core.db import Db
from app.core.models import Instruction, InstructionRequest
from app.engine.tick import TickEngine
from app.tools import message_guard

router = APIRouter(prefix="/v1/apps", tags=["instructions"])


@router.post("/{app}/instruction", response_model=Instruction)
def post_instruction(
    app: str,
    body: InstructionRequest,
    response: Response,
    db: Db = Depends(get_db),
    engine: TickEngine = Depends(get_engine),
    dedup: InstructionTraceDedup = Depends(get_instruction_trace_dedup),
) -> Instruction:
    rt = engine.apps.get(app)
    if rt is None:
        raise HTTPException(status_code=404, detail="app not found or not active")
    if not rt.spec.routing.enabled:
        raise HTTPException(status_code=404, detail="this app does not give instructions")
    latest = engine.latest(app)
    if latest is None:
        raise HTTPException(status_code=503, detail="no map yet; try again shortly", headers={"Retry-After": "2"})
    now = datetime.fromtimestamp(clock_mod.now(), UTC)
    ins = message_guard.safe_instruction(
        spec=rt.spec,
        grid=rt.grid,
        score=latest.score,
        lat=body.lat,
        lon=body.lon,
        pois=db.list_pois(app, status="active"),
        now=now,
    )
    row, col, inside = rt.grid.cell_of(body.lat, body.lon)
    key = (app, int(row), int(col)) if bool(inside) else (app, None)
    if dedup.should_trace(key, ins):
        db.add_trace(message_guard.trace_entry(app, ins, now))
    response.headers["Cache-Control"] = "no-store"
    return ins
