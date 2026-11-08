"""Ingest/authority key checks and visibility filtering.

Shared by REST (``api/ingest.py``, ``api/outputs.py``) and, later, the WebSocket endpoint
(``api/ws.py``, #30): both must filter what a caller sees the same way (rule 8 — sensitive
data stays in the authority view). Never log a key.
"""

from __future__ import annotations

from fastapi import HTTPException

from app.core.db import Db
from app.core.models import Visibility


def is_authority(db: Db, app_id: str, authority_key: str | None) -> bool:
    """Whether ``authority_key`` is the app's current authority key."""
    return authority_key is not None and db.check_authority_key(app_id, authority_key)


def is_valid_ingest_key(db: Db, app_id: str, ingest_key: str | None) -> bool:
    return ingest_key is not None and db.check_ingest_key(app_id, ingest_key)


def visible(visibility: Visibility, caller_is_authority: bool) -> bool:
    """Whether an item at this visibility reaches this caller."""
    return caller_is_authority or visibility == "public"


def require_authority_if_present(db: Db, app_id: str, authority_key: str | None) -> bool:
    """REST visibility rule: no key gets public data; a *wrong* key is rejected outright.

    Returns whether the caller is the authority (so the route can include authority-only
    items). Raises 401 only when a key was given and it didn't match.
    """
    if authority_key is None:
        return False
    if not db.check_authority_key(app_id, authority_key):
        raise HTTPException(status_code=401, detail="invalid authority key")
    return True
