"""`api/outputs.py`: `GET /map?format=geojson`, `GET /hotspots`, `GET /pois` and `GET /inputs/{input}/raw`.

Both read the engine's latest `TickResult` and filter by visibility: no `X-Authority-Key`
header gets public data (rule 8); a *wrong* key is rejected with 401, not silently
downgraded to public. `build_map_response`/`build_hotspots_response` do the actual
filtering so `api/ws.py` streams exactly the same shapes over the WebSocket.

Hotspot visibility is decided in `engine/tick.py` (public only when every contributing layer,
including any active override layer, is a public output layer; else authority). Here it's
only filtered. POIs are filtered per item by their own `visibility`. Raw events are authority-only:
a missing or wrong key is 401 (rule 8: raw inputs never reach a request without the key).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import authority_key_header, get_db, get_engine
from app.core.access import require_authority_if_present, visible
from app.core.db import Db
from app.core.models import (
    POI,
    AppSpec,
    CellCollection,
    Event,
    HotspotsResponse,
    MapResponse,
    UtcDatetime,
    VectorCell,
    VectorGrid,
)
from app.engine.grid import Grid
from app.engine.tick import TickEngine, TickResult

router = APIRouter(prefix="/v1/apps", tags=["outputs"])

# grid.to_geojson() keeps cells with value > min_value; this matches "value < 0.01 omitted"
# (values are rounded to 4 decimals, so this margin never changes which cells are kept).
_CELL_MIN_VALUE = 0.0099995
MAX_RAW = 5000
MAX_VECTOR_CELLS = 400  # per layer: the map draws one arrow per sampled cell


def vector_grid(u: Any, v: Any, max_cells: int = MAX_VECTOR_CELLS) -> VectorGrid | None:
    """Sample an operator's per-cell vector field (u east, v north) every `step` cells, at cell centres
    of each block, so at most `max_cells` arrows go out. None when there is no usable field."""
    try:
        uu = np.asarray(u, dtype=float)
        vv = np.asarray(v, dtype=float)
    except (TypeError, ValueError):
        return None
    if uu.ndim != 2 or uu.shape != vv.shape or uu.size == 0:
        return None
    h, w = uu.shape
    step = max(1, math.ceil(math.sqrt(h * w / max_cells)))
    while math.ceil(h / step) * math.ceil(w / step) > max_cells:
        step += 1
    off = step // 2
    cells = []
    for r in range(min(off, h - 1), h, step):
        for c in range(min(off, w - 1), w, step):
            cu, cv = float(uu[r, c]), float(vv[r, c])
            if math.isfinite(cu) and math.isfinite(cv) and (cu or cv):
                cells.append(VectorCell(row=r, col=c, u=round(cu, 3), v=round(cv, 3)))
    return VectorGrid(step=step, cells=cells) if cells else None


def build_map_response(app_id: str, spec: AppSpec, grid: Grid, result: TickResult, authority: bool) -> MapResponse:
    public_ids = {ol.id for ol in spec.outputs.layers if ol.visibility == "public"}
    arrays = {"score": result.score, **dict(zip(result.layer_ids, result.normalized, strict=True))}
    layers = {
        layer_id: CellCollection.model_validate(grid.to_geojson(arr, min_value=_CELL_MIN_VALUE, property_name="value"))
        for layer_id, arr in arrays.items()
        if authority or layer_id in public_ids
    }
    # A layer's vector field follows that layer's visibility (rule 8).
    vectors = {}
    for layer_id, ex in (result.extras or {}).items():
        if not isinstance(ex, dict) or "vector_u" not in ex or not (authority or layer_id in public_ids):
            continue
        vg = vector_grid(ex["vector_u"], ex.get("vector_v"))
        if vg is not None:
            vectors[layer_id] = vg
    return MapResponse(
        app_id=app_id,
        t=datetime.fromtimestamp(result.t, tz=UTC),
        cell_m=grid.cell_m,
        bbox=grid.bbox,
        layers=layers,
        vectors=vectors,
    )


def build_hotspots_response(app_id: str, spec: AppSpec, result: TickResult, authority: bool) -> HotspotsResponse:
    """tick.py emits models.Hotspot with its visibility already set (from contributing layers);
    this only filters, so REST and the WebSocket serve exactly the same items."""
    hotspots = [h for h in result.hotspots if visible(h.visibility, authority)]
    return HotspotsResponse(app_id=app_id, t=datetime.fromtimestamp(result.t, tz=UTC), hotspots=hotspots)


@router.get("/{app}/map", response_model=MapResponse)
def get_map(
    app: str,
    format: str = "geojson",
    db: Db = Depends(get_db),
    engine: TickEngine = Depends(get_engine),
    authority_key: str | None = Depends(authority_key_header),
) -> MapResponse:
    if format != "geojson":
        raise HTTPException(status_code=400, detail="only format=geojson is supported")
    spec = db.get_app(app)
    if spec is None:
        raise HTTPException(status_code=404, detail="app not found")
    result = engine.latest(app)
    if result is None:
        raise HTTPException(status_code=404, detail="app has not ticked yet")

    authority = require_authority_if_present(db, app, authority_key)
    grid = engine.apps[app].grid
    return build_map_response(app, spec, grid, result, authority)


@router.get("/{app}/hotspots", response_model=HotspotsResponse)
def get_hotspots(
    app: str,
    db: Db = Depends(get_db),
    engine: TickEngine = Depends(get_engine),
    authority_key: str | None = Depends(authority_key_header),
) -> HotspotsResponse:
    spec = db.get_app(app)
    if spec is None:
        raise HTTPException(status_code=404, detail="app not found")
    result = engine.latest(app)
    if result is None:
        raise HTTPException(status_code=404, detail="app has not ticked yet")

    authority = require_authority_if_present(db, app, authority_key)
    return build_hotspots_response(app, spec, result, authority)


@router.get("/{app}/pois", response_model=list[POI])
def get_pois(
    app: str,
    db: Db = Depends(get_db),
    authority_key: str | None = Depends(authority_key_header),
) -> list[POI]:
    """Active POIs; without the authority key, only `public` ones (sensitive labels stay private)."""
    if db.get_app(app) is None:
        raise HTTPException(status_code=404, detail="app not found")
    authority = require_authority_if_present(db, app, authority_key)
    return [p for p in db.list_pois(app, status="active") if visible(p.visibility, authority)]


@router.get("/{app}/inputs/{input_id}/raw", response_model=list[Event])
def get_raw(
    app: str,
    input_id: str,
    since: UtcDatetime | None = Query(default=None, description="Only events with ts after this time."),
    limit: int = Query(default=1000, ge=1, le=MAX_RAW, description="At most this many, the most recent."),
    db: Db = Depends(get_db),
    authority_key: str | None = Depends(authority_key_header),
) -> list[Event]:
    spec = db.get_app(app)
    if spec is None:
        raise HTTPException(status_code=404, detail="app not found")
    if authority_key is None or not db.check_authority_key(app, authority_key):
        raise HTTPException(status_code=401, detail="authority key required")
    if not any(i.id == input_id for i in spec.inputs):
        raise HTTPException(status_code=404, detail="input not found")
    since_s = since.timestamp() if since is not None else None
    return db.list_events(app, input_id=input_id, since=since_s, limit=limit)  # most recent `limit`, in SQL
