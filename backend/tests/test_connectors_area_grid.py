"""A json_api preset with `area_grid` samples a grid of points across the app's area in one request
(recorded Open-Meteo multi-point fixture; no network)."""

import asyncio
import json
from pathlib import Path

import httpx
import numpy as np
from test_connectors_engine import _db_with_app, _permissive_url_guard, _wind_input  # noqa: F401 - autouse fixture

from app.connectors import engine, json_api
from app.connectors.presets import PRESETS
from app.core.models import AppSpec, BBox, ConnectorConfig
from app.engine.grid import Grid
from app.engine.operators.base import Events
from app.engine.operators.field import FieldParams, field
from app.engine.trust import suspicious_users

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "open_meteo_area.json").read_text(encoding="utf-8"))
GRID_DECL = PRESETS["open_meteo"]["area_grid"]
KM = 1 / 111.195  # degrees of latitude per km


def _square(km: float, lat: float = 0.0) -> BBox:
    return BBox(min_lat=lat, min_lon=0.0, max_lat=lat + km * KM, max_lon=km * KM)


def _points(bbox: BBox) -> list[json_api.AreaPoint]:
    return json_api.area_points(bbox.min_lat, bbox.min_lon, bbox.max_lat, bbox.max_lon, GRID_DECL)


def _reading(speed: float, deg: float) -> dict:
    return {"latitude": 0.0, "longitude": 0.0, "current": {"wind_speed_10m": speed, "wind_direction_10m": deg}}


def test_grid_size_follows_the_area():
    assert len(_points(_square(1))) == 1  # a 1 km area has one reading: its centre
    (p,) = _points(_square(1))
    assert abs(p.lat - 0.5 * KM) < 1e-5 and abs(p.lon - 0.5 * KM) < 1e-5
    assert len(_points(_square(5))) == 4  # 2 x 2 at 2.5 km spacing
    assert len(_points(_square(50))) == 16  # capped by max_points
    assert len(_points(_square(500))) <= GRID_DECL["max_points"]
    long_thin = BBox(min_lat=0.0, min_lon=0.0, max_lat=1 * KM, max_lon=50 * KM)
    assert len(_points(long_thin)) == 1  # spacing holds along the short side too
    pts = _points(_square(50))
    assert len({(p.lat, p.lon) for p in pts}) == 16 and len({p.point_id for p in pts}) == 16
    b = _square(50)
    assert all(b.min_lat < p.lat < b.max_lat and b.min_lon < p.lon < b.max_lon for p in pts)


def test_the_hard_ceiling_holds_whatever_the_preset_says():
    pts = json_api.area_points(0.0, 0.0, 10.0, 10.0, {"max_points": 10_000, "min_spacing_m": 0})
    assert len(pts) <= json_api.MAX_AREA_POINTS


def test_one_request_joins_every_point():
    conn = ConnectorConfig(type="json_api", preset="open_meteo", config={"latitude": 9.0, "longitude": 9.0})
    url = httpx.URL(engine._request(conn, _square(50)))
    lats, lons = url.params["latitude"].split(","), url.params["longitude"].split(",")
    assert len(lats) == len(lons) == 16
    assert "9.0" not in lats  # the config's centre is only a fallback
    assert httpx.URL(engine._request(conn)).params["latitude"] == "9.0"  # no bbox known: the config point
    one = httpx.URL(engine._request(conn, _square(1))).params["latitude"]
    assert "," not in one and abs(float(one) - 0.5 * KM) < 1e-5  # one point: a plain number


def test_multi_point_response_becomes_one_event_per_point():
    conn = ConnectorConfig(type="json_api", preset="open_meteo", config={"latitude": 0.0, "longitude": 0.0})
    bbox = _square(5)
    events = engine.events_from_raw(conn, FIXTURE, now=1_800_000_000.0, bbox=bbox)
    pts = _points(bbox)
    assert len(events) == 4
    for e, p, raw in zip(events, pts, FIXTURE, strict=True):
        assert (e["lat"], e["lon"], e["user_id"]) == (p.lat, p.lon, p.point_id)
        assert e["wind_speed_10m"] == raw["current"]["wind_speed_10m"]
        assert e["ts"].timestamp() == 1_800_000_000.0
    try:
        engine.events_from_raw(conn, FIXTURE[:3], now=0.0, bbox=bbox)
    except ValueError as err:
        assert "expected 4" in str(err)
    else:
        raise AssertionError("a short response must be refused")


def test_an_existing_app_gets_the_grid_without_a_spec_change():
    db = _db_with_app("app", [_wind_input()])  # preset open_meteo, config lat/lon = a fixed point
    spec = db.get_app("app")
    spec = spec.model_copy(update={"grid": spec.grid.model_copy(update={"bbox": _square(50, lat=30.0), "cell_m": 500})})
    db.upsert_app(spec, t=0.0)
    seen = []

    def handler(request):
        seen.append(request.url)
        n = len(request.url.params["latitude"].split(","))
        return httpx.Response(200, json=[_reading(2.0 + i, 10.0 * i) for i in range(n)])

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await engine.poll_once(client, db, "app", _wind_input(), now=100.0)

    result = asyncio.run(go())
    assert len(seen) == 1, result  # one request per poll
    assert result.ok is True and result.events_written == 16
    stored = db.list_events("app", "wind")
    assert len({e.user_id for e in stored}) == 16 and len({(e.lat, e.lon) for e in stored}) == 16
    # stable ids at distinct points: nothing for the spoof checks to flag
    ev = Events.from_records(
        [{"user_id": e.user_id, "t": e.ts.timestamp(), "lat": e.lat, "lon": e.lon} for e in stored]
    )
    assert not suspicious_users(ev)


def test_field_operator_varies_across_the_area_from_the_grid_events():
    spec = AppSpec.model_validate(
        {"app_id": "a", "intent": "t", "grid": {"cell_m": 250, "bbox": _square(5).model_dump()}}
    )
    conn = ConnectorConfig(type="json_api", preset="open_meteo", config={})
    events = engine.events_from_raw(conn, FIXTURE, now=50.0, bbox=spec.grid.bbox)
    ev = Events.from_records([{**e, "t": 50.0} for e in events])
    params = FieldParams(
        value_field="wind_speed_10m", vector_speed_field="wind_speed_10m", vector_dir_field="wind_direction_10m"
    )

    class Clock:
        def now(self) -> float:
            return 60.0

    r = field(ev, params, None, Clock(), Grid.from_spec(spec.grid))
    assert r.extras["n_points"] == 4
    u, v = r.extras["vector_u"], r.extras["vector_v"]
    angle = np.degrees(np.arctan2(u, v))
    assert angle.max() - angle.min() > 20.0  # the fixture's directions differ by up to 52 degrees
    assert np.hypot(u, v).std() > 0.1


def test_check_connector_counts_every_point():
    conn = ConnectorConfig(type="json_api", preset="open_meteo", config={"latitude": 0.0, "longitude": 0.0})
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=FIXTURE)))
    with client:
        events = engine.check_connector_sync(client, conn, now=0.0, bbox=_square(5))
    assert len(events) == 4
