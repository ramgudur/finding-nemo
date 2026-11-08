"""`api/outputs.py`: `GET /map?format=geojson` and `GET /hotspots`.

Both read the engine's latest `TickResult` and filter by visibility: no `X-Authority-Key`
header gets public data (rule 8); a *wrong* key is rejected with 401, not silently
downgraded to public. Visibility filtering details beyond "authority sees everything"
are step 2; for now every hotspot is authority-only, matching `specs/campus-alert.json`.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import authority_key_header, get_db, get_engine
from app.core.access import require_authority_if_present, visible
from app.core.db import Db
from app.core.models import CellCollection, Hotspot, HotspotsResponse, MapResponse
from app.engine.tick import TickEngine

router = APIRouter(prefix="/v1/apps", tags=["outputs"])

# grid.to_geojson() keeps cells with value > min_value; this matches "value < 0.01 omitted"
# (values are rounded to 4 decimals, so this margin never changes which cells are kept).
_CELL_MIN_VALUE = 0.0099995


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
    public_ids = {ol.id for ol in spec.outputs.layers if ol.visibility == "public"}
    grid = engine.apps[app].grid
    arrays = {"score": result.score, **dict(zip(result.layer_ids, result.normalized, strict=True))}
    layers = {
        layer_id: CellCollection.model_validate(grid.to_geojson(arr, min_value=_CELL_MIN_VALUE, property_name="value"))
        for layer_id, arr in arrays.items()
        if authority or layer_id in public_ids
    }
    return MapResponse(
        app_id=app,
        t=datetime.fromtimestamp(result.t, tz=UTC),
        cell_m=grid.cell_m,
        bbox=grid.bbox,
        layers=layers,
    )


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
    hotspots = [
        Hotspot(
            id=h.id,
            lat=h.lat,
            lon=h.lon,
            radius_m=math.sqrt(h.area_m2 / math.pi),
            score=h.peak,
            mean=h.mean,
            area_m2=h.area_m2,
            n_cells=h.n_cells,
            persisted_ticks=h.persisted_ticks,
            contributing_layers=list(h.contributing_layers),
            visibility="authority",
        )
        for h in result.hotspots
    ]
    hotspots = [h for h in hotspots if visible(h.visibility, authority)]
    return HotspotsResponse(app_id=app, t=datetime.fromtimestamp(result.t, tz=UTC), hotspots=hotspots)
