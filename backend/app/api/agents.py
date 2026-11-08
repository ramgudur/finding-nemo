"""`POST /v1/apps/{app}/agents/{agent}/run`: the dev asks the Critic or the POI Labeler to run now.

Authority key required (401). The app must be active (409). The run is queued through the agent
scheduler's own trigger path (`request_run`), so it obeys the same 15 s per-app trigger cooldown
(429 with `Retry-After` inside it) and the same shared rate budget: an empty budget still skips
the run with a trace entry (rule 2). The run itself happens in the scheduler's background loop,
never in the tick and never in this request; the response says only that it was queued.
"""

from __future__ import annotations

import math

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.deps import authority_key_header, get_db, get_engine
from app.api.trace import _authority
from app.core.db import Db
from app.core.models import AgentRunAccepted, RunnableAgent
from app.engine.tick import TickEngine

router = APIRouter(prefix="/v1/apps", tags=["agents"])

SCHEDULERS = {"critic": "critic_scheduler", "poi_labeler": "poi_labeler_scheduler"}


@router.post("/{app}/agents/{agent}/run", status_code=202, response_model=AgentRunAccepted)
async def run_agent_now(
    app: str,
    agent: RunnableAgent,
    request: Request,
    db: Db = Depends(get_db),
    engine: TickEngine = Depends(get_engine),
    authority_key: str | None = Depends(authority_key_header),
) -> AgentRunAccepted | JSONResponse:
    _authority(db, app, authority_key)  # 404 unknown app, 401 missing/wrong key
    spec = db.get_app(app)
    if spec is None or spec.status != "active" or app not in engine.apps:
        raise HTTPException(status_code=409, detail="app is not active")
    if agent == "poi_labeler" and spec.outputs.poi_labels.mode == "off":
        raise HTTPException(status_code=409, detail="POI labels are off for this app")
    scheduler = getattr(request.app.state, SCHEDULERS[agent])
    left = scheduler.request_run(app, engine.clock.now())
    if left is not None:
        retry = max(1, math.ceil(left))
        return JSONResponse(
            status_code=429,
            content={"detail": f"the {agent} ran or was triggered less than 15 s ago; retry in {retry} s"},
            headers={"Retry-After": str(retry)},
        )
    return AgentRunAccepted(agent=agent)
