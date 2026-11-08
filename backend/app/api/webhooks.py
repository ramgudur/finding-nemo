"""`POST /v1/apps/{app}/webhooks/{input}`: another service's own JSON, mapped by config (rule 10,
#222) into events, validated exactly like `POST /{app}/inputs/{input}/events`.

The mapping (`InputSpec.webhook`, push inputs only) reads each item with dotted paths and
optional list indices (e.g. "geometry.coordinates.1") -- data only, never code. The raw request
body is never stored (rule 8): only per-item accept/reject outcomes are kept, and only the first
20 rejection reasons are returned.

Coordinates are validated like `/events`: `Event.model_validate` in Pydantic's default (lax) mode,
so a numeric string such as "45.51" is accepted as a number, while a non-numeric string is
rejected. Mapped `fields` values are type-checked strictly, also exactly like `/events`.

Auth is the input's ingest key, same as `/events`, via `X-Ingest-Key` or `?key=` for senders that
can't set headers (the docs note a query-string key can end up in sender/proxy logs).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import ValidationError

from app.api.deps import get_db, ingest_key_header
from app.api.ingest import _check_fields, _check_position
from app.connectors.mapping import PathNotFound as _PathNotFound
from app.connectors.mapping import items as _items
from app.connectors.mapping import map_item as _map_item
from app.core import clock as clock_mod
from app.core.db import Db
from app.core.models import Event, WebhookReceipt, WebhookRejection

router = APIRouter(prefix="/v1/apps", tags=["webhooks"])

MAX_BODY_BYTES = 1_000_000
MAX_ITEMS = 500


def _short_reason(e: Exception) -> str:
    """One short line per rejected item: Pydantic errors condensed to `field: message` (no docs
    URL, no echoed input), anything else as its own message."""
    if isinstance(e, ValidationError):
        parts = [f"{'.'.join(map(str, x['loc'])) or 'item'}: {x['msg']}" for x in e.errors()[:3]]
        more = len(e.errors()) - 3
        return "; ".join(parts) + (f" (+{more} more)" if more > 0 else "")
    return str(e)


@router.post("/{app}/webhooks/{input}", response_model=WebhookReceipt, status_code=202)
async def post_webhook(
    app: str,
    input: str,
    request: Request,
    header_key: str | None = Depends(ingest_key_header),
    query_key: str | None = Query(default=None, alias="key"),
    db: Db = Depends(get_db),
) -> WebhookReceipt:
    key = header_key if header_key is not None else query_key
    if key is None or not db.check_ingest_key(app, key):
        raise HTTPException(status_code=401, detail="invalid ingest key")

    spec = db.get_app(app)
    inp = db.get_input(app, input)
    if spec is None or inp is None:
        raise HTTPException(status_code=404, detail="app or input not found")
    if spec.status != "active":
        raise HTTPException(status_code=403, detail="app is not active")
    if inp.kind != "push" or inp.webhook is None:
        raise HTTPException(status_code=409, detail="input is not a push input with a webhook mapping")

    content_length = request.headers.get("content-length")
    if content_length is not None and content_length.isdigit() and int(content_length) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail=f"body over {MAX_BODY_BYTES} bytes")
    # Content-Length can be absent (chunked transfer) or understate the real size, so the real
    # limit is enforced on the stream itself -- a sender can't force the full body into memory
    # by lying about or omitting the header.
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail=f"body over {MAX_BODY_BYTES} bytes")
    raw = bytes(chunks)
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=422, detail=f"body is not valid JSON: {e}") from None

    try:
        items = _items(body, inp.webhook.items_path)
    except _PathNotFound as e:
        raise HTTPException(status_code=422, detail=f"items_path: {e}") from None
    if len(items) > MAX_ITEMS:
        raise HTTPException(status_code=413, detail=f"over {MAX_ITEMS} items in one call")

    now = datetime.fromtimestamp(clock_mod.now(), UTC)
    accepted = 0
    rejected: list[WebhookRejection] = []
    for index, item in enumerate(items):
        try:
            mapped = _map_item(item, inp.webhook, now)
            event = Event.model_validate(mapped)
            reason = _check_position(spec, event) or _check_fields(inp.fields, event.model_extra or {})
            if reason is not None:
                raise ValueError(reason)
        except Exception as e:  # noqa: BLE001 - one bad item must not fail the whole call
            if len(rejected) < 20:
                rejected.append(WebhookRejection(index=index, reason=_short_reason(e)))
            continue
        db.insert_event(app, input, event, received_at=clock_mod.now())
        accepted += 1

    return WebhookReceipt(received=len(items), accepted=accepted, rejected=rejected)
