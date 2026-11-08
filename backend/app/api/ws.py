"""`api/ws.py`: `WS /v1/apps/{app}/stream`.

First-frame auth: the client's first inbound frame may be `{type: "auth", key}`; the
reply is always `{type: "auth_ok", authority: bool}` (`false` for a bad key — the socket
stays public-only, never closed). The key is never echoed back or logged. Any other
inbound frame -- unparseable JSON, a non-object, or an unrecognized `type` -- gets
`{type: "error", reason: "malformed frame"}` (a fixed reason; never echoes the frame's
content) and the socket stays open with its current visibility. After that, every new
engine tick sends `{type: "tick", t, map: MapResponse, hotspots: HotspotsResponse}`,
filtered exactly like REST (`api/outputs.py`'s builders, which use `core/access.py`).

There's no push path from the tick loop to a socket (it runs in a worker thread via
`asyncio.to_thread`), so this polls `engine.latest(app)` on a short interval instead and
sends a new frame only when the tick's `t` has moved. At the tick cadence (2 s) this is
indistinguishable from a push, and it keeps the handler on a single asyncio task with no
cross-thread handoff.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.outputs import build_hotspots_response, build_map_response
from app.core.db import Db
from app.engine.tick import TickEngine

router = APIRouter(prefix="/v1/apps", tags=["ws"])

_POLL_S = 0.05


@router.websocket("/{app}/stream")
async def stream(websocket: WebSocket, app: str) -> None:
    db: Db = websocket.app.state.db
    engine: TickEngine = websocket.app.state.engine

    spec = db.get_app(app)
    await websocket.accept()
    if spec is None:
        await websocket.close(code=4404, reason="app not found")
        return

    authority = False
    last_t: float | None = None
    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=_POLL_S)
            except TimeoutError:
                raw = None

            if raw is not None:
                try:
                    msg = json.loads(raw)
                    if not isinstance(msg, dict) or msg.get("type") != "auth":
                        raise ValueError("not an auth frame")
                    key = msg.get("key")
                    authority = bool(key) and db.check_authority_key(app, key)
                    await websocket.send_json({"type": "auth_ok", "authority": authority})
                except WebSocketDisconnect:
                    raise
                except Exception:  # noqa: BLE001 - any malformed/unknown frame, not just JSON errors
                    await websocket.send_json({"type": "error", "reason": "malformed frame"})

            result = engine.latest(app)
            if result is not None and result.t != last_t:
                last_t = result.t
                spec = db.get_app(app) or spec
                grid = engine.apps[app].grid
                map_resp = build_map_response(app, spec, grid, result, authority)
                hotspots_resp = build_hotspots_response(app, spec, result, authority)
                await websocket.send_json(
                    {
                        "type": "tick",
                        "t": datetime.fromtimestamp(result.t, tz=UTC)
                        .isoformat(timespec="milliseconds")
                        .replace("+00:00", "Z"),
                        "map": map_resp.model_dump(mode="json"),
                        "hotspots": hotspots_resp.model_dump(mode="json"),
                    }
                )
    except WebSocketDisconnect:
        pass
