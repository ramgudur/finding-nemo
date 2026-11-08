import pytest
from fake_llm import FakeAsyncOpenAI
from fastapi.testclient import TestClient

from app.agents.llm import LlmRouter
from app.core.config import Settings
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


# -- POST /{app}/compile ---------------------------------------------------------------


def _compile_request() -> dict:
    return {
        "intent": "find where people gather",
        "grid": {"cell_m": 10, "bbox": {"min_lat": 0.0, "min_lon": 0.0, "max_lat": 0.01, "max_lon": 0.01}},
        "inputs": [{"id": "pings", "kind": "push", "shape": "point", "meaning": "a ping means someone is here"}],
    }


def _router(script) -> LlmRouter:
    fake = FakeAsyncOpenAI(script)
    return LlmRouter(Settings(_env_file=None), clients={"nvidia": fake, "openrouter": fake})


def _app_with_llm(script):
    return create_app(db=Db(":memory:"), llm=_router(script), autostart_engine=False)


def test_compile_returns_the_proposed_spec_pending_confirmation():
    app = _app_with_llm("compiler_campus_alert")
    with TestClient(app) as client:
        r = client.post("/v1/apps/campus-alert/compile", json=_compile_request())
    assert r.status_code == 200
    body = r.json()
    assert body["app_id"] == "campus-alert" and body["status"] == "pending_confirmation"
    assert len(body["inputs"]) == 4  # the fixture's spec, not the 1-input request body


def test_compile_rejects_an_already_active_app():
    app = _app_with_llm("compiler_campus_alert")
    with TestClient(app) as client:
        client.post("/v1/apps/campus-alert/compile", json=_compile_request())
        client.post("/v1/apps/campus-alert/activate")
        r = client.post("/v1/apps/campus-alert/compile", json=_compile_request())
    assert r.status_code == 409


def test_compile_returns_422_with_the_last_trace_reason_when_it_never_finalizes():
    script = {"scenario": "s", "description": "d", "steps": [{"response": {"content": "no good spec here"}}]}
    app = _app_with_llm(script)
    with TestClient(app) as client:
        r = client.post("/v1/apps/some-app/compile", json=_compile_request())
    assert r.status_code == 422
    assert r.json()["detail"] == "final answer"


def test_compile_returns_503_with_the_skipped_reason_when_rate_limited():
    app = _app_with_llm({"scenario": "s", "description": "d", "steps": [{"response": {"content": "unused"}}]})
    app.state.llm._provider("nvidia").bucket.tokens = 0.0
    with TestClient(app) as client:
        r = client.post("/v1/apps/some-app/compile", json=_compile_request())
    assert r.status_code == 503
    assert "rate budget" in r.json()["detail"]


def test_compile_second_call_with_same_intent_and_inputs_is_a_cache_hit():
    app = _app_with_llm("compiler_campus_alert")
    with TestClient(app) as client:
        first = client.post("/v1/apps/campus-alert/compile", json=_compile_request())
        assert first.status_code == 200

        router2 = _router("compiler_campus_alert")
        app.state.llm = router2  # a fresh router: if this one gets called, it's not a cache hit
        second = client.post("/v1/apps/campus-alert-2/compile", json=_compile_request())
    assert second.status_code == 200
    assert second.json()["app_id"] == "campus-alert-2"
    assert router2._provider("nvidia").client.calls == []


def test_compile_copies_the_devs_destination_points():
    points = [{"lat": 0.002, "lon": 0.003, "label": "Library"}]
    app = _app_with_llm("compiler_campus_alert")
    with TestClient(app) as client:
        r = client.post("/v1/apps/campus-alert/compile", json={**_compile_request(), "destination_points": points})
    assert r.status_code == 200, r.text
    assert r.json()["routing"]["destination_points"] == points


# -- #152: a pull input's connector, chosen by the dev, rides along with compile ---------

FEED = {"type": "rss", "url": "https://feeds.example.org/area.xml", "poll_s": 300, "config": {}, "preset": None}


def _pull_compile_request() -> dict:
    req = _compile_request()
    req["inputs"].append(
        {
            "id": "feed",
            "kind": "pull",
            "shape": "document",
            "meaning": "public reports about the area",
            "connector": FEED,
        }
    )
    return req


def _finalize_script() -> dict:
    """The model proposes the feed input with a different URL; finalize_app must keep the dev's."""
    req = _compile_request()
    spec = {
        "app_id": "some-app",
        "intent": req["intent"],
        "grid": req["grid"],
        "inputs": [
            {**req["inputs"][0], "tier": "flexible", "operators": [{"op": "density", "weight": 1.0}]},
            {
                "id": "feed",
                "kind": "pull",
                "shape": "document",
                "tier": "stiff",
                "meaning": "public reports about the area",
                "connector": {"type": "rss", "url": "https://elsewhere.example.net/feed"},
                "operators": [{"op": "event_points", "weight": 0.3}],
            },
        ],
    }
    return {
        "scenario": "s",
        "description": "d",
        "steps": [
            {"response": {"tool_calls": [{"name": "finalize_app", "arguments": {"spec": spec}}]}},
            {"response": {"content": "Proposed."}},
        ],
    }


def test_compile_copies_the_devs_connector_verbatim(monkeypatch):
    from app.connectors import url_guard

    monkeypatch.setattr(url_guard, "check_url", lambda url, **kw: url)
    app = _app_with_llm(_finalize_script())
    with TestClient(app) as client:
        r = client.post("/v1/apps/some-app/compile", json=_pull_compile_request())
    assert r.status_code == 200, r.text
    feed = next(i for i in r.json()["inputs"] if i["id"] == "feed")
    assert feed["connector"] == FEED
    assert feed["meaning"] == "public reports about the area"


def test_compile_rejects_an_unsafe_connector_url_before_any_model_call(monkeypatch):
    from app.connectors import url_guard

    def refuse(url, **kw):
        raise url_guard.UnsafeUrl("that host is not allowed")

    monkeypatch.setattr(url_guard, "check_url", refuse)
    app = _app_with_llm({"scenario": "s", "description": "d", "steps": []})
    with TestClient(app) as client:
        r = client.post("/v1/apps/some-app/compile", json=_pull_compile_request())
    assert r.status_code == 422 and "connector URL refused" in r.text


def test_compile_rejects_a_connector_on_a_push_input():
    req = _compile_request()
    req["inputs"][0]["connector"] = FEED
    app = _app_with_llm({"scenario": "s", "description": "d", "steps": []})
    with TestClient(app) as client:
        r = client.post("/v1/apps/some-app/compile", json=req)
    assert r.status_code == 422
