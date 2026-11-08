"""`api/outputs.py`: `GET /map?format=geojson` and `GET /hotspots`.

Both read the engine's latest `TickResult` and filter by visibility: no `X-Authority-Key`
header gets public data (rule 8); a *wrong* key is rejected with 401, not silently
downgraded to public. `build_map_response`/`build_hotspots_response` do the actual
filtering so `api/ws.py` streams exactly the same shapes over the WebSocket.

Hotspot visibility is decided in `engine/tick.py` (public only when every contributing layer,
including any active override layer, is a public output layer; else authority). Here it's
only filtered.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import authority_key_header, get_db, get_engine
from app.core.access import require_authority_if_present, visible
from app.core.db import Db
from app.core.models import AppSpec, CellCollection, HotspotsResponse, MapResponse
from app.engine.grid import Grid
from app.engine.tick import TickEngine, TickResult

router = APIRouter(prefix="/v1/apps", tags=["outputs"])

# grid.to_geojson() keeps cells with value > min_value; this matches "value < 0.01 omitted"
# (values are rounded to 4 decimals, so this margin never changes which cells are kept).
_CELL_MIN_VALUE = 0.0099995


def build_map_response(app_id: str, spec: AppSpec, grid: Grid, result: TickResult, authority: bool) -> MapResponse:
    public_ids = {ol.id for ol in spec.outputs.layers if ol.visibility == "public"}
    arrays = {"score": result.score, **dict(zip(result.layer_ids, result.normalized, strict=True))}
    layers = {
        layer_id: CellCollection.model_validate(grid.to_geojson(arr, min_value=_CELL_MIN_VALUE, property_name="value"))
        for layer_id, arr in arrays.items()
        if authority or layer_id in public_ids
    }
    return MapResponse(
        app_id=app_id,
        t=datetime.fromtimestamp(result.t, tz=UTC),
        cell_m=grid.cell_m,
        bbox=grid.bbox,
        layers=layers,
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
