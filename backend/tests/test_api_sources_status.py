"""`GET /v1/apps/{app}/sources/status` and the activation backfill poll (#267). No network."""

import asyncio
import threading
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

from app.connectors import engine as conn_engine
from app.core import clock as clock_mod
from app.core.db import Db
from app.main import create_app

BBOX = {"min_lat": 0.0, "min_lon": 0.0, "max_lat": 0.01, "max_lon": 0.01}
NOW = 1_800_000_000.0
LIST_CONFIG = {"items_path": "items", "lat": "lat", "lon": "lon", "fields": {"level": "level"}}


def _spec() -> dict:
    return {
        "app_id": "status-app",
        "intent": "show where items are reported",
        "grid": {"cell_m": 10, "bbox": BBOX},
        "inputs": [
            {
                "id": "pings",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "a ping is one report",
                "operators": [{"op": "density", "weight": 1.0}],
            },
            {
                "id": "hooked",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "reports another service sends",
                "operators": [{"op": "density", "weight": 1.0}],
                "webhook": {"lat": "lat", "lon": "lon"},
            },
            {
                "id": "listed",
                "kind": "pull",
                "shape": "point",
                "tier": "stiff",
                "meaning": "a public list of located items",
                "operators": [{"op": "density", "weight": 1.0}],
                "connector": {
                    "type": "json_list",
                    "url": "https://data.example.org/items.json",
                    "poll_s": 120,
                    "config": LIST_CONFIG,
                },
            },
        ],
    }


class _RecordingConnectors:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def poll_app_now(self, app_id: str) -> bool:
        self.calls.append(app_id)
        return True

    def start(self) -> None:  # pragma: no cover - autostart is off in these tests
        pass

    async def stop(self) -> None:  # pragma: no cover
        pass


@pytest.fixture
def frozen_clock(monkeypatch):
    monkeypatch.setattr(clock_mod, "now", lambda: NOW)


@pytest.fixture
def setup(frozen_clock):
    connectors = _RecordingConnectors()
    app = create_app(db=Db(":memory:"), connector_engine=connectors, autostart_engine=False)
    with TestClient(app) as c:
        assert c.post("/v1/apps", json=_spec()).status_code == 201
        keys = c.post("/v1/apps/status-app/activate").json()
        yield c, app.state.db, keys, connectors


def test_status_needs_the_authority_key(setup):
    c, _, keys, _ = setup
    assert c.get("/v1/apps/status-app/sources/status").status_code == 401
    assert c.get("/v1/apps/status-app/sources/status", headers={"X-Authority-Key": "nope"}).status_code == 401
    assert (
        c.get("/v1/apps/status-app/sources/status", headers={"X-Authority-Key": keys["ingest_key"]}).status_code == 401
    )
    assert c.get("/v1/apps/missing/sources/status", headers={"X-Authority-Key": "x"}).status_code == 404


def test_status_reports_via_polls_and_event_counts(setup):
    c, db, keys, _ = setup
    for ts in (NOW - 7200, NOW - 60, NOW - 30):
        r = c.post(
            "/v1/apps/status-app/inputs/pings/events",
            headers={"X-Ingest-Key": keys["ingest_key"]},
            json={"user_id": "u1", "lat": 0.005, "lon": 0.005, "ts": datetime.fromtimestamp(ts, UTC).isoformat()},
        )
        assert r.status_code == 202, r.text
    db.upsert_connector("status-app", "listed", db.get_input("status-app", "listed").connector)
    db.record_connector_poll("status-app", "listed", polled_at=NOW - 20, status="error: HTTP 503")

    r = c.get("/v1/apps/status-app/sources/status", headers={"X-Authority-Key": keys["authority_key"]})
    assert r.status_code == 200, r.text
    rows = {s["input_id"]: s for s in r.json()}
    assert set(rows) == {"pings", "hooked", "listed"}
    assert set(rows["pings"]) == {
        "input_id",
        "kind",
        "via",
        "poll_s",
        "last_polled_at",
        "next_poll_at",
        "last_status",
        "events_total",
        "events_last_hour",
        "last_event_at",
    }

    pings = rows["pings"]
    assert (pings["kind"], pings["via"], pings["poll_s"], pings["last_polled_at"]) == ("push", "post_api", None, None)
    assert (pings["events_total"], pings["events_last_hour"]) == (3, 2)
    assert datetime.fromisoformat(pings["last_event_at"]).timestamp() == NOW - 30

    assert rows["hooked"]["via"] == "webhook" and rows["hooked"]["events_total"] == 0
    assert rows["hooked"]["last_event_at"] is None

    listed = rows["listed"]
    assert (listed["kind"], listed["via"], listed["poll_s"]) == ("pull", "json_list", 120)
    assert datetime.fromisoformat(listed["last_polled_at"]).timestamp() == NOW - 20
    assert datetime.fromisoformat(listed["next_poll_at"]).timestamp() == NOW - 20 + 120
    assert listed["last_status"] == "error: HTTP 503"
    assert "example.org" not in r.text  # no connector URL in the status view


def test_event_counts_query():
    db = Db(":memory:")
    assert db.event_counts("a", "i", since=0.0) == (0, 0, None)


def test_activation_schedules_one_backfill_poll(setup):
    _, _, _, connectors = setup
    assert connectors.calls == ["status-app"]


def test_activation_without_pull_inputs_schedules_nothing(frozen_clock):
    connectors = _RecordingConnectors()
    spec = _spec()
    spec["inputs"] = spec["inputs"][:1]
    app = create_app(db=Db(":memory:"), connector_engine=connectors, autostart_engine=False)
    with TestClient(app) as c:
        c.post("/v1/apps", json=spec)
        assert c.post("/v1/apps/status-app/activate").status_code == 200
    assert connectors.calls == []


def test_activation_succeeds_even_if_scheduling_the_backfill_fails(frozen_clock):
    class Broken(_RecordingConnectors):
        def poll_app_now(self, app_id: str) -> bool:
            raise RuntimeError("boom")

    app = create_app(db=Db(":memory:"), connector_engine=Broken(), autostart_engine=False)
    with TestClient(app) as c:
        c.post("/v1/apps", json=_spec())
        assert c.post("/v1/apps/status-app/activate").status_code == 200


def test_poll_app_now_wakes_a_running_engine_from_another_thread(monkeypatch):
    """The real engine: started with a long check interval, a backfill request from a worker
    thread (as the sync activate handler is) runs the poll at once and records its status."""
    from app.connectors import url_guard

    monkeypatch.setattr(url_guard, "check_url", lambda url, **kw: url)
    db = Db(":memory:")
    from app.core.models import AppSpec

    spec = AppSpec.model_validate({**_spec(), "status": "pending_confirmation"})
    db.upsert_app(spec, t=0.0)
    fetched = threading.Event()

    def handler(request):
        fetched.set()
        return httpx.Response(200, json={"items": [{"lat": 0.005, "lon": 0.005, "level": 1}]})

    async def go():
        ce = conn_engine.ConnectorEngine(
            db, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), check_interval_s=60
        )
        ce.start()
        await asyncio.sleep(0.05)  # first check: nothing active yet
        assert not fetched.is_set()
        db.upsert_app(spec.model_copy(update={"status": "active"}), t=1.0)
        woke = await asyncio.to_thread(ce.poll_app_now, "status-app")
        for _ in range(100):
            if db.get_connector_status("status-app", "listed") is not None:
                break
            await asyncio.sleep(0.02)
        await ce.stop()
        return woke

    assert asyncio.run(go()) is True
    assert fetched.is_set()
    polled_at, status = db.get_connector_status("status-app", "listed")
    assert status == "ok"
    assert len(db.list_events("status-app", "listed")) == 1


def test_poll_app_now_without_a_running_loop_just_marks_the_app_due():
    ce = conn_engine.ConnectorEngine(Db(":memory:"), client=httpx.AsyncClient())
    ce._last_polled_at[("a", "x")] = 5.0
    assert ce.poll_app_now("a") is False
    assert ("a", "x") not in ce._last_polled_at


def test_put_spec_refuses_a_bad_json_list_mapping(frozen_clock):
    app = create_app(db=Db(":memory:"), connector_engine=_RecordingConnectors(), autostart_engine=False)
    spec = {**_spec(), "status": "pending_confirmation"}
    with TestClient(app) as c:
        assert c.post("/v1/apps", json=spec).status_code == 201
        bad = _spec()
        bad["inputs"][2]["connector"]["config"] = {"lat": "lat", "lon": "lon", "script": "x"}
        r = c.put("/v1/apps/status-app/spec", json=bad)
        assert r.status_code == 422, r.text
        assert "connector config refused" in r.text
        good = c.put("/v1/apps/status-app/spec", json=_spec())
        assert good.status_code == 200, good.text
        fields = {f["name"] for f in good.json()["spec"]["inputs"][2]["fields"]}
        assert "level" in fields  # the mapping's field names are declared on the input (#259 rule)
