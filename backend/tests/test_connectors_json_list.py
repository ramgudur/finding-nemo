"""`json_list` pull connector (#267): a fetched list of located items, one event each. No network
(httpx.MockTransport)."""

import asyncio

import httpx
import pytest

from app.agents.source_suggester import catalogue
from app.connectors import engine, json_list
from app.connectors import fields as conn_fields
from app.core.db import Db
from app.core.models import AppSpec, BBox, ConnectorConfig, InputSpec

BBOX = BBox(min_lat=0.0, min_lon=0.0, max_lat=0.01, max_lon=0.01)
URL = "https://data.example.org/items.geojson"


@pytest.fixture(autouse=True)
def _permissive_url_guard(monkeypatch):
    from app.connectors import url_guard

    monkeypatch.setattr(url_guard, "check_url", lambda url, **kw: url)


def _geojson(*features) -> dict:
    return {"type": "FeatureCollection", "features": list(features)}


def _feature(lon, lat, fid="a", t=1_800_000_000_000, level=3.5, label="x") -> dict:
    return {
        "type": "Feature",
        "id": fid,
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {"time": t, "level": level, "label": label},
    }


CONFIG = {
    "items_path": "features",
    "lat": "geometry.coordinates.1",
    "lon": "geometry.coordinates.0",
    "ts": "properties.time",
    "ts_format": "epoch_ms",
    "user_id": "id",
    "fields": {"level": "properties.level"},
}


def _input(config: dict | None = None, fields: list | None = None, url: str | None = URL) -> InputSpec:
    return InputSpec.model_validate(
        {
            "id": "items",
            "kind": "pull",
            "shape": "point",
            "tier": "stiff",
            "meaning": "located items from a public list",
            "fields": fields or [],
            "connector": {"type": "json_list", "url": url, "poll_s": 60, "config": config or dict(CONFIG)},
        }
    )


def _db(inp: InputSpec) -> Db:
    db = Db(":memory:")
    spec = AppSpec.model_validate(
        {
            "app_id": "app",
            "intent": "test",
            "status": "active",
            "grid": {"cell_m": 10, "bbox": BBOX.model_dump()},
            "inputs": [inp.model_dump()],
        }
    )
    db.upsert_app(spec, t=0.0)
    return db


def _client(body) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))


def _poll(db: Db, body, now: float = 1_800_000_100.0):
    inp = db.get_app("app").inputs[0]

    async def go():
        async with _client(body) as client:
            return await engine.poll_once(client, db, "app", inp, now, spec=db.get_app("app"))

    return asyncio.run(go())


# -- config -----------------------------------------------------------------------------


def test_config_uses_the_webhook_mapping_keys_and_rejects_unknown_ones():
    m = json_list.check_config({**CONFIG, "url": URL})
    assert (m.items_path, m.lat, m.ts_format, m.fields) == (
        "features",
        "geometry.coordinates.1",
        "epoch_ms",
        CONFIG["fields"],
    )
    with pytest.raises(ValueError, match="code"):
        json_list.check_config({**CONFIG, "code": "x"})
    with pytest.raises(ValueError, match="lat"):
        json_list.check_config({"lon": "a"})
    with pytest.raises(ValueError, match="reserved"):
        json_list.check_config({**CONFIG, "fields": {"lat": "a"}})
    with pytest.raises(ValueError, match="empty segment"):
        json_list.check_config({**CONFIG, "lat": "a..b"})


def test_connector_type_is_accepted_and_its_url_may_sit_in_config():
    conn = ConnectorConfig(type="json_list", config={**CONFIG, "url": URL})
    assert engine.connector_url(conn) == URL
    assert engine.connector_url(ConnectorConfig(type="json_list", url=URL, config=CONFIG)) == URL


def test_implied_fields_are_the_mapping_field_names():
    conn = ConnectorConfig(type="json_list", url=URL, config={**CONFIG, "fields": {"level": "a", "count": "b"}})
    assert [(f.name, f.type, f.required) for f in conn_fields.implied_fields(conn)] == [
        ("level", "number", False),
        ("count", "number", False),
    ]
    bad = ConnectorConfig(type="json_list", url=URL, config={"bogus": 1})
    assert conn_fields.implied_fields(bad) == []


# -- parse -----------------------------------------------------------------------------


def test_parse_maps_every_item_and_drops_and_counts_items_outside_the_area():
    body = _geojson(_feature(0.005, 0.005, "a"), _feature(0.006, 0.004, "b"), _feature(5.0, 5.0, "far"))
    parsed = json_list.parse(body, CONFIG, now=0.0, bbox=BBOX)
    assert parsed.items == 3 and parsed.outside == 1 and parsed.unmapped == 0
    assert [e["user_id"] for e in parsed.events] == ["a", "b"]
    assert parsed.events[0]["lat"] == 0.005 and parsed.events[0]["level"] == 3.5
    assert parsed.events[0]["ts"].timestamp() == 1_800_000_000.0


def test_parse_counts_unmappable_items_without_echoing_values():
    broken = {"type": "Feature", "id": "z", "geometry": None, "properties": {"time": 1}}
    parsed = json_list.parse(_geojson(_feature(0.005, 0.005), broken), CONFIG, now=0.0, bbox=BBOX)
    assert len(parsed.events) == 1 and parsed.unmapped == 1
    assert parsed.first_error == "path 'geometry.coordinates.1' not found"


def test_parse_without_ts_stamps_the_fetch_time_and_coerces_values_losslessly():
    cfg = {"lat": "y", "lon": "x", "fields": {"level": "v"}}
    declared = conn_fields.implied_fields(ConnectorConfig(type="json_list", url=URL, config=cfg))
    body = [{"y": "0.005", "x": 0.005, "v": "2.5"}, {"y": 0.001, "x": 0.001, "v": "not a number"}]
    parsed = json_list.parse(body, cfg, now=1_800_000_000.0, bbox=BBOX, declared=declared)
    assert [e.get("level") for e in parsed.events] == [2.5, None]  # optional field left out, item kept
    assert all(e["ts"].timestamp() == 1_800_000_000.0 for e in parsed.events)


def test_parse_caps_items_per_poll():
    body = [{"lat": 0.005, "lon": 0.005}] * (json_list.MAX_ITEMS + 7)
    parsed = json_list.parse(body, {"lat": "lat", "lon": "lon"}, now=0.0)
    assert len(parsed.events) == json_list.MAX_ITEMS and parsed.truncated == 7


def test_a_missing_items_path_is_a_config_error():
    with pytest.raises(ValueError, match="items_path"):
        json_list.parse({"other": []}, CONFIG, now=0.0)


# -- poll ------------------------------------------------------------------------------


def test_poll_writes_items_inside_the_area_and_skips_ones_already_stored():
    db = _db(_input())
    body = _geojson(_feature(0.005, 0.005, "a"), _feature(0.002, 0.003, "b"), _feature(9.0, 9.0, "far"))
    r = _poll(db, body)
    assert r.ok and r.events_written == 2 and r.dropped_outside == 1
    events = db.list_events("app", "items")
    assert {e.user_id for e in events} == {"a", "b"} and events[0].model_extra == {"level": 3.5}
    again = _poll(db, body)  # the same list next poll: nothing new
    assert again.ok and again.events_written == 0
    newer = _poll(db, _geojson(_feature(0.005, 0.005, "a", t=1_800_000_060_000)))
    assert newer.events_written == 1


def test_poll_with_no_mappable_item_is_an_error_status_without_values():
    db = _db(_input())
    r = _poll(db, _geojson({"id": "q", "geometry": {"coordinates": []}, "properties": {"time": 1}}))
    assert not r.ok and "no item could be mapped" in r.error and "q" not in r.error
    assert engine.poll_status(r).startswith("error: ")


def test_poll_records_status_like_any_connector():
    db = _db(_input())
    inp = db.get_app("app").inputs[0]

    async def go():
        ce = engine.ConnectorEngine(db, client=_client(_geojson(_feature(0.005, 0.005))))
        await ce.check_once(now=1_800_000_100.0)
        await ce.client.aclose()

    asyncio.run(go())
    assert db.get_connector_status("app", inp.id) == (1_800_000_100.0, "ok")


def test_check_connector_sync_explains_when_every_item_is_outside_the_area():
    conn = _input().connector
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=_geojson(_feature(9, 9)))))
    with pytest.raises(ValueError, match="outside the app's area"):
        engine.check_connector_sync(client, conn, 0.0, bbox=BBOX)
    ok = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=_geojson(_feature(0.005, 0.005))))
    )
    assert len(engine.check_connector_sync(ok, conn, 0.0, bbox=BBOX)) == 1


def test_every_fetch_goes_through_the_url_guard(monkeypatch):
    from app.connectors import url_guard

    def refuse(url, **kw):
        raise url_guard.UnsafeUrl("private and local addresses can't be fetched (outside demo mode)")

    monkeypatch.setattr(url_guard, "check_url", refuse)
    db = _db(_input(url="http://127.0.0.1/items.json"))
    r = _poll(db, [])
    assert not r.ok and r.error.startswith("UnsafeUrl") and "127.0.0.1" not in r.error


def test_suggester_catalogue_offers_the_list_feed():
    opt = next(o for o in catalogue() if o.type == "json_list")
    assert opt.label == "JSON list / GeoJSON feed (we fetch it)"
