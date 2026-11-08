"""MapResponse.vectors: a vector-field layer's per-cell (u, v), subsampled, with the layer's visibility (rule 8)."""

from datetime import UTC, datetime

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.api.outputs import MAX_VECTOR_CELLS, vector_grid
from app.core.clock import SimClock
from app.core.db import Db
from app.engine.tick import TickEngine
from app.main import DbEventSource, create_app

T0 = 1_800_000_000.0
FIELD_LAYER = "stations.field"


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _spec(field_public: bool) -> dict:
    return {
        "app_id": "vec-app",
        "intent": "show where readings are high and which way the vector field runs",
        "grid": {"cell_m": 10, "bbox": {"min_lat": 0.0, "min_lon": 0.0, "max_lat": 0.01, "max_lon": 0.01}},
        "inputs": [
            {
                "id": "stations",
                "kind": "push",
                "shape": "field",
                "tier": "stiff",
                "meaning": "a station reports a reading and a vector",
                "fields": [
                    {"name": "value", "type": "number", "required": True},
                    {"name": "mag", "type": "number", "required": True},
                    {"name": "deg", "type": "number", "required": True},
                ],
                "operators": [
                    {
                        "op": "field",
                        "weight": 0.5,
                        "vector_speed_field": "mag",
                        "vector_dir_field": "deg",
                        "vector_dir_convention": "from",
                    }
                ],
            }
        ],
        "outputs": {"layers": [{"id": FIELD_LAYER, "visibility": "public" if field_public else "authority"}]},
    }


def _setup(field_public: bool):
    db = Db(":memory:")
    clock = SimClock(start=T0)
    engine = TickEngine(DbEventSource(db), clock=clock)
    app_obj = create_app(db=db, engine=engine, autostart_engine=False)
    client = TestClient(app_obj)
    client.__enter__()
    assert client.post("/v1/apps", json=_spec(field_public)).status_code < 300
    keys = client.post("/v1/apps/vec-app/activate").json()
    # two stations: from the west in the SW corner, from the south in the NE corner
    for uid, lat, lon, deg in (("a", 0.001, 0.001, 270.0), ("b", 0.009, 0.009, 180.0)):
        r = client.post(
            "/v1/apps/vec-app/inputs/stations/events",
            json={"user_id": uid, "lat": lat, "lon": lon, "ts": _iso(T0), "value": 1.0, "mag": 4.0, "deg": deg},
            headers={"X-Ingest-Key": keys["ingest_key"]},
        )
        assert r.status_code < 300, r.text
    engine.tick_once()
    return client, keys


@pytest.fixture(params=[True, False], ids=["public", "authority"])
def setup(request):
    client, keys = _setup(request.param)
    try:
        yield client, keys, request.param
    finally:
        client.__exit__(None, None, None)


def _check_vectors(vectors: dict) -> None:
    vg = vectors[FIELD_LAYER]
    assert vg["step"] >= 1
    assert 0 < len(vg["cells"]) <= MAX_VECTOR_CELLS
    cells = {(c["row"], c["col"]): c for c in vg["cells"]}
    sw = min(cells, key=lambda rc: rc[0] + rc[1])
    ne = max(cells, key=lambda rc: rc[0] + rc[1])
    # "from" convention resolved: from the west flows east (u > 0); from the south flows north (v > 0)
    assert cells[sw]["u"] > abs(cells[sw]["v"])
    assert cells[ne]["v"] > abs(cells[ne]["u"])


def test_rest_vectors_follow_the_layer_visibility(setup):
    client, keys, public = setup
    auth = client.get("/v1/apps/vec-app/map", headers={"X-Authority-Key": keys["authority_key"]}).json()
    _check_vectors(auth["vectors"])
    pub = client.get("/v1/apps/vec-app/map").json()
    if public:
        _check_vectors(pub["vectors"])
    else:
        assert pub["vectors"] == {}


def test_ws_vectors_follow_the_layer_visibility(setup):
    client, keys, public = setup
    with client.websocket_connect("/v1/apps/vec-app/stream") as ws:
        pub = ws.receive_json()
    assert pub["type"] == "tick"
    if public:
        _check_vectors(pub["map"]["vectors"])
    else:
        assert pub["map"]["vectors"] == {}
    with client.websocket_connect("/v1/apps/vec-app/stream") as ws:
        ws.send_json({"type": "auth", "key": keys["authority_key"]})
        assert ws.receive_json()["authority"] is True
        tick = ws.receive_json()
    _check_vectors(tick["map"]["vectors"])


def test_vector_grid_caps_cells_and_skips_empty_fields():
    u = np.ones((200, 300))
    vg = vector_grid(u, -u)
    assert vg is not None and len(vg.cells) <= MAX_VECTOR_CELLS
    assert all(c.u == 1.0 and c.v == -1.0 for c in vg.cells)
    small = vector_grid(np.full((3, 4), 2.0), np.zeros((3, 4)))
    assert small is not None and small.step == 1 and len(small.cells) == 12
    assert vector_grid(np.zeros((5, 5)), np.zeros((5, 5))) is None  # no readings: no arrows
    assert vector_grid(np.ones((2, 2)), np.ones((3, 3))) is None
