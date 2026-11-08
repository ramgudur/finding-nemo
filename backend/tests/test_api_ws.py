from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.clock import SimClock
from app.core.db import Db
from app.engine.tick import TickEngine
from app.main import DbEventSource, create_app

T0 = 1_800_000_000.0


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _spec() -> dict:
    return {
        "app_id": "test-app",
        "intent": "find where people gather",
        "grid": {"cell_m": 10, "bbox": {"min_lat": 0.0, "min_lon": 0.0, "max_lat": 0.01, "max_lon": 0.01}},
        "inputs": [
            {
                "id": "pings",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "a ping means someone is here",
                "operators": [{"op": "density", "weight": 1.0}],
            }
        ],
        "outputs": {"layers": [{"id": "score", "visibility": "public"}, {"id": "pings.density"}]},
    }


@pytest.fixture
def setup():
    db = Db(":memory:")
    clock = SimClock(start=T0)
    engine = TickEngine(DbEventSource(db), clock=clock)
    app_obj = create_app(db=db, engine=engine, autostart_engine=False)
    with TestClient(app_obj) as client:
        client.post("/v1/apps", json=_spec())
        keys = client.post("/v1/apps/test-app/activate").json()
        for i in range(5):
            client.post(
                "/v1/apps/test-app/inputs/pings/events",
                json={"user_id": f"u{i}", "lat": 0.005, "lon": 0.005, "ts": _iso(T0)},
                headers={"X-Ingest-Key": keys["ingest_key"]},
            )
        engine.tick_once()
        yield client, keys


def test_public_viewer_gets_tick_frames_with_only_public_layers(setup):
    client, keys = setup
    with client.websocket_connect("/v1/apps/test-app/stream") as ws:
        frame = ws.receive_json()
    assert frame["type"] == "tick"
    assert set(frame["map"]["layers"]) <= {"score"}
    assert frame["hotspots"]["hotspots"] == []  # pings.density is authority-only here


def test_valid_auth_frame_unlocks_authority_data(setup):
    client, keys = setup
    with client.websocket_connect("/v1/apps/test-app/stream") as ws:
        ws.send_json({"type": "auth", "key": keys["authority_key"]})
        auth_ok = ws.receive_json()
        tick = ws.receive_json()
    assert auth_ok == {"type": "auth_ok", "authority": True}
    assert tick["type"] == "tick"
    assert "pings.density" in tick["map"]["layers"]
    assert tick["hotspots"]["hotspots"]


def test_bad_auth_key_stays_public(setup):
    client, keys = setup
    with client.websocket_connect("/v1/apps/test-app/stream") as ws:
        ws.send_json({"type": "auth", "key": "wrong"})
        auth_ok = ws.receive_json()
        tick = ws.receive_json()
    assert auth_ok == {"type": "auth_ok", "authority": False}
    assert set(tick["map"]["layers"]) <= {"score"}
    assert tick["hotspots"]["hotspots"] == []


def test_auth_key_is_never_echoed_back(setup):
    client, keys = setup
    with client.websocket_connect("/v1/apps/test-app/stream") as ws:
        ws.send_json({"type": "auth", "key": keys["authority_key"]})
        auth_ok = ws.receive_json()
        ws.receive_json()  # the tick frame
    assert keys["authority_key"] not in str(auth_ok)


def test_unknown_app_closes_the_socket(setup):
    client, keys = setup
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/v1/apps/nope/stream") as ws:
            ws.receive_json()
