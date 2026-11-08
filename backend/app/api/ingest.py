"""`api/ingest.py`: `POST /v1/apps/{app}/inputs/{input}/events`.

Header `X-Ingest-Key`; body is validated against the input's declared `fields`. Events
are rejected unless the app is `active`. Accepted events land straight in the event
store (`core/db.py`); the tick loop picks them up on its own schedule, so ingest never
touches the engine directly.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.deps import get_db, ingest_key_header
from app.core import clock as clock_mod
from app.core.db import Db
from app.core.models import Event, FieldType, InputField

router = APIRouter(prefix="/v1/apps", tags=["ingest"])

_TYPE_CHECKS: dict[FieldType, type | tuple[type, ...]] = {
    "number": (int, float),
    "integer": int,
    "string": str,
    "boolean": bool,
}


def _check_fields(fields: list[InputField], extra: dict[str, Any]) -> str | None:
    """A human-readable reason the extras don't match the declared fields, or None if they do."""
    declared = {f.name: f for f in fields}
    unknown = set(extra) - set(declared)
    if unknown:
        return f"undeclared field(s): {sorted(unknown)}"
    for f in fields:
        if f.required and f.name not in extra:
            return f"missing required field {f.name!r}"
        if f.name not in extra:
            continue
        v = extra[f.name]
        expected = _TYPE_CHECKS[f.type]
        # bool is an int subclass in Python; keep "integer"/"number" from accepting booleans.
        if isinstance(v, bool) and expected is not bool:
            return f"field {f.name!r} must be {f.type}, got boolean"
        if not isinstance(v, expected):
            return f"field {f.name!r} must be {f.type}"
    return None


@router.post("/{app}/inputs/{input}/events", status_code=202)
def post_event(
    app: str,
    input: str,
    body: dict[str, Any] = Body(...),
    ingest_key: str | None = Depends(ingest_key_header),
    db: Db = Depends(get_db),
) -> dict[str, str]:
    if ingest_key is None or not db.check_ingest_key(app, ingest_key):
        raise HTTPException(status_code=401, detail="invalid ingest key")
    spec = db.get_app(app)
    inp = db.get_input(app, input)
    if spec is None or inp is None or inp.kind != "push":
        raise HTTPException(status_code=404, detail="app or input not found")
    if spec.status != "active":
        raise HTTPException(status_code=403, detail="app is not active")

    try:
        event = Event.model_validate(body)
    except Exception as e:  # noqa: BLE001 - surfaced as a 422 with the validator's reason
        raise HTTPException(status_code=422, detail=str(e)) from e

    reason = _check_fields(inp.fields, event.model_extra or {})
    if reason is not None:
        raise HTTPException(status_code=422, detail=reason)

    db.insert_event(app, input, event, received_at=clock_mod.now())
    return {"status": "accepted"}
