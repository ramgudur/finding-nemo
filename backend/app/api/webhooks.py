"""`/v1/apps/{app}/webhooks`: stubbed (CLAUDE.md Scope). Every method answers 501 so clients get a clear
"not built" rather than a 404 that looks like a typo."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/v1/apps", tags=["webhooks"])


@router.api_route("/{app}/webhooks", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def webhooks(app: str) -> None:
    raise HTTPException(status_code=501, detail="webhooks are not implemented yet")
