"""`POST /v1/apps/{app}/simulate` (#268): generate test events for one push input (authority key).

A dev tool for the console: it lets the dev see the map, the Critic and the spoof checks react
without a real device in the field. Generated events go through the SAME validation and storage
as `POST /v1/apps/{app}/inputs/{input}/events` (`api/ingest._check_position`, `_check_fields`,
`Db.insert_event`), so nothing reaches the engine that a real push couldn't send. The engine and
agents see them only as ordinary events (rule 1: this route writes no weights, trust or POIs).

Modes:
  - ``normal``: points scattered uniformly within ``radius_m`` of the centre, one distinct
    ``user_id`` ``sim-<n>`` each, timestamps spread over the last few tens of seconds.
  - ``spoof``: brand-new ``user_id``s ``spoof-<n>`` all at exactly the centre and within a few
    seconds, the pattern `engine/trust.py`'s duplicate-coordinate and new-ID-burst checks look for.

Ids keep counting per app across requests (`sim-1`, `sim-2`, ...), so every generated user is
new. Required input fields get neutral defaults (string "simulated", number 0, integer 0,
boolean false). Events outside the app's area or failing the field checks are counted as
rejected, not stored.

Tracing (rule 11): each request writes one TraceEntry. `AgentName` has no dev value, so it follows
the connector-poll convention (`connectors/engine.py`): ``agent="scout"`` (the area that brings
data in) with ``action="simulate"`` and a `why` that says the events are simulated and were asked
for by the dev. The trace is authority-only (rule 8); the response carries counts only.
Nothing here is simulator ground truth (rule 9): the events are ordinary inputs, labelled as such.
"""

from __future__ import annotations

import math
import re
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import authority_key_header, get_db
from app.api.ingest import OUTSIDE_AREA, _check_fields, _check_position
from app.api.trace import _authority
from app.core import clock as clock_mod
from app.core.db import Db
from app.core.models import Event, FieldType, InputField, SimulateRequest, SimulateResult, TraceEntry

router = APIRouter(prefix="/v1/apps", tags=["simulate"])

PREFIX = {"normal": "sim-", "spoof": "spoof-"}
NORMAL_SPREAD_S = 30.0  # normal events: timestamps spread over the last 30 s
SPOOF_SPREAD_S = 3.0  # spoof events: all within a few seconds
_M_PER_DEG_LAT = 111_320.0

_DEFAULTS: dict[FieldType, Any] = {"string": "simulated", "number": 0.0, "integer": 0, "boolean": False}


def get_sim_rng(request: Request) -> np.random.Generator:
    """RNG for generated positions and times. Unseeded (OS entropy) in production; tests override
    this dependency with a seeded generator."""
    return np.random.default_rng()


class _Counters:
    """Next `<n>` per (app, prefix). Started lazily from the highest id already stored, so ids stay
    new across requests and restarts."""

    def __init__(self) -> None:
        self._next: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def take(self, db: Db, app_id: str, prefix: str, n: int) -> int:
        with self._lock:
            key = (app_id, prefix)
            if key not in self._next:
                pat = re.compile(rf"^{re.escape(prefix)}(\d+)$")
                hi = 0
                for e in db.list_events(app_id):
                    m = pat.match(e.user_id or "")
                    if m:
                        hi = max(hi, int(m.group(1)))
                self._next[key] = hi + 1
            start = self._next[key]
            self._next[key] = start + n
            return start


def _counters(request: Request) -> _Counters:
    state = request.app.state
    c = getattr(state, "sim_counters", None)
    if c is None:
        c = state.sim_counters = _Counters()
    return c


def _field_defaults(fields: list[InputField]) -> dict[str, Any]:
    return {f.name: _DEFAULTS[f.type] for f in fields if f.required}


def _positions(body: SimulateRequest, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    if body.mode == "spoof":
        return np.full(body.count, body.lat), np.full(body.count, body.lon)
    r = body.radius_m * np.sqrt(rng.random(body.count))  # uniform over the disc
    a = rng.random(body.count) * 2 * math.pi
    dlat = r * np.sin(a) / _M_PER_DEG_LAT
    dlon = r * np.cos(a) / (_M_PER_DEG_LAT * max(math.cos(math.radians(body.lat)), 1e-6))
    return body.lat + dlat, body.lon + dlon


@router.post("/{app}/simulate", status_code=202, response_model=SimulateResult)
def simulate(
    app: str,
    body: SimulateRequest,
    request: Request,
    db: Db = Depends(get_db),
    rng: np.random.Generator = Depends(get_sim_rng),
    authority_key: str | None = Depends(authority_key_header),
) -> SimulateResult:
    _authority(db, app, authority_key)  # 404 unknown app, 401 missing/wrong key
    spec = db.get_app(app)
    if spec is None or spec.status != "active":
        raise HTTPException(status_code=409, detail="app is not active")
    inp = next((i for i in spec.inputs if i.id == body.input_id), None)
    if inp is None:
        raise HTTPException(status_code=404, detail="input not found")
    if inp.kind != "push":
        raise HTTPException(status_code=422, detail="only push inputs can be simulated")
    centre = Event(lat=body.lat, lon=body.lon, ts=datetime.now(UTC))
    if _check_position(spec, centre) is not None:
        raise HTTPException(status_code=422, detail=f"centre: {OUTSIDE_AREA}")

    prefix = PREFIX[body.mode]
    start = _counters(request).take(db, app, prefix, body.count)
    now = clock_mod.now()
    spread = SPOOF_SPREAD_S if body.mode == "spoof" else NORMAL_SPREAD_S
    offsets = np.sort(rng.random(body.count) * spread)[::-1]  # oldest first
    lats, lons = _positions(body, rng)
    extras = _field_defaults(inp.fields)

    accepted = rejected = 0
    for i in range(body.count):
        ts = datetime.fromtimestamp(now, UTC) - timedelta(seconds=float(offsets[i]))
        try:
            event = Event.model_validate(
                {"user_id": f"{prefix}{start + i}", "lat": float(lats[i]), "lon": float(lons[i]), "ts": ts, **extras}
            )
        except Exception:  # noqa: BLE001 - e.g. a point pushed past a pole; counted, not stored
            rejected += 1
            continue
        if _check_position(spec, event) or _check_fields(inp.fields, event.model_extra or {}):
            rejected += 1
            continue
        db.insert_event(app, inp.id, event, received_at=clock_mod.now())
        accepted += 1

    db.add_trace(
        TraceEntry(
            t=datetime.fromtimestamp(now, UTC),
            app_id=app,
            agent="scout",
            saw=f"dev asked for {body.count} simulated {body.mode} event(s) on input {inp.id!r}",
            result=f"{accepted} accepted, {rejected} rejected (outside the area or failed field checks)",
            action="simulate",
            why=f"SIMULATED test data requested by the dev, not real reports; user_ids "
            f"{prefix}{start}..{prefix}{start + body.count - 1}",
        )
    )
    return SimulateResult(accepted=accepted, rejected=rejected, user_id_prefix=prefix)
