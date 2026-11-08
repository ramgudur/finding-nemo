import asyncio

import httpx
import pytest

from app.connectors import engine
from app.core.db import Db
from app.core.models import AppSpec, ConnectorConfig, InputSpec


@pytest.fixture(autouse=True)
def _permissive_url_guard(monkeypatch):
    """These tests cover connector behaviour against a local/mock feed; the SSRF guard has its own
    tests (test_url_guard.py)."""
    from app.connectors import url_guard

    monkeypatch.setattr(url_guard, "check_url", lambda url, **kw: url)


RSS_FEED = (
    '<rss version="2.0" xmlns:georss="http://www.georss.org/georss"><channel>'
    "<item><title>Alert</title><description>Something.</description>"
    '<guid isPermaLink="false">doc-1</guid>'
    "<pubDate>Sun, 08 Nov 2026 05:00:00 GMT</pubDate>"
    "<georss:point>32.825 -96.7225</georss:point></item>"
    "</channel></rss>"
)


def _db_with_app(app_id: str, inputs: list[InputSpec]) -> Db:
    db = Db(":memory:")
    spec = AppSpec.model_validate(
        {
            "app_id": app_id,
            "intent": "test",
            "status": "active",
            "grid": {"cell_m": 10, "bbox": {"min_lat": 0.0, "min_lon": 0.0, "max_lat": 0.01, "max_lon": 0.01}},
        }
    )
    spec = spec.model_copy(update={"inputs": inputs})
    db.upsert_app(spec, t=0.0)
    return db


def _rss_input(poll_s: float = 30) -> InputSpec:
    return InputSpec.model_validate(
        {
            "id": "local_news",
            "kind": "pull",
            "shape": "document",
            "tier": "stiff",
            "meaning": "news",
            "fields": [
                {"name": "doc_id", "type": "string", "required": False},
                {"name": "confidence", "type": "number", "required": False},
            ],
            "connector": {"type": "rss", "url": "http://localhost:8765/feed.xml", "poll_s": poll_s},
        }
    )


def _wind_input(poll_s: float = 900) -> InputSpec:
    return InputSpec.model_validate(
        {
            "id": "wind",
            "kind": "pull",
            "shape": "field",
            "tier": "stiff",
            "meaning": "wind",
            "fields": [
                {"name": "wind_speed_10m", "type": "number"},
                {"name": "wind_direction_10m", "type": "number"},
            ],
            "connector": {
                "type": "json_api",
                "preset": "open_meteo",
                "poll_s": poll_s,
                "config": {"latitude": 32.8, "longitude": -96.7},
            },
        }
    )


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# -- events_from_raw / write_events ----------------------------------------------------


def test_rss_events_from_raw_carries_doc_id_and_confidence():
    conn = ConnectorConfig(type="rss", url="http://x", config={"source_id": "feed-1", "default_confidence": 0.7})
    events = engine.events_from_raw(conn, RSS_FEED, now=0.0)
    assert len(events) == 1
    e = events[0]
    assert e["user_id"] == "feed-1" and e["confidence"] == 0.7 and e["doc_id"] == "doc-1"
    assert e["lat"] == 32.825 and e["lon"] == -96.7225


def test_json_api_events_from_raw_stamps_the_fetch_time_not_the_response():
    conn = ConnectorConfig(type="json_api", preset="open_meteo", config={"latitude": 1.0, "longitude": 2.0})
    raw = {"current": {"time": "2020-01-01T00:00", "wind_speed_10m": 5.0, "wind_direction_10m": 90}}
    events = engine.events_from_raw(conn, raw, now=1_800_000_000.0)
    assert len(events) == 1
    assert events[0]["ts"].timestamp() == 1_800_000_000.0
    assert events[0]["wind_speed_10m"] == 5.0


def test_write_events_rejects_an_undeclared_field():
    db = Db(":memory:")
    inp = InputSpec.model_validate(
        {"id": "x", "kind": "pull", "shape": "document", "tier": "stiff", "meaning": "m"}
    )  # no declared fields
    raw = [{"lat": 1.0, "lon": 2.0, "ts": "2026-01-01T00:00:00Z", "doc_id": "d"}]
    result = engine.write_events(db, "app", inp, raw, now=0.0)
    assert result.ok is False
    assert db.list_events("app", "x") == []


def test_write_events_writes_valid_events_and_skips_invalid_ones():
    db = Db(":memory:")
    inp = InputSpec.model_validate(
        {
            "id": "x",
            "kind": "pull",
            "shape": "document",
            "tier": "stiff",
            "meaning": "m",
            "fields": [{"name": "doc_id", "type": "string", "required": False}],
        }
    )
    raw = [
        {"lat": 1.0, "lon": 2.0, "ts": "2026-01-01T00:00:00Z", "doc_id": "ok"},
        {"lat": 999.0, "lon": 2.0, "ts": "2026-01-01T00:00:00Z", "doc_id": "bad-lat"},  # invalid Event
    ]
    result = engine.write_events(db, "app", inp, raw, now=0.0)
    assert result.ok is True and result.events_written == 1
    assert len(db.list_events("app", "x")) == 1


def test_write_events_dedupes_by_user_and_doc_id_against_the_store_and_within_a_batch():
    db = Db(":memory:")
    inp = InputSpec.model_validate(
        {
            "id": "x",
            "kind": "pull",
            "shape": "document",
            "tier": "stiff",
            "meaning": "m",
            "fields": [{"name": "doc_id", "type": "string", "required": False}],
        }
    )
    first = [{"user_id": "feed-1", "lat": 1.0, "lon": 2.0, "ts": "2026-01-01T00:00:00Z", "doc_id": "d1"}]
    engine.write_events(db, "app", inp, first, now=0.0)
    assert len(db.list_events("app", "x")) == 1

    # a second poll re-reports d1 (unchanged feed) alongside a genuinely new d2, and the
    # same batch repeats d2 (e.g. a feed listing the same item twice)
    second = [
        {"user_id": "feed-1", "lat": 1.0, "lon": 2.0, "ts": "2026-01-01T00:01:00Z", "doc_id": "d1"},
        {"user_id": "feed-1", "lat": 1.1, "lon": 2.1, "ts": "2026-01-01T00:01:00Z", "doc_id": "d2"},
        {"user_id": "feed-1", "lat": 1.1, "lon": 2.1, "ts": "2026-01-01T00:01:00Z", "doc_id": "d2"},
    ]
    result = engine.write_events(db, "app", inp, second, now=1.0)
    assert result.events_written == 1  # only d2, once
    assert {e.model_extra["doc_id"] for e in db.list_events("app", "x")} == {"d1", "d2"}


def test_write_events_dedup_is_scoped_per_user_id():
    db = Db(":memory:")
    inp = InputSpec.model_validate(
        {
            "id": "x",
            "kind": "pull",
            "shape": "document",
            "tier": "stiff",
            "meaning": "m",
            "fields": [{"name": "doc_id", "type": "string", "required": False}],
        }
    )
    engine.write_events(
        db,
        "app",
        inp,
        [{"user_id": "feed-1", "lat": 1.0, "lon": 2.0, "ts": "2026-01-01T00:00:00Z", "doc_id": "d1"}],
        now=0.0,
    )
    # a different source reporting the same doc_id is not a duplicate
    result = engine.write_events(
        db,
        "app",
        inp,
        [{"user_id": "feed-2", "lat": 1.0, "lon": 2.0, "ts": "2026-01-01T00:00:00Z", "doc_id": "d1"}],
        now=1.0,
    )
    assert result.events_written == 1
    assert len(db.list_events("app", "x")) == 2


# -- poll_once (fetch + parse + write) --------------------------------------------------


def test_poll_once_rss_writes_the_event():
    db = _db_with_app("app", [_rss_input()])

    def handler(request):
        return httpx.Response(200, text=RSS_FEED)

    async def go():
        async with _client(handler) as client:
            return await engine.poll_once(client, db, "app", _rss_input(), now=0.0)

    result = asyncio.run(go())
    assert result.ok is True and result.events_written == 1
    assert len(db.list_events("app", "local_news")) == 1


def test_poll_once_json_api_open_meteo_writes_the_event():
    db = _db_with_app("app", [_wind_input()])

    def handler(request):
        assert request.url.params["latitude"] == "32.8"
        return httpx.Response(200, json={"current": {"wind_speed_10m": 4.2, "wind_direction_10m": 200}})

    async def go():
        async with _client(handler) as client:
            return await engine.poll_once(client, db, "app", _wind_input(), now=0.0)

    result = asyncio.run(go())
    assert result.ok is True and result.events_written == 1
    events = db.list_events("app", "wind")
    assert events[0].model_extra["wind_speed_10m"] == 4.2


def test_poll_once_reports_a_failed_fetch_without_raising():
    db = _db_with_app("app", [_rss_input()])

    def handler(request):
        return httpx.Response(500, text="boom")

    async def go():
        async with _client(handler) as client:
            return await engine.poll_once(client, db, "app", _rss_input(), now=0.0)

    result = asyncio.run(go())
    assert result.ok is False and result.error == "HTTP 500"  # status only, never the URL or body


# -- ConnectorEngine scheduling ----------------------------------------------------------


def test_connector_engine_polls_due_connectors_and_skips_others():
    db = _db_with_app("app", [_rss_input(poll_s=10), _wind_input(poll_s=1000)])
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if "open-meteo" in str(request.url):
            return httpx.Response(200, json={"current": {"wind_speed_10m": 1.0, "wind_direction_10m": 1}})
        return httpx.Response(200, text=RSS_FEED)

    async def go():
        ce = engine.ConnectorEngine(db, client=_client(handler))
        try:
            r1 = await ce.check_once(now=0.0)  # both due on the first check
            r2 = await ce.check_once(now=5.0)  # rss not due again yet (poll_s=10); wind not due (poll_s=1000)
            r3 = await ce.check_once(now=11.0)  # rss due again
            return r1, r2, r3
        finally:
            await ce.stop()

    r1, r2, r3 = asyncio.run(go())
    assert {r.input_id for r in r1} == {"local_news", "wind"}
    assert r2 == []
    assert {r.input_id for r in r3} == {"local_news"}


def test_connector_engine_ignores_inactive_apps_and_push_inputs():
    db = Db(":memory:")
    draft = AppSpec.model_validate(
        {
            "app_id": "draft-app",
            "intent": "t",
            "grid": {"cell_m": 10, "bbox": {"min_lat": 0.0, "min_lon": 0.0, "max_lat": 0.01, "max_lon": 0.01}},
            "inputs": [_rss_input().model_dump(mode="json")],
        }
    )
    db.upsert_app(draft, t=0.0)  # status="draft": never polled

    async def go():
        ce = engine.ConnectorEngine(db, client=_client(lambda r: httpx.Response(200, text=RSS_FEED)))
        try:
            return await ce.check_once(now=0.0)
        finally:
            await ce.stop()

    assert asyncio.run(go()) == []


def test_connector_engine_start_stop_lifecycle():
    db = _db_with_app("app", [_rss_input(poll_s=0.01)])

    async def go():
        ce = engine.ConnectorEngine(
            db, client=_client(lambda r: httpx.Response(200, text=RSS_FEED)), check_interval_s=0.01
        )
        ce.start()
        await asyncio.sleep(0.05)
        await ce.stop()
        return ce

    ce = asyncio.run(go())
    assert ce._task is None


# -- locating RSS items without georss (Scout extraction + geocoding, injected) -------------

UNLOCATED_FEED = (
    '<rss version="2.0" xmlns:georss="http://www.georss.org/georss"><channel>'
    "<item><title>Located</title><description>a</description><guid>doc-a</guid>"
    "<georss:point>0.005 0.005</georss:point></item>"
    "<item><title>Has coords in text</title><description>b</description><guid>doc-b</guid></item>"
    "<item><title>Names a place</title><description>c</description><guid>doc-c</guid></item>"
    "<item><title>Unrelated</title><description>d</description><guid>doc-d</guid></item>"
    "<item><title>Outside the area</title><description>e</description><guid>doc-e</guid></item>"
    "</channel></rss>"
)


class _Ex:
    def __init__(self, relevant, lat=None, lon=None, place=None, confidence=0.8):
        self.relevant, self.lat, self.lon, self.place, self.confidence = relevant, lat, lon, place, confidence


def _fake_locator(calls):
    async def extract(spec, inp, title, text):
        calls.append(title)
        return {
            "Has coords in text": _Ex(True, 0.004, 0.006, confidence=0.9),
            "Names a place": _Ex(True, place="the hall", confidence=0.7),
            "Unrelated": _Ex(False),
            "Outside the area": _Ex(True, 5.0, 5.0),
        }.get(title)

    async def geocode(place, spec):
        return (0.003, 0.003) if place == "the hall" else None

    return engine.make_extract_locator(extract, geocode)


def test_without_a_locator_unlocated_items_are_skipped():
    db = _db_with_app("app", [_rss_input()])
    eng = engine.ConnectorEngine(db, client=_client(lambda r: httpx.Response(200, text=UNLOCATED_FEED)))
    (res,) = asyncio.run(eng.check_once(now=100.0))
    assert res.ok and res.events_written == 1


def test_locator_places_extracted_and_geocoded_items_inside_the_bbox_only():
    db = _db_with_app("app", [_rss_input()])
    calls = []
    eng = engine.ConnectorEngine(
        db, client=_client(lambda r: httpx.Response(200, text=UNLOCATED_FEED)), locate=_fake_locator(calls)
    )
    (res,) = asyncio.run(eng.check_once(now=100.0))
    assert res.ok and res.events_written == 3  # georss + extracted coords + geocoded place
    by_doc = {e.model_extra["doc_id"]: e for e in db.list_events("app", "local_news")}
    assert set(by_doc) == {"doc-a", "doc-b", "doc-c"}  # unrelated and out-of-area dropped
    assert (by_doc["doc-b"].lat, by_doc["doc-b"].lon) == (0.004, 0.006)
    assert by_doc["doc-b"].model_extra["confidence"] == 0.9
    assert (by_doc["doc-c"].lat, by_doc["doc-c"].lon) == (0.003, 0.003)
    assert "Located" not in calls  # items with georss never reach the extractor


def test_locator_never_reextracts_a_written_document_and_is_capped(monkeypatch):
    db = _db_with_app("app", [_rss_input(poll_s=1)])
    calls = []
    eng = engine.ConnectorEngine(
        db, client=_client(lambda r: httpx.Response(200, text=UNLOCATED_FEED)), locate=_fake_locator(calls)
    )
    asyncio.run(eng.check_once(now=100.0))
    first = list(calls)
    asyncio.run(eng.check_once(now=102.0))
    assert calls[len(first) :] == []  # every doc costs at most one extraction per process
    monkeypatch.setattr(engine, "MAX_LOCATE_PER_POLL", 1)
    calls.clear()
    db2 = _db_with_app("app", [_rss_input()])
    eng2 = engine.ConnectorEngine(
        db2, client=_client(lambda r: httpx.Response(200, text=UNLOCATED_FEED)), locate=_fake_locator(calls)
    )
    asyncio.run(eng2.check_once(now=100.0))
    assert len(calls) == 1


def test_a_failing_locator_drops_only_that_document():
    db = _db_with_app("app", [_rss_input()])

    async def boom(spec, inp, item):
        raise RuntimeError("model down")

    eng = engine.ConnectorEngine(db, client=_client(lambda r: httpx.Response(200, text=UNLOCATED_FEED)), locate=boom)
    (res,) = asyncio.run(eng.check_once(now=100.0))
    assert res.ok and res.events_written == 1  # the georss item still lands


# -- #169: due polls run concurrently, bounded, each isolated ----------------------------


def _feed_input(i: int, poll_s: float = 30) -> InputSpec:
    inp = _rss_input(poll_s).model_dump(mode="json")
    inp["id"] = f"feed_{i}"
    inp["connector"]["url"] = f"http://localhost:8765/feed-{i}.xml"
    return InputSpec.model_validate(inp)


def test_a_slow_connector_does_not_hold_up_the_others():
    db = _db_with_app("app", [_feed_input(0), _feed_input(1)])
    fast_done = asyncio.Event()

    async def handler(request):
        if "feed-0" in str(request.url):
            # waits for the other poll: polled one after another, this would time out
            await asyncio.wait_for(fast_done.wait(), timeout=2.0)
        else:
            fast_done.set()
        return httpx.Response(200, text=RSS_FEED)

    async def go():
        ce = engine.ConnectorEngine(db, client=_client(handler))
        try:
            return await ce.check_once(now=0.0)
        finally:
            await ce.stop()

    results = asyncio.run(go())
    assert [r.input_id for r in results] == ["feed_0", "feed_1"]  # input order kept
    assert all(r.ok and r.events_written == 1 for r in results)


def test_concurrent_polls_are_bounded():
    db = _db_with_app("app", [_feed_input(i) for i in range(6)])
    active, peak = 0, 0

    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, text=RSS_FEED)

    async def go():
        ce = engine.ConnectorEngine(db, client=_client(handler), max_concurrent_polls=2)
        try:
            return await ce.check_once(now=0.0)
        finally:
            await ce.stop()

    results = asyncio.run(go())
    assert len(results) == 6 and all(r.ok for r in results)
    assert peak == 2


def test_one_connector_raising_never_breaks_the_others(monkeypatch):
    db = _db_with_app("app", [_feed_input(0), _feed_input(1), _feed_input(2)])
    real = engine.poll_once
    stamps = []

    async def flaky(client, db_, app_id, inp, now, **kw):
        stamps.append(now)
        if inp.id == "feed_1":
            raise RuntimeError("unexpected")
        return await real(client, db_, app_id, inp, now, **kw)

    monkeypatch.setattr(engine, "poll_once", flaky)

    async def go():
        ce = engine.ConnectorEngine(db, client=_client(lambda r: httpx.Response(200, text=RSS_FEED)))
        try:
            return await ce.check_once(now=42.0)
        finally:
            await ce.stop()

    results = asyncio.run(go())
    assert [r.input_id for r in results] == ["feed_0", "feed_2"]
    assert all(r.ok and r.events_written == 1 for r in results)
    assert stamps == [42.0, 42.0, 42.0]  # every poll stamped with the same Clock time


# -- #259: query kept, implied fields, poll status + traces, custom JSON, User-Agent -------


def _custom_json_input(url: str, config: dict, fields: list | None = None) -> InputSpec:
    return InputSpec.model_validate(
        {
            "id": "readings",
            "kind": "pull",
            "shape": "field",
            "tier": "stiff",
            "meaning": "readings at one site",
            "fields": fields or [],
            "connector": {"type": "json_api", "url": url, "poll_s": 60, "config": config},
        }
    )


def test_a_custom_json_url_keeps_its_own_query_string():
    inp = _custom_json_input(
        "https://api.example.org/v1/data?station=7&units=si", {"latitude": 0.005, "longitude": 0.005}
    )
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, json={})

    async def go():
        async with _client(handler) as client:
            return await engine.fetch_raw(client, inp.connector)

    asyncio.run(go())
    assert seen[0].params["station"] == "7" and seen[0].params["units"] == "si"


def test_a_preset_query_merges_into_an_existing_url_query():
    conn = ConnectorConfig(type="json_api", url="https://x.example.org/q?keep=1")
    assert engine._request(conn) == "https://x.example.org/q?keep=1"  # nothing to add: untouched
    conn = _wind_input().connector
    url = httpx.URL(engine._request(conn))
    assert url.params["latitude"] == "32.8" and url.params["wind_speed_unit"] == "ms"


def test_connector_fetches_send_a_descriptive_user_agent(monkeypatch):
    from app.core import config as config_mod

    agents = []

    def handler(request):
        agents.append(request.headers["user-agent"])
        return httpx.Response(200, text=RSS_FEED)

    conn = _rss_input().connector
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        engine.fetch_raw_sync(c, conn)
    assert agents[-1] == engine.DEFAULT_USER_AGENT

    monkeypatch.setattr(
        config_mod, "get_settings", lambda: config_mod.Settings(nominatim_user_agent="my-app (ops@x.org)")
    )
    monkeypatch.setattr(engine, "get_settings", config_mod.get_settings)
    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        engine.fetch_raw_sync(c, conn)
    assert agents[-1] == "my-app (ops@x.org)"


def test_rss_events_pass_field_checks_on_an_input_with_no_declared_fields():
    inp = _rss_input().model_copy(update={"fields": []})  # a spec stored before #259 (Builder: fields: [])
    db = _db_with_app("app", [inp])
    raw = engine.events_from_raw(inp.connector, RSS_FEED, now=0.0)
    result = engine.write_events(db, "app", inp, raw, now=0.0)
    assert result.ok and result.events_written == 1


def test_preset_events_pass_field_checks_on_an_input_with_no_declared_fields():
    inp = _wind_input().model_copy(update={"fields": []})
    db = _db_with_app("app", [inp])
    raw = engine.events_from_raw(inp.connector, {"current": {"wind_speed_10m": 3.0, "wind_direction_10m": 90}}, 0.0)
    assert engine.write_events(db, "app", inp, raw, now=0.0).events_written == 1


def test_implied_fields_still_reject_anything_else_undeclared():
    inp = _rss_input().model_copy(update={"fields": []})
    db = _db_with_app("app", [inp])
    raw = [{"lat": 0.005, "lon": 0.005, "ts": "2026-11-08T05:00:00Z", "doc_id": "d", "secret_extra": 1}]
    result = engine.write_events(db, "app", inp, raw, now=0.0)
    assert not result.ok and "undeclared" in result.error


def test_custom_json_reads_response_path_and_field_paths():
    config = {
        "latitude": 0.005,
        "longitude": 0.005,
        "response_path": "data.0",
        "fields": {"level": "obs.level", "rate": "obs.rate"},
    }
    inp = _custom_json_input("https://api.example.org/v1/site", config)
    db = _db_with_app("app", [inp])
    body = {"data": [{"obs": {"level": 2.5, "rate": 0.1}}]}

    def handler(request):
        return httpx.Response(200, json=body)

    async def go():
        async with _client(handler) as client:
            return await engine.poll_once(client, db, "app", inp, now=0.0)

    result = asyncio.run(go())
    assert result.ok and result.events_written == 1, result
    extra = db.list_events("app", "readings")[0].model_extra
    assert extra == {"level": 2.5, "rate": 0.1}


def test_a_bad_custom_config_fails_the_poll_before_fetching():
    inp = _custom_json_input("https://api.example.org/v1/site", {"fields": {"lat": "x"}})
    db = _db_with_app("app", [inp])
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={})

    async def go():
        async with _client(handler) as client:
            return await engine.poll_once(client, db, "app", inp, now=0.0)

    result = asyncio.run(go())
    assert not result.ok and "reserved" in result.error and calls == []


def test_poll_status_is_recorded_and_traced_only_on_change():
    db = _db_with_app("app", [_rss_input(poll_s=1)])
    state = {"code": 403}

    def handler(request):
        if state["code"] != 200:
            return httpx.Response(state["code"], text="<html>secret body</html>")
        return httpx.Response(200, text=RSS_FEED)

    eng = engine.ConnectorEngine(db, client=_client(handler))

    def traces():
        return [t for t in db.list_trace("app") if t.action == "connector_poll"]

    asyncio.run(eng.check_once(now=10.0))
    assert db.get_connector_status("app", "local_news") == (10.0, "error: HTTP 403")
    assert len(traces()) == 1 and traces()[0].agent == "scout"
    assert "HTTP 403" in traces()[0].result and "secret body" not in traces()[0].model_dump_json()

    asyncio.run(eng.check_once(now=20.0))  # still failing the same way: recorded, not re-traced
    assert db.get_connector_status("app", "local_news") == (20.0, "error: HTTP 403")
    assert len(traces()) == 1

    state["code"] = 200
    asyncio.run(eng.check_once(now=30.0))  # recovered: one trace
    assert db.get_connector_status("app", "local_news") == (30.0, "ok")
    assert len(traces()) == 2 and "ok again" in traces()[1].result

    asyncio.run(eng.check_once(now=40.0))  # healthy and unchanged: nothing new
    assert len(traces()) == 2


def test_a_first_successful_poll_is_recorded_without_a_trace():
    db = _db_with_app("app", [_rss_input(poll_s=1)])
    eng = engine.ConnectorEngine(db, client=_client(lambda r: httpx.Response(200, text=RSS_FEED)))
    asyncio.run(eng.check_once(now=5.0))
    assert db.get_connector_status("app", "local_news") == (5.0, "ok")
    assert not [t for t in db.list_trace("app") if t.action == "connector_poll"]


def test_poll_errors_never_include_the_url_or_its_query():
    inp = _custom_json_input("https://api.example.org/v1/site?api_key=SECRET123", {"latitude": 0.0, "longitude": 0.0})

    def handler(request):
        raise httpx.ConnectError("could not reach https://api.example.org/v1/site?api_key=SECRET123")

    db = _db_with_app("app", [inp])

    async def go():
        async with _client(handler) as client:
            return await engine.poll_once(client, db, "app", inp, now=0.0)

    result = asyncio.run(go())
    assert not result.ok and "SECRET123" not in result.error
