"""`POST /v1/apps/{app}/scout` (#183): dev-requested Scout runs. No network, scripted fake LLM."""

import asyncio

import pytest
from fake_llm import FakeAsyncOpenAI
from fastapi.testclient import TestClient

from app.agents import scout as scout_mod
from app.agents.llm import LlmRouter
from app.agents.scout import pending_requests, request_instructions
from app.core import clock as clock_mod
from app.core.clock import SimClock
from app.core.config import Settings
from app.core.db import Db
from app.engine.tick import InMemoryEventSource, TickEngine
from app.main import create_app

T0 = 1_800_000_000.0
BBOX = {"min_lat": 32.77, "min_lon": -96.77, "max_lat": 32.79, "max_lon": -96.75}
APP = "scout-api-app"
REQUEST = "look for a public feed of reports covering this area"


def _spec(app_id: str = APP) -> dict:
    return {
        "app_id": app_id,
        "intent": "map where items are reported",
        "status": "pending_confirmation",
        "grid": {"cell_m": 25, "bbox": BBOX},
        "inputs": [
            {
                "id": "reports",
                "kind": "pull",
                "shape": "document",
                "tier": "stiff",
                "meaning": "public reports of items in the area",
                "operators": [{"op": "event_points", "weight": 0.3}],
            },
            {
                "id": "pings",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "a report from our own app",
                "operators": [{"op": "density", "weight": 1.0}],
            },
        ],
    }


def _router() -> LlmRouter:
    script = {"scenario": "s", "description": "d", "steps": [{"response": {"content": "No source found."}}]}
    fake = FakeAsyncOpenAI(script)
    return LlmRouter(Settings(_env_file=None), clients={"nvidia": fake, "openrouter": fake})


@pytest.fixture()
def p():
    clock = SimClock(start=T0)
    prev = clock_mod.set_clock(clock)
    try:
        db = Db(":memory:")
        engine = TickEngine(InMemoryEventSource(), clock=clock)
        app = create_app(db=db, engine=engine, llm=_router(), autostart_engine=False)
        c = TestClient(app)
        assert c.post("/v1/apps", json=_spec()).status_code == 201
        keys = c.post(f"/v1/apps/{APP}/activate").json()
        yield {"c": c, "db": db, "app": app, "auth": {"X-Authority-Key": keys["authority_key"]}, "clock": clock}
    finally:
        clock_mod.set_clock(prev)


def _post(p, body=None, headers=None, app_id=APP):
    body = body if body is not None else {"input_id": "reports", "request": REQUEST}
    return p["c"].post(f"/v1/apps/{app_id}/scout", json=body, headers=p["auth"] if headers is None else headers)


def test_accepted_with_job_id_and_enqueued(p):
    r = _post(p)
    assert r.status_code == 202
    body = r.json()
    assert set(body) == {"job_id", "app_id", "input_id"}
    assert body["app_id"] == APP and body["input_id"] == "reports" and body["job_id"]
    assert REQUEST not in r.text  # never echoed back
    (req,) = pending_requests(p["db"], APP)
    assert req.agent == "scout" and req.action == "requested" and req.why == "dev request"
    assert req.saw == "reports" and request_instructions(req) == REQUEST
    assert body["job_id"] in req.result


def test_runner_picks_up_the_dev_request(p, monkeypatch):
    assert _post(p).status_code == 202
    calls = []

    async def fake_run(ctx, input_id, instructions="", llm=None):
        calls.append((ctx.app_id, input_id, instructions))
        await original(ctx, input_id, instructions, llm)

    original = scout_mod.run_scout
    monkeypatch.setattr(scout_mod, "run_scout", fake_run)
    runner = p["app"].state.scout_runner
    p["clock"].advance(1)
    asyncio.run(runner.check_once())
    assert calls == [(APP, "reports", REQUEST)]
    assert pending_requests(p["db"], APP) == []
    assert any(e.agent == "scout" and e.action == "request_handled" for e in p["db"].list_trace(APP))


def test_rate_budget_empty_is_a_skipped_trace_not_a_blocked_request(p):
    router = p["app"].state.scout_runner.llm
    for prov in ("nvidia", "openrouter"):
        router._provider(prov).bucket.tokens = 0.0
    assert _post(p).status_code == 202
    p["clock"].advance(1)
    asyncio.run(p["app"].state.scout_runner.check_once())
    trace = p["db"].list_trace(APP)
    assert any(e.agent == "scout" and e.skipped_reason == "rate budget" for e in trace)


def test_missing_or_wrong_key_is_401(p):
    assert _post(p, headers={}).status_code == 401
    assert _post(p, headers={"X-Authority-Key": "wrong"}).status_code == 401
    assert pending_requests(p["db"], APP) == []


def test_unknown_app_or_input_is_404(p):
    assert _post(p, app_id="no-such-app").status_code == 404
    assert _post(p, body={"input_id": "nope", "request": REQUEST}).status_code == 404


def test_push_input_is_422(p):
    r = _post(p, body={"input_id": "pings", "request": REQUEST})
    assert r.status_code == 422
    assert pending_requests(p["db"], APP) == []


def test_request_length_bounds_are_422(p):
    assert _post(p, body={"input_id": "reports", "request": "x" * 501}).status_code == 422
    assert _post(p, body={"input_id": "reports", "request": ""}).status_code == 422
    assert _post(p, body={"input_id": "reports", "request": "x" * 500}).status_code == 202


def test_not_active_is_409(p):
    other = "scout-api-pending"
    assert p["c"].post("/v1/apps", json=_spec(other)).status_code == 201
    keys = p["db"].create_keys(other, t=T0)  # a key but a spec still awaiting confirmation
    r = _post(p, app_id=other, headers={"X-Authority-Key": keys.authority_key})
    assert r.status_code == 409
    # active in the DB but not loaded in the engine: the runner would never see it
    p["app"].state.engine.remove_app(APP)
    assert _post(p).status_code == 409
