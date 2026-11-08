"""Authority-only console routes for the POI Labeler's label proposals (`propose_label`).

The dev lists open proposals and accepts or dismisses each one. Every route needs the app's
`X-Authority-Key` (rule 8: a proposal's `why` is agent text and stays in the authority view); a
missing or wrong key is 401. Accepting goes through the dev-only `accept_label_proposal` tool, so
guards and `AppSpec` validation still decide and a `spec_edit` Change is recorded.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.deps import authority_key_header, get_db, get_engine
from app.api.trace import _authority
from app.core.db import Db
from app.core.models import LabelProposal, LabelProposalAccept, LabelProposalAcceptResponse
from app.engine.tick import TickEngine
from app.tools import registry
from app.tools.context import ToolContext

router = APIRouter(prefix="/v1/apps", tags=["console"])


def _open(db: Db, app: str, proposal_id: str) -> LabelProposal:
    prop = db.get_label_proposal(app, proposal_id)
    if prop is None:
        raise HTTPException(status_code=404, detail="label proposal not found")
    if prop.status != "open":
        raise HTTPException(status_code=409, detail=f"label proposal is {prop.status}, not open")
    return prop


def _dev_ctx(app: str, db: Db, engine: TickEngine) -> ToolContext:
    spec = db.get_app(app)
    if spec is None or spec.status != "active" or app not in engine.apps:
        raise HTTPException(status_code=409, detail="app is not active")
    return ToolContext(app_id=app, db=db, engine=engine, actor="dev")


@router.get("/{app}/label-proposals", response_model=list[LabelProposal])
def list_label_proposals(
    app: str,
    db: Db = Depends(get_db),
    authority_key: str | None = Depends(authority_key_header),
) -> list[LabelProposal]:
    _authority(db, app, authority_key)
    return db.list_label_proposals(app)


@router.post("/{app}/label-proposals/{proposal_id}/accept", response_model=LabelProposalAcceptResponse)
def accept_label_proposal(
    app: str,
    proposal_id: str,
    body: LabelProposalAccept | None = Body(default=None),
    db: Db = Depends(get_db),
    engine: TickEngine = Depends(get_engine),
    authority_key: str | None = Depends(authority_key_header),
) -> LabelProposalAcceptResponse:
    """Add the proposal's label to the active spec's label_set (optionally overriding its flags)."""
    _authority(db, app, authority_key)
    _open(db, app, proposal_id)
    ctx = _dev_ctx(app, db, engine)
    flags = body or LabelProposalAccept()
    args = {"proposal_id": proposal_id, **flags.model_dump(exclude_none=True)}
    out = registry.call_tool(ctx, "accept_label_proposal", args)
    if not out["ok"]:
        raise HTTPException(status_code=422, detail=out["error"])
    res = out["result"]
    return LabelProposalAcceptResponse(proposal=res["proposal"], label_set=res["label_set"])


@router.post("/{app}/label-proposals/{proposal_id}/dismiss", response_model=LabelProposal)
def dismiss_label_proposal(
    app: str,
    proposal_id: str,
    db: Db = Depends(get_db),
    engine: TickEngine = Depends(get_engine),
    authority_key: str | None = Depends(authority_key_header),
) -> LabelProposal:
    _authority(db, app, authority_key)
    _open(db, app, proposal_id)
    ctx = ToolContext(app_id=app, db=db, engine=engine, actor="dev")
    out = registry.call_tool(ctx, "dismiss_label_proposal", {"proposal_id": proposal_id})
    if not out["ok"]:
        raise HTTPException(status_code=409, detail=out["error"])
    return LabelProposal.model_validate(out["result"]["proposal"])
