"""Dev-requested Scout runs: `POST /v1/apps/{app}/scout` (authority key required).

The Scout runs on request only (CLAUDE.md agents table). This route queues one request through
`ScoutRunner.enqueue`, the same trace-entry queue the Compiler's `request_scout` feeds, and returns
202 at once: the Scout runs asynchronously and shares the one LlmRouter rate budget (an empty budget
becomes its usual skipped trace). The request text goes only into the authority-only trace, capped;
the response never echoes it (rule 8).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.agents.scout import ScoutRunner
from app.api.deps import authority_key_header, get_db, get_engine
from app.api.trace import _authority
from app.core.db import Db
from app.core.models import ScoutJobAccepted, ScoutRequest
from app.engine.tick import TickEngine

router = APIRouter(prefix="/v1/apps", tags=["scout"])


def get_scout_runner(request: Request) -> ScoutRunner:
    return request.app.state.scout_runner


@router.post("/{app}/scout", status_code=202, response_model=ScoutJobAccepted)
def request_scout(
    app: str,
    body: ScoutRequest,
    db: Db = Depends(get_db),
    engine: TickEngine = Depends(get_engine),
    runner: ScoutRunner = Depends(get_scout_runner),
    authority_key: str | None = Depends(authority_key_header),
) -> ScoutJobAccepted:
    _authority(db, app, authority_key)  # 404 unknown app, 401 missing/wrong key
    spec = db.get_app(app)
    if spec is None or spec.status != "active" or app not in engine.apps:
        raise HTTPException(status_code=409, detail="app is not active")
    inp = next((i for i in spec.inputs if i.id == body.input_id), None)
    if inp is None:
        raise HTTPException(status_code=404, detail="input not found")
    if inp.kind != "pull":
        raise HTTPException(status_code=422, detail="the Scout configures pull inputs only")
    job_id = runner.enqueue(app, inp.id, body.request)
    return ScoutJobAccepted(job_id=job_id, app_id=app, input_id=inp.id)
