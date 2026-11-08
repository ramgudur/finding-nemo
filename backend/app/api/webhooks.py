"""`POST /v1/apps/{app}/webhooks/{input}`: another service's own JSON, mapped by config (rule 10,
#222) into events, validated exactly like `POST /{app}/inputs/{input}/events`.

The mapping (`InputSpec.webhook`, push inputs only) reads each item with dotted paths and
optional list indices (e.g. "geometry.coordinates.1") -- data only, never code. The raw request
body is never stored (rule 8): only per-item accept/reject outcomes are kept, and only the first
20 rejection reasons are returned.

Auth is the input's ingest key, same as `/events`, via `X-Ingest-Key` or `?key=` for senders that
can't set headers (the docs note a query-string key can end up in sender/proxy logs).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import get_db, ingest_key_header
from app.api.ingest import _check_fields
from app.core import clock as clock_mod
from app.core.db import Db
from app.core.models import Event, TsFormat, WebhookMapping, WebhookReceipt, WebhookRejection

router = APIRouter(prefix="/v1/apps", tags=["webhooks"])

MAX_BODY_BYTES = 1_000_000
MAX_ITEMS = 500


class _PathNotFound(Exception):
    pass


def _dig(data: Any, path: str) -> Any:
    """Dotted path with optional list indices, e.g. "geometry.coordinates.1"."""
    node = data
    for part in path.split(".") if path else []:
        if isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError) as e:
                raise _PathNotFound(path) from e
        elif isinstance(node, dict):
            if part not in node:
                raise _PathNotFound(path)
            node = node[part]
        else:
            raise _PathNotFound(path)
    return node


def _items(body: Any, items_path: str) -> list[Any]:
    node = _dig(body, items_path)
    return node if isinstance(node, list) else [node]


def _parse_ts(raw: Any, ts_format: TsFormat) -> datetime:
    if ts_format == "epoch_s":
        return datetime.fromtimestamp(float(raw), tz=UTC)
    if ts_format == "epoch_ms":
        return datetime.fromtimestamp(float(raw) / 1000.0, tz=UTC)
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))


def _map_item(item: Any, mapping: WebhookMapping, now: datetime) -> dict[str, Any]:
    """One item -> an `/events`-shaped body, or raises with a reason this item is rejected."""
    out: dict[str, Any] = {"lat": _dig(item, mapping.lat), "lon": _dig(item, mapping.lon)}
    out["ts"] = _parse_ts(_dig(item, mapping.ts), mapping.ts_format) if mapping.ts else now
    if mapping.user_id:
        out["user_id"] = _dig(item, mapping.user_id)
    for name, path in mapping.fields.items():
        out[name] = _dig(item, path)
    return out


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

    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail=f"body over {MAX_BODY_BYTES} bytes")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=422, detail=f"body is not valid JSON: {e}") from None

    try:
        items = _items(body, inp.webhook.items_path)
    except _PathNotFound as e:
        raise HTTPException(status_code=422, detail=f"items_path not found: {e}") from None
    if len(items) > MAX_ITEMS:
        raise HTTPException(status_code=413, detail=f"over {MAX_ITEMS} items in one call")

    now = datetime.fromtimestamp(clock_mod.now(), UTC)
    accepted = 0
    rejected: list[WebhookRejection] = []
    for index, item in enumerate(items):
        try:
            mapped = _map_item(item, inp.webhook, now)
            event = Event.model_validate(mapped)
            reason = _check_fields(inp.fields, event.model_extra or {})
            if reason is not None:
                raise ValueError(reason)
        except Exception as e:  # noqa: BLE001 - one bad item must not fail the whole call
            if len(rejected) < 20:
                rejected.append(WebhookRejection(index=index, reason=str(e)))
            continue
        db.insert_event(app, input, event, received_at=clock_mod.now())
        accepted += 1

    return WebhookReceipt(received=len(items), accepted=accepted, rejected=rejected)
