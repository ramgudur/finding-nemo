import pytest
from fake_llm import FakeAsyncOpenAI
from fastapi.testclient import TestClient

from app.agents.llm import LlmRouter
from app.api.compile_gate import CompileGate
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
    """Matches the recorded `compiler_campus_alert` fixture, which proposes specs/campus-alert.json:
    finalize_app pins the dev's inputs, intent and grid (#164), so they must be the same."""
    import json
    from pathlib import Path

    spec = json.loads((Path(__file__).parents[2] / "specs" / "campus-alert.json").read_text(encoding="utf-8"))
    return {
        "intent": spec["intent"],
        "grid": spec["grid"],
        "inputs": [{k: i[k] for k in ("id", "kind", "shape", "meaning", "fields") if k in i} for i in spec["inputs"]],
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
    detail = r.json()["detail"]
    assert set(detail) == {"reason", "errors", "warnings"}
    assert "without finalizing" in detail["reason"]
    assert detail["errors"] == [] and detail["warnings"] == []


def test_compile_returns_503_with_the_skipped_reason_when_rate_limited():
    app = _app_with_llm({"scenario": "s", "description": "d", "steps": [{"response": {"content": "unused"}}]})
    app.state.llm._provider("nvidia").bucket.tokens = 0.0
    with TestClient(app) as client:
        r = client.post("/v1/apps/some-app/compile", json=_compile_request())
    assert r.status_code == 503
    assert "rate budget" in r.json()["detail"]
    assert r.headers["Retry-After"] == "2"  # ceil(60 s / 35 RPM): one request's refill
    assert "2 s" in r.json()["detail"]


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


def _neutral_request() -> dict:
    """A small request for tests with their own synthetic proposal (not the recorded fixture)."""
    return {
        "intent": "find where people gather",
        "grid": {"cell_m": 10, "bbox": {"min_lat": 0.0, "min_lon": 0.0, "max_lat": 0.01, "max_lon": 0.01}},
        "inputs": [{"id": "pings", "kind": "push", "shape": "point", "meaning": "a ping means someone is here"}],
    }


def _pull_compile_request() -> dict:
    req = _neutral_request()
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
    req = _neutral_request()
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


# -- #167: compile progress and readable failures ------------------------------------------


def _bad_spec() -> dict:
    spec = _spec("some-app")
    spec["inputs"][0]["operators"] = [{"op": "no_such_op", "weight": 1.0, "layer_id": "pings.density"}]
    return spec


def test_compile_422_lists_the_last_proposed_specs_errors_and_warnings():
    script = {
        "scenario": "s",
        "description": "validates a spec with an unknown operator, then stops without finalizing",
        "steps": [
            {"response": {"tool_calls": [{"id": "c1", "name": "validate_spec", "arguments": {"spec": _bad_spec()}}]}},
            {"response": {"content": "giving up"}},
        ],
    }
    app = _app_with_llm(script)
    with TestClient(app) as client:
        r = client.post("/v1/apps/some-app/compile", json=_compile_request())
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert any("unknown operator 'no_such_op'" in e for e in detail["errors"]), detail
    assert any("plausibility" in w for w in detail["warnings"])  # flexible point input without it
    assert not any("pydantic.dev" in e or "input_value" in e for e in detail["errors"])  # plain language


def test_compile_422_reports_spec_shape_errors_in_plain_language():
    spec = _spec("some-app")
    spec["outputs"]["layers"].append({"id": "nowhere.layer"})
    script = {
        "scenario": "s",
        "description": "finalizes a spec whose outputs name a layer no input makes",
        "steps": [
            {"response": {"tool_calls": [{"id": "c1", "name": "finalize_app", "arguments": {"spec": spec}}]}},
            {"response": {"content": "done"}},
        ],
    }
    with TestClient(_app_with_llm(script)) as client:
        r = client.post("/v1/apps/some-app/compile", json=_compile_request())
    assert r.status_code == 422
    errors = r.json()["detail"]["errors"]
    assert any("nowhere.layer" in e for e in errors), errors


def test_compile_status_is_404_for_an_unknown_app(client):
    assert client.get("/v1/apps/nothing-here/compile/status").status_code == 404


def test_compile_status_reports_step_elapsed_and_last_validate_without_trace_content(app, client):
    from datetime import UTC, datetime

    from app.api.apps import _CompileRun
    from app.core import clock as clock_mod
    from app.core.models import ToolCall, TraceEntry

    t0 = clock_mod.now()
    client.get("/v1/apps/nothing-here/compile/status")  # creates app.state.compile_runs
    app.state.compile_runs["some-app"] = _CompileRun(started_at=t0)
    db = app.state.db
    at = datetime.fromtimestamp(t0 + 1, tz=UTC)
    secret = "SECRET-ARG-TEXT"
    bad = {**_bad_spec(), "intent": secret}
    db.add_trace(
        TraceEntry(
            t=at,
            app_id="some-app",
            agent="compiler",
            action="tool_calls",
            why=f"why {secret}",
            tool_calls=[ToolCall(name="validate_spec", args={"spec": bad}, ok=True, result_summary=secret)],
        )
    )
    db.add_trace(
        TraceEntry(
            t=at,
            app_id="some-app",
            agent="compiler",
            action="tool_calls",
            tool_calls=[ToolCall(name="made_up_tool", args={"x": secret}, result_summary=secret)],
        )
    )
    db.add_trace(TraceEntry(t=at, app_id="some-app", agent="compiler", action="suggest_source", why="suggest_source"))

    r = client.get("/v1/apps/some-app/compile/status")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"running", "step", "elapsed_s", "last_validate"}
    assert body["running"] is True
    assert body["step"] == 2  # the source suggestion isn't a compile step
    assert body["elapsed_s"] >= 0
    assert any("unknown operator 'no_such_op'" in e for e in body["last_validate"]["errors"])
    assert secret not in r.text and "made_up_tool" not in r.text

    # a proposal that doesn't even parse: plain "where: what" lines, the values never echoed
    db.add_trace(
        TraceEntry(
            t=at,
            app_id="some-app",
            agent="compiler",
            action="tool_calls",
            tool_calls=[ToolCall(name="finalize_app", args={"spec": {"intent": secret}}, ok=False)],
        )
    )
    r = client.get("/v1/apps/some-app/compile/status")
    assert r.json()["step"] == 3
    errors = r.json()["last_validate"]["errors"]
    assert errors and not any("pydantic.dev" in e for e in errors)
    assert secret not in r.text


def test_compile_status_after_a_compile_then_404_once_active():
    app = _app_with_llm("compiler_campus_alert")
    with TestClient(app) as client:
        assert client.post("/v1/apps/campus-alert/compile", json=_compile_request()).status_code == 200
        r = client.get("/v1/apps/campus-alert/compile/status")
        assert r.status_code == 200
        body = r.json()
        assert body["running"] is False and body["step"] >= 1 and body["elapsed_s"] == 0
        assert body["last_validate"]["errors"] == []  # the finalized spec passed the checks
        client.post("/v1/apps/campus-alert/activate")
        assert client.get("/v1/apps/campus-alert/compile/status").status_code == 404


def test_compile_status_for_a_pending_app_with_no_compile_in_this_process(client):
    client.post("/v1/apps", json=_spec("draft-app"))
    r = client.get("/v1/apps/draft-app/compile/status")
    assert r.status_code == 200
    assert r.json() == {"running": False, "step": 0, "elapsed_s": 0.0, "last_validate": None}


# -- #169: per-app compile cooldown ----------------------------------------------------------


class _ManualTime:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _app_with_gate(script, gate: CompileGate):
    return create_app(db=Db(":memory:"), llm=_router(script), compile_gate=gate, autostart_engine=False)


def test_compile_gap_defaults_to_ten_seconds_and_is_optional():
    assert Settings(_env_file=None).compile_min_gap_s == 10.0
    assert create_app(db=Db(":memory:"), autostart_engine=False).state.compile_gate.min_gap_s == 10.0


def test_a_second_compile_too_soon_is_a_429_with_retry_after():
    clock = _ManualTime()
    app = _app_with_gate("compiler_campus_alert", CompileGate(10.0, now=clock))
    with TestClient(app) as client:
        assert client.post("/v1/apps/campus-alert/compile", json=_compile_request()).status_code == 200
        clock.t += 3.0
        r = client.post("/v1/apps/campus-alert/compile", json=_compile_request())
        assert r.status_code == 429
        assert r.headers["Retry-After"] == "7"
        assert "apart" in r.json()["detail"]
        # another app is not affected
        assert client.post("/v1/apps/campus-alert-2/compile", json=_compile_request()).status_code == 200
        clock.t += 7.0
        app.state.llm = _router("compiler_campus_alert")
        assert client.post("/v1/apps/campus-alert/compile", json=_compile_request()).status_code == 200


def test_a_failed_compile_still_starts_the_gap():
    clock = _ManualTime()
    script = {"scenario": "s", "description": "d", "steps": [{"response": {"content": "no good spec here"}}]}
    app = _app_with_gate(script, CompileGate(10.0, now=clock))
    with TestClient(app) as client:
        assert client.post("/v1/apps/some-app/compile", json=_compile_request()).status_code == 422
        assert client.post("/v1/apps/some-app/compile", json=_compile_request()).status_code == 429


def test_a_compile_already_in_flight_is_a_429():
    gate = CompileGate(0.0)
    app = _app_with_gate({"scenario": "s", "description": "d", "steps": []}, gate)
    assert gate.try_acquire("some-app") is None  # another request is compiling this app
    with TestClient(app) as client:
        r = client.post("/v1/apps/some-app/compile", json=_compile_request())
        assert r.status_code == 429 and r.headers["Retry-After"] == "1"
        assert "already running" in r.json()["detail"]
        gate.release("some-app")
        assert gate.try_acquire("some-app") is None  # gap disabled: free again at once


def test_refusals_before_the_model_do_not_spend_the_cooldown():
    gate = CompileGate(10.0, now=_ManualTime())
    app = _app_with_gate("compiler_campus_alert", gate)
    with TestClient(app) as client:
        client.post("/v1/apps/campus-alert/compile", json=_compile_request())
        client.post("/v1/apps/campus-alert/activate")
        assert client.post("/v1/apps/campus-alert/compile", json=_compile_request()).status_code == 409
    assert gate.try_acquire("never-compiled") is None


def test_compile_gate_forgets_old_entries():
    clock = _ManualTime()
    gate = CompileGate(5.0, now=clock)
    for i in range(50):
        assert gate.try_acquire(f"app-{i}") is None
        gate.release(f"app-{i}")
    clock.t += 5.0
    assert gate.try_acquire("other") is None
    assert gate._finished_at == {}
