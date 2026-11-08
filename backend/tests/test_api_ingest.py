import pytest
from fastapi.testclient import TestClient

from app.core.db import Db
from app.main import create_app


def _spec(**fields_over) -> dict:
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
                "fields": [{"name": "confidence", "type": "number", "required": fields_over.get("required", False)}],
                "operators": [{"op": "density", "weight": 1.0}],
            },
            {
                "id": "weather",
                "kind": "pull",
                "shape": "field",
                "tier": "stiff",
                "meaning": "wind speed",
                "connector": {"type": "json_api", "preset": "open_meteo"},
            },
        ],
        "outputs": {"layers": [{"id": "score", "visibility": "public"}]},
    }


@pytest.fixture
def client():
    with TestClient(create_app(db=Db(":memory:"), autostart_engine=False)) as c:
        yield c


@pytest.fixture
def ingest_key(client):
    client.post("/v1/apps", json=_spec())
    return client.post("/v1/apps/test-app/activate").json()["ingest_key"]


def _event(**over):
    body = {"lat": 0.005, "lon": 0.005, "ts": "2026-01-01T00:00:00Z"}
    body.update(over)
    return body


def test_valid_event_is_accepted(client, ingest_key):
    r = client.post("/v1/apps/test-app/inputs/pings/events", json=_event(), headers={"X-Ingest-Key": ingest_key})
    assert r.status_code == 202


def test_wrong_ingest_key_is_401(client, ingest_key):
    r = client.post("/v1/apps/test-app/inputs/pings/events", json=_event(), headers={"X-Ingest-Key": "wrong"})
    assert r.status_code == 401


def test_missing_ingest_key_is_401(client, ingest_key):
    r = client.post("/v1/apps/test-app/inputs/pings/events", json=_event())
    assert r.status_code == 401


def test_unknown_input_is_404(client, ingest_key):
    r = client.post("/v1/apps/test-app/inputs/nope/events", json=_event(), headers={"X-Ingest-Key": ingest_key})
    assert r.status_code == 404


def test_pull_input_is_not_a_valid_ingest_target(client, ingest_key):
    r = client.post("/v1/apps/test-app/inputs/weather/events", json=_event(), headers={"X-Ingest-Key": ingest_key})
    assert r.status_code == 404


def test_inactive_app_rejects_events():
    with TestClient(create_app(db=Db(":memory:"), autostart_engine=False)) as c:
        c.post("/v1/apps", json=_spec())
        # never activated, so there's no ingest key yet; use a key that will never match
        r = c.post("/v1/apps/test-app/inputs/pings/events", json=_event(), headers={"X-Ingest-Key": "x"})
        assert r.status_code == 401  # no keys exist yet for a draft app


def test_undeclared_field_is_422(client, ingest_key):
    r = client.post(
        "/v1/apps/test-app/inputs/pings/events",
        json=_event(bogus="x"),
        headers={"X-Ingest-Key": ingest_key},
    )
    assert r.status_code == 422


def test_wrong_field_type_is_422(client, ingest_key):
    r = client.post(
        "/v1/apps/test-app/inputs/pings/events",
        json=_event(confidence="not-a-number"),
        headers={"X-Ingest-Key": ingest_key},
    )
    assert r.status_code == 422


def test_boolean_is_rejected_for_a_number_field(client, ingest_key):
    r = client.post(
        "/v1/apps/test-app/inputs/pings/events",
        json=_event(confidence=True),
        headers={"X-Ingest-Key": ingest_key},
    )
    assert r.status_code == 422


def test_missing_required_field_is_422():
    with TestClient(create_app(db=Db(":memory:"), autostart_engine=False)) as c:
        c.post("/v1/apps", json=_spec(required=True))
        key = c.post("/v1/apps/test-app/activate").json()["ingest_key"]
        r = c.post("/v1/apps/test-app/inputs/pings/events", json=_event(), headers={"X-Ingest-Key": key})
        assert r.status_code == 422


def test_invalid_event_body_is_422(client, ingest_key):
    r = client.post(
        "/v1/apps/test-app/inputs/pings/events",
        json={"lat": 999, "lon": 0.0, "ts": "2026-01-01T00:00:00Z"},
        headers={"X-Ingest-Key": ingest_key},
    )
    assert r.status_code == 422


@pytest.mark.parametrize("lat,lon", [(40.0, 0.005), (0.005, 202 - 180), (-0.001, 0.005), (0.005, 0.0101)])
def test_position_outside_the_grid_bbox_is_422(client, ingest_key, lat, lon):
    r = client.post(
        "/v1/apps/test-app/inputs/pings/events", json=_event(lat=lat, lon=lon), headers={"X-Ingest-Key": ingest_key}
    )
    assert r.status_code == 422
    assert r.json()["detail"] == "position outside the app's area"


def test_position_on_the_bbox_edge_is_accepted(client, ingest_key):
    for lat, lon in [(0.0, 0.0), (0.01, 0.01)]:
        r = client.post(
            "/v1/apps/test-app/inputs/pings/events", json=_event(lat=lat, lon=lon), headers={"X-Ingest-Key": ingest_key}
        )
        assert r.status_code == 202, r.text


def test_out_of_range_latitude_is_still_a_validation_422(client, ingest_key):
    r = client.post("/v1/apps/test-app/inputs/pings/events", json=_event(lat=202), headers={"X-Ingest-Key": ingest_key})
    assert r.status_code == 422
