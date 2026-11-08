"""`GET /v1/apps/{app}/sources/status`: how each input's data is arriving (#267, console).

Authority key required (401 without it, rule 8): a source's poll status and event counts are
part of the authority view. Read-only; nothing here changes state.

- `via`: push inputs are `webhook` when they carry a webhook mapping, else `post_api`; pull inputs
  are their connector type (null for a pull input that has no connector yet).
- `last_polled_at` / `last_status` come from the connector's poll record (`record_poll` in
  `connectors/engine.py`), whose status is "ok" or "error: <reason>" with no URL and no body.
- `next_poll_at` = `last_polled_at + poll_s`, while the app is active.
- Event counts are one aggregate query per input over the events index; "last hour" is by event
  time, read from the Clock.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import authority_key_header, get_db
from app.core import clock as clock_mod
from app.core.db import Db
from app.core.models import InputSpec, SourceStatus, SourceVia

router = APIRouter(prefix="/v1/apps", tags=["console"])

HOUR_S = 3600.0


def _dt(t: float | None) -> datetime | None:
    return datetime.fromtimestamp(t, tz=UTC) if t is not None else None


def _via(inp: InputSpec) -> SourceVia | None:
    if inp.kind == "push":
        return "webhook" if inp.webhook is not None else "post_api"
    return inp.connector.type if inp.connector is not None else None


@router.get("/{app}/sources/status", response_model=list[SourceStatus])
def sources_status(
    app: str,
    db: Db = Depends(get_db),
    authority_key: str | None = Depends(authority_key_header),
) -> list[SourceStatus]:
    spec = db.get_app(app)
    if spec is None:
        raise HTTPException(status_code=404, detail="app not found")
    if authority_key is None or not db.check_authority_key(app, authority_key):
        raise HTTPException(status_code=401, detail="authority key required")
    now = clock_mod.now()
    out: list[SourceStatus] = []
    for inp in spec.inputs:
        total, recent, last_ts = db.event_counts(app, inp.id, since=now - HOUR_S)
        poll_s = inp.connector.poll_s if inp.kind == "pull" and inp.connector is not None else None
        polled_at, status = (None, None)
        if poll_s is not None:
            polled_at, status = db.get_connector_status(app, inp.id) or (None, None)
        next_poll = (
            polled_at + poll_s if polled_at is not None and poll_s is not None and spec.status == "active" else None
        )
        out.append(
            SourceStatus(
                input_id=inp.id,
                kind=inp.kind,
                via=_via(inp),
                poll_s=max(1, round(poll_s)) if poll_s is not None else None,
                last_polled_at=_dt(polled_at),
                next_poll_at=_dt(next_poll),
                last_status=status,
                events_total=total,
                events_last_hour=recent,
                last_event_at=_dt(last_ts),
            )
        )
    return out
