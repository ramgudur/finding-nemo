"""`PUT /v1/apps/{app}/spec`: the dev edits a pending spec before confirming it (#166)."""

import copy

import pytest
from fastapi.testclient import TestClient

from app.core.db import Db
from app.main import create_app


def _spec(app_id: str = "edit-app") -> dict:
    return {
        "app_id": app_id,
        "intent": "show where entities gather",
        "status": "pending_confirmation",
        "grid": {"cell_m": 10, "bbox": {"min_lat": 0.0, "min_lon": 0.0, "max_lat": 0.01, "max_lon": 0.01}},
        "inputs": [
            {
                "id": "pings",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "a ping means an entity is here",
                "operators": [{"op": "density", "weight": 1.0}],
                "plausibility": {"max_speed_mps": 5.0},
            }
        ],
        "outputs": {"layers": [{"id": "score"}, {"id": "pings.density"}]},
    }


@pytest.fixture
def db():
    return Db(":memory:")


@pytest.fixture
def client(db):
    with TestClient(create_app(db=db, autostart_engine=False)) as c:
        yield c


def _pending(client) -> dict:
    spec = _spec()
    assert client.post("/v1/apps", json=spec).status_code == 201
    return spec


def test_edit_pending_spec_saves_and_keeps_status(client, db):
    spec = _pending(client)
    edited = copy.deepcopy(spec)
    edited["inputs"][0]["tier"] = "stiff"
    edited["inputs"][0]["operators"][0]["weight"] = 0.5
    edited["outputs"]["poi_labels"] = {"mode": "on", "label_set": [{"label": "gathering", "destination": True}]}
    r = client.put("/v1/apps/edit-app/spec", json=edited)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["spec"]["status"] == "pending_confirmation"
    assert body["spec"]["inputs"][0]["tier"] == "stiff"
    assert isinstance(body["warnings"], list)
    saved = db.get_app("edit-app")
    assert saved.status == "pending_confirmation"
    assert saved.inputs[0].tier == "stiff"
    assert saved.inputs[0].operators[0].weight == 0.5


def test_edit_returns_warnings_with_the_saved_spec(client):
    spec = _pending(client)
    edited = copy.deepcopy(spec)
    edited["inputs"][0]["plausibility"] = None  # flexible point input without plausibility -> warning
    r = client.put("/v1/apps/edit-app/spec", json=edited)
    assert r.status_code == 200
    assert any("plausibility" in w for w in r.json()["warnings"])


def test_edit_writes_a_spec_edit_change_with_only_changed_fields(client, db):
    spec = _pending(client)
    edited = copy.deepcopy(spec)
    edited["inputs"][0]["tier"] = "stiff"
    edited["routing"] = {"enabled": True, "destination_points": [{"lat": 0.005, "lon": 0.005, "label": "point a"}]}
    assert client.put("/v1/apps/edit-app/spec", json=edited).status_code == 200
    changes = db.list_changes("edit-app", kind="spec_edit")
    assert len(changes) == 1
    c = changes[0]
    assert c.actor == "dev" and c.target == "edit-app"
    assert set(c.before) == set(c.after) == {"inputs[pings].tier", "routing"}
    assert c.before["inputs[pings].tier"] == "flexible"
    assert c.after["inputs[pings].tier"] == "stiff"
    assert c.before["routing"]["enabled"] is False
    assert c.after["routing"]["destination_points"][0]["label"] == "point a"


def test_edit_with_no_changes_writes_no_change(client, db):
    spec = _pending(client)
    assert client.put("/v1/apps/edit-app/spec", json=spec).status_code == 200
    assert db.list_changes("edit-app", kind="spec_edit") == []


def test_edit_active_app_is_409(client):
    spec = _pending(client)
    assert client.post("/v1/apps/edit-app/activate").status_code == 200
    assert client.put("/v1/apps/edit-app/spec", json=spec).status_code == 409


def test_edit_draft_app_is_409(client):
    spec = {**_spec(), "status": "draft"}
    client.post("/v1/apps", json=spec)
    assert client.put("/v1/apps/edit-app/spec", json=spec).status_code == 409


def test_edit_missing_app_is_404(client):
    assert client.put("/v1/apps/nope/spec", json=_spec("nope")).status_code == 404


def test_edit_app_id_mismatch_is_422(client):
    _pending(client)
    assert client.put("/v1/apps/edit-app/spec", json=_spec("other-app")).status_code == 422


def test_invalid_spec_is_422_with_every_error(client, db):
    spec = _pending(client)
    edited = copy.deepcopy(spec)
    edited["inputs"][0]["operators"].append({"op": "no_such_operator", "weight": 0.2})
    edited["outputs"]["layers"] = [{"id": "score", "visibility": "public"}, {"id": "pings.density"}]
    edited["routing"] = {"enabled": True}
    r = client.put("/v1/apps/edit-app/spec", json=edited)
    assert r.status_code == 422
    errors = r.json()["detail"]
    assert isinstance(errors, list) and len(errors) == 3
    assert any("unknown operator" in e for e in errors)
    assert any("routing is enabled" in e for e in errors)
    assert any("'score' is public" in e for e in errors)
    assert db.get_app("edit-app").inputs[0].operators[0].op == "density"  # nothing saved
    assert db.list_changes("edit-app", kind="spec_edit") == []


def test_schema_invalid_spec_is_422_with_messages(client):
    spec = _pending(client)
    edited = copy.deepcopy(spec)
    edited["inputs"][0]["tier"] = "unbounded"
    r = client.put("/v1/apps/edit-app/spec", json=edited)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail and all(isinstance(e, str) for e in detail)
    assert any("tier" in e for e in detail)


def test_edit_refuses_an_unsafe_new_connector_url(client, monkeypatch):
    from app.connectors import url_guard

    def refuse(url, **kw):
        raise url_guard.UnsafeUrl("that host is not allowed")

    monkeypatch.setattr(url_guard, "check_url", refuse)
    spec = _pending(client)
    edited = copy.deepcopy(spec)
    edited["inputs"].append(
        {
            "id": "docs",
            "kind": "pull",
            "shape": "document",
            "tier": "stiff",
            "meaning": "documents that mention the area",
            "connector": {"type": "rss", "url": "http://feed.invalid/rss"},
            "operators": [{"op": "event_points", "weight": 0.3}],
        }
    )
    r = client.put("/v1/apps/edit-app/spec", json=edited)
    assert r.status_code == 422
    assert any("connector URL refused" in e for e in r.json()["detail"])


def test_edit_cannot_activate_the_spec(client, db):
    spec = _pending(client)
    r = client.put("/v1/apps/edit-app/spec", json={**spec, "status": "active"})
    assert r.status_code == 422
    assert db.get_app("edit-app").status == "pending_confirmation"


def test_activate_after_edit_activates_the_edited_spec(client, db):
    spec = _pending(client)
    edited = copy.deepcopy(spec)
    edited["inputs"][0]["tier"] = "stiff"
    edited["inputs"][0]["visibility"] = "public"
    assert client.put("/v1/apps/edit-app/spec", json=edited).status_code == 200
    keys = client.post("/v1/apps/edit-app/activate").json()
    got = client.get("/v1/apps/edit-app", headers={"X-Authority-Key": keys["authority_key"]}).json()
    assert got["status"] == "active"
    assert got["inputs"][0]["tier"] == "stiff"
    assert got["inputs"][0]["visibility"] == "public"
    assert [c.kind for c in db.list_changes("edit-app")] == ["spec_edit", "spec_confirm"]


def test_edit_declares_a_new_connectors_implied_fields(client, monkeypatch):
    """#259: the fields a connector's events carry are declared; the dev's own are kept as written."""
    from app.connectors import url_guard

    monkeypatch.setattr(url_guard, "check_url", lambda url, **kw: url)
    spec = _pending(client)
    edited = copy.deepcopy(spec)
    edited["inputs"].append(
        {
            "id": "docs",
            "kind": "pull",
            "shape": "document",
            "tier": "stiff",
            "meaning": "documents that mention the area",
            "fields": [{"name": "confidence", "type": "number", "required": True}],
            "connector": {"type": "rss", "url": "https://feed.example.org/rss"},
            "operators": [{"op": "event_points", "weight": 0.3}],
        }
    )
    r = client.put("/v1/apps/edit-app/spec", json=edited)
    assert r.status_code == 200, r.text
    docs = next(i for i in r.json()["spec"]["inputs"] if i["id"] == "docs")
    assert [(f["name"], f["required"]) for f in docs["fields"]] == [("confidence", True), ("doc_id", False)]


def test_edit_refuses_a_bad_custom_json_config(client, monkeypatch):
    from app.connectors import url_guard

    monkeypatch.setattr(url_guard, "check_url", lambda url, **kw: url)
    spec = _pending(client)
    edited = copy.deepcopy(spec)
    edited["inputs"].append(
        {
            "id": "site",
            "kind": "pull",
            "shape": "field",
            "tier": "stiff",
            "meaning": "readings",
            "connector": {"type": "json_api", "url": "https://a.example.org", "config": {"fields": {"a": 5}}},
            "operators": [{"op": "field", "weight": 0.0}],
        }
    )
    r = client.put("/v1/apps/edit-app/spec", json=edited)
    assert r.status_code == 422
    assert any("connector config refused" in e for e in r.json()["detail"])
