"""`GET /v1/operators`: the public operator catalogue, the same one the Compiler reads via `list_operators`.

Platform data only (names, descriptions, accepted shapes, params schemas), never app data, so no key.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.models import OperatorInfo
from app.engine.operators import registry

router = APIRouter(prefix="/v1", tags=["catalog"])


@router.get("/operators", response_model=list[OperatorInfo])
def get_operators() -> list[OperatorInfo]:
    return [OperatorInfo.model_validate(o) for o in registry.list_operators()]
