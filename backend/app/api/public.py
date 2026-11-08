"""Public app info: `GET /v1/apps/{app}/public` (no key), for the end-user page (#266).

Rule 8: only what the public may see. The intent (the dev's own description, written for the
people the app serves), the area, whether routing is on and when the map last updated. Sources are
listed by name (the input's meaning, cut to 80 characters) only when the input has at least one
`public` output layer; every other input is only counted, per kind ("N private sources"). Never
an input id, a connector URL, a field, an operator, a weight, a tier or a layer value.
Works for every status, and says which one; 404 for an unknown app.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.deps import get_db, get_engine
from app.core.db import Db
from app.core.models import AppSpec, InputKind, PublicAppInfo, PublicSource
from app.engine.tick import TickEngine

router = APIRouter(prefix="/v1/apps", tags=["public"])

LABEL_MAX = 80


def title_of(app_id: str) -> str:
    """From the app id: "la-wildfire_watch" -> "La Wildfire Watch"."""
    return " ".join(w.capitalize() for w in re.split(r"[-_]+", app_id) if w)


def public_sources(spec: AppSpec) -> list[PublicSource]:
    public_layers = {ol.id for ol in spec.outputs.layers if ol.visibility == "public"}
    out: list[PublicSource] = []
    private: Counter[InputKind] = Counter()
    for inp in spec.inputs:
        if any(op.layer_id in public_layers for op in inp.operators):
            label = " ".join(inp.meaning.split())
            if len(label) > LABEL_MAX:
                label = label[: LABEL_MAX - 3].rstrip() + "..."
            out.append(PublicSource(kind=inp.kind, label=label))
        else:
            private[inp.kind] += 1
    for kind in ("push", "pull"):
        n = private[kind]
        if n:
            out.append(PublicSource(kind=kind, label=f"{n} private source{'' if n == 1 else 's'}"))
    return out


@router.get("/{app}/public", response_model=PublicAppInfo)
def get_public_info(
    app: str,
    response: Response,
    db: Db = Depends(get_db),
    engine: TickEngine = Depends(get_engine),
) -> PublicAppInfo:
    spec = db.get_app(app)
    if spec is None:
        raise HTTPException(status_code=404, detail="app not found")
    latest = engine.latest(app) if spec.status == "active" else None
    response.headers["Cache-Control"] = "max-age=30"
    return PublicAppInfo(
        app_id=spec.app_id,
        title=title_of(spec.app_id),
        intent=spec.intent,
        status=spec.status,
        routing_enabled=spec.routing.enabled,
        bbox=spec.grid.bbox,
        last_update=datetime.fromtimestamp(latest.t, UTC) if latest is not None else None,
        sources=public_sources(spec),
    )
