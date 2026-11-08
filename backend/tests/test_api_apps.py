import pytest
from fastapi.testclient import TestClient

from app.core.db import Db
from app.main import create_app


def _spec(app_id: str = "test-app") -> dict:
    return {
        "app_id": app_id,
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
def app():
    return create_app(db=Db(":memory:"), autostart_engine=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def test_create_app_returns_draft_spec(client):
    r = client.post("/v1/apps", json=_spec())
    assert r.status_code == 201
    assert r.json()["status"] == "draft"


def test_create_app_rejects_duplicate_app_id(client):
    client.post("/v1/apps", json=_spec())
    r = client.post("/v1/apps", json=_spec())
    assert r.status_code == 409


def test_create_app_rejects_already_active_spec(client):
    r = client.post("/v1/apps", json={**_spec(), "status": "active"})
    assert r.status_code == 422


def test_get_app_requires_authority_key(client):
    client.post("/v1/apps", json=_spec())
    assert client.get("/v1/apps/test-app").status_code == 401
    r = client.get("/v1/apps/test-app", headers={"X-Authority-Key": "wrong"})
    assert r.status_code == 401


def test_get_app_missing_is_404(client):
    assert client.get("/v1/apps/nope", headers={"X-Authority-Key": "x"}).status_code == 404


def test_activate_generates_keys_and_flips_status(client):
    client.post("/v1/apps", json=_spec())
    r = client.post("/v1/apps/test-app/activate")
    assert r.status_code == 200
    body = r.json()
    assert body["app_id"] == "test-app"
    assert body["ingest_key"] and body["authority_key"]
    assert body["ingest_key"] != body["authority_key"]

    got = client.get("/v1/apps/test-app", headers={"X-Authority-Key": body["authority_key"]})
    assert got.status_code == 200
    assert got.json()["status"] == "active"


def test_activate_writes_a_spec_confirm_change(client, app):
    client.post("/v1/apps", json=_spec())
    client.post("/v1/apps/test-app/activate")
    changes = app.state.db.list_changes("test-app", kind="spec_confirm")
    assert len(changes) == 1
    c = changes[0]
    assert c.actor == "dev"
    assert c.before == "draft"
    assert c.after == "active"


def test_activate_missing_app_is_404(client):
    assert client.post("/v1/apps/nope/activate").status_code == 404


def test_activate_twice_is_409(client):
    client.post("/v1/apps", json=_spec())
    client.post("/v1/apps/test-app/activate")
    r = client.post("/v1/apps/test-app/activate")
    assert r.status_code == 409


def test_activate_adds_app_to_the_engine(client, app):
    client.post("/v1/apps", json=_spec())
    client.post("/v1/apps/test-app/activate")
    assert "test-app" in app.state.engine.apps
