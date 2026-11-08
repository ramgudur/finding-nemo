"""#266: public app info, routing modes (route / escape / nearest_safe / fallback) with walking
time, and destination points outside the area rejected. Neutral synthetic examples only."""

import copy
from datetime import UTC, datetime

import numpy as np
import pytest
from fake_llm import FakeAsyncOpenAI
from fastapi.testclient import TestClient

from app.agents.llm import LlmRouter
from app.core import clock as clock_mod
from app.core.clock import SimClock
from app.core.config import Settings
from app.core.db import Db
from app.core.models import AppSpec, DestinationPoint
from app.engine import routing
from app.engine.grid import Grid
from app.engine.tick import TickEngine
from app.main import DbEventSource, create_app
from app.tools import message_guard as mg

NOW = datetime(2026, 11, 8, tzinfo=UTC)
AREA = {"min_lat": 0.0, "min_lon": 0.0, "max_lat": 0.01, "max_lon": 0.01}


# -- A. GET /v1/apps/{app}/public ----------------------------------------------------------


def _app_spec(app_id: str = "area_watch-demo", **over) -> dict:
    spec = {
        "app_id": app_id,
        "intent": "show where entities gather",
        "grid": {"cell_m": 10, "bbox": AREA},
        "inputs": [
            {
                "id": "pings",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "a ping means an entity is here " + "and it is a very long description " * 4,
                "operators": [{"op": "density", "weight": 0.5}],
                "plausibility": {"max_speed_mps": 5.0},
            },
            {
                "id": "secret-feed",
                "kind": "pull",
                "shape": "document",
                "tier": "stiff",
                "meaning": "private reports from a confidential source",
                "connector": {"type": "rss", "url": "https://feeds.example.org/private.xml", "poll_s": 300},
                "operators": [{"op": "event_points", "weight": 0.5}],
            },
            {
                "id": "markers",
                "kind": "push",
                "shape": "fixed_point",
                "tier": "locked",
                "meaning": "authority markers",
                "operators": [{"op": "override", "weight": 0.0}],
            },
        ],
        "outputs": {
            "layers": [
                {"id": "pings.density", "visibility": "public"},
                {"id": "secret-feed.event_points"},
                {"id": "score"},
            ]
        },
        "routing": {"enabled": True, "destination_points": [{"lat": 0.005, "lon": 0.005, "label": "meeting area"}]},
    }
    spec.update(over)
    return spec


@pytest.fixture
def ticking():
    clock = SimClock(start=1_800_000_000.0)
    prev = clock_mod.set_clock(clock)
    try:
        db = Db(":memory:")
        engine = TickEngine(DbEventSource(db))
        app = create_app(db=db, engine=engine, autostart_engine=False)
        with TestClient(app) as c:
            yield c, engine, clock
    finally:
        clock_mod.set_clock(prev)


def test_public_info_404_for_unknown_app(ticking):
    c, _, _ = ticking
    assert c.get("/v1/apps/nope/public").status_code == 404


def test_public_info_for_a_draft_says_so_and_lists_only_public_sources(ticking):
    c, _, _ = ticking
    assert c.post("/v1/apps", json=_app_spec()).status_code == 201
    r = c.get("/v1/apps/area_watch-demo/public")
    assert r.status_code == 200
    assert r.headers["Cache-Control"] == "max-age=30"
    body = r.json()
    assert body["title"] == "Area Watch Demo"
    assert body["status"] == "draft" and body["routing_enabled"] is True
    assert body["intent"] == "show where entities gather"
    assert body["bbox"] == AREA and body["last_update"] is None
    named, counted = body["sources"][0], body["sources"][1:]
    assert named["kind"] == "push" and len(named["label"]) <= 80 and named["label"].startswith("a ping means")
    assert counted == [{"kind": "push", "label": "1 private source"}, {"kind": "pull", "label": "1 private source"}]
    # Rule 8: nothing authority-only leaks: no ids, URLs, operators, weights, tiers or private meanings.
    text = r.text
    for secret in ("secret-feed", "feeds.example.org", "confidential", "event_points", "density", "stiff", "0.5"):
        assert secret not in text, secret
    assert set(body) == {
        "app_id",
        "title",
        "intent",
        "status",
        "routing_enabled",
        "bbox",
        "last_update",
        "sources",
    }


def test_public_info_reports_the_last_tick_once_active(ticking):
    c, engine, clock = ticking
    assert c.post("/v1/apps", json=_app_spec()).status_code == 201
    assert c.post("/v1/apps/area_watch-demo/activate").status_code == 200
    assert c.get("/v1/apps/area_watch-demo/public").json()["last_update"] is None
    engine.tick_once()
    body = c.get("/v1/apps/area_watch-demo/public").json()
    assert body["status"] == "active"
    assert datetime.fromisoformat(body["last_update"]).timestamp() == pytest.approx(clock.now())


# -- D. routing modes ---------------------------------------------------------------------

# Synthetic 1 km x 1 km box, 10 m cells: 100 x 100. Nothing app-specific.
BOX = {"min_lat": 45.0, "min_lon": 10.0, "max_lat": 45.009, "max_lon": 10.0127}


def _route_spec(points: list[dict]) -> AppSpec:
    return AppSpec.model_validate(
        {
            "app_id": "route-modes",
            "intent": "test",
            "status": "active",
            "grid": {"cell_m": 10, "bbox": BOX},
            "routing": {"enabled": True, "alpha": 4.0, "impassable_score": 0.8, "destination_points": points},
        }
    )


@pytest.fixture
def grid() -> Grid:
    return Grid.from_spec(_route_spec([]).grid)


def _ll(grid: Grid, x: float, y: float) -> tuple[float, float]:
    lat, lon = grid.to_latlon(x, y)
    return float(lat), float(lon)


def _point(grid: Grid, x: float, y: float, label: str) -> dict:
    lat, lon = _ll(grid, x, y)
    return {"lat": lat, "lon": lon, "label": label}


def _hot_disc(grid: Grid, cx: float, cy: float, r_m: float, value: float = 0.9) -> np.ndarray:
    x, y = grid.centers_xy()
    score = np.zeros(grid.shape)
    score[np.hypot(x - cx, y - cy) <= r_m] = value
    return score


def _ask(spec, grid, score, x, y):
    lat, lon = _ll(grid, x, y)
    return mg.safe_instruction(spec=spec, grid=grid, score=score, lat=lat, lon=lon, pois=[], now=NOW)


def test_nearby_destination_is_a_normal_route_with_walking_time(grid):
    spec = _route_spec([_point(grid, 800, 500, "meeting area")])
    ins = _ask(spec, grid, np.zeros(grid.shape), 200, 500)
    assert ins.guard.passed and ins.mode == "route"
    assert ins.destination.label == "meeting area"
    assert ins.walk_minutes == routing.walk_minutes(ins.distance_m) == round(ins.distance_m / 1.3 / 60)
    assert f"about {ins.walk_minutes} min on foot" in ins.text
    assert "consider driving" not in ins.text


def test_start_inside_a_blocked_area_is_an_escape(grid):
    spec = _route_spec([_point(grid, 900, 500, "meeting area")])
    score = _hot_disc(grid, 500, 500, 60)
    ins = _ask(spec, grid, score, 500, 500)
    assert ins.guard.passed and ins.mode == "escape"
    assert ins.text.startswith("Leave this area now")
    assert ins.destination.label == "meeting area"


def test_far_destination_routes_to_the_nearest_safe_cell_instead(grid):
    # The only destination is ~900 m away; the hotspot edge is ~40 m from the caller.
    spec = _route_spec([_point(grid, 980, 980, "meeting area")])
    score = _hot_disc(grid, 100, 100, 60)
    ins = _ask(spec, grid, score, 100, 100)
    assert ins.guard.passed, ins.guard
    assert ins.mode == "nearest_safe"
    assert ins.destination.label == routing.SAFE_LABEL
    assert "out of the high-risk area" in ins.text and "min on foot" in ins.text
    assert len(ins.text) <= 360
    assert not mg._COORD_RE.search(ins.text)
    # The target is safe and clear of every hotspot-level cell by SAFE_CLEAR_CELLS.
    mask = routing.safe_mask(score, safe_max_score=0.5, hot_score=0.5)
    r, c, _ = grid.cell_of(ins.destination.lat, ins.destination.lon)
    assert mask[int(r), int(c)]
    hot_r, hot_c = np.nonzero(score >= 0.5)
    assert np.min(np.maximum(abs(hot_r - int(r)), abs(hot_c - int(c)))) >= routing.SAFE_CLEAR_CELLS
    assert ins.distance_m < 200


def test_no_destination_at_all_still_gets_out_of_danger(grid):
    spec = _route_spec([])
    ins = _ask(spec, grid, _hot_disc(grid, 500, 500, 100), 500, 500)
    assert ins.guard.passed and ins.mode == "nearest_safe"
    assert ins.text.startswith("Leave this area now: move about")


def test_caller_already_safe_with_no_destination_gets_the_fallback(grid):
    spec = _route_spec([])
    ins = _ask(spec, grid, np.zeros(grid.shape), 500, 500)
    assert ins.mode == "fallback" and not ins.guard.passed
    assert ins.text == spec.routing.fallback_text and ins.walk_minutes is None


def test_nowhere_safe_is_the_fallback(grid):
    spec = _route_spec([_point(grid, 900, 900, "meeting area")])
    ins = _ask(spec, grid, np.full(grid.shape, 0.9), 500, 500)
    assert ins.mode == "fallback" and ins.route == []


def test_far_threshold_scales_with_the_danger_and_cell_size(grid):
    def route_of(d):
        return routing.Route(DestinationPoint(lat=0, lon=0, label="x"), [], [], d, d)

    safe = route_of(150.0)
    # 10 m cells: never "too far" under FAR_MIN_CELLS cells, and only past FAR_FACTOR x the safe distance.
    assert not routing.too_far(route_of(routing.FAR_MIN_CELLS * 10.0), safe, 10.0)
    assert routing.too_far(route_of(routing.FAR_MIN_CELLS * 10.0 + 1), safe, 10.0)
    big = route_of(1000.0)
    assert not routing.too_far(route_of(2900.0), big, 10.0)
    assert routing.too_far(route_of(3100.0), big, 10.0)


def test_walking_text_suggests_driving_over_8_km():
    assert routing.walk_minutes(0) == 0
    assert routing.walk_minutes(10) == 1
    assert routing.walk_minutes(936) == 12
    assert "consider driving" in routing._travel(8001)
    assert "consider driving" not in routing._travel(8000)


def test_long_labels_still_fit_360_chars(grid):
    label = "meeting area " * 25  # 325 chars
    spec = _route_spec([_point(grid, 900, 500, label.strip())])
    ins = _ask(spec, grid, np.zeros(grid.shape), 100, 500)
    assert len(ins.text) <= 360  # too long -> the guard fails and the fallback text is sent
    assert ins.mode in ("route", "fallback")


# -- F. destination points outside the area -------------------------------------------------

OUTSIDE = [{"lat": 0.02, "lon": 0.005, "label": "far point"}]


def test_create_app_rejects_destination_points_outside_the_area():
    with TestClient(create_app(db=Db(":memory:"), autostart_engine=False)) as c:
        spec = _app_spec("outside-app")
        spec["routing"] = {"enabled": True, "destination_points": OUTSIDE}
        r = c.post("/v1/apps", json=spec)
        assert r.status_code == 422
        assert "outside the app's area" in r.text
        edge = _app_spec("edge-app")
        edge["routing"] = {"enabled": True, "destination_points": [{"lat": 0.01, "lon": 0.0, "label": "corner"}]}
        assert c.post("/v1/apps", json=edge).status_code == 201  # bbox edges count as inside


def test_put_spec_rejects_destination_points_outside_the_area():
    with TestClient(create_app(db=Db(":memory:"), autostart_engine=False)) as c:
        spec = _app_spec("edit-outside", status="pending_confirmation")
        assert c.post("/v1/apps", json=spec).status_code == 201
        edited = copy.deepcopy(spec)
        edited["routing"] = {"enabled": True, "destination_points": OUTSIDE}
        r = c.put("/v1/apps/edit-outside/spec", json=edited)
        assert r.status_code == 422
        assert any("outside the app's area" in e for e in r.json()["detail"])


def test_compile_rejects_destination_points_outside_the_area_before_any_model_call():
    fake = FakeAsyncOpenAI("compiler_campus_alert")  # any script: it must never be called
    llm = LlmRouter(Settings(_env_file=None), clients={"nvidia": fake, "openrouter": fake})
    with TestClient(create_app(db=Db(":memory:"), llm=llm, autostart_engine=False)) as c:
        body = {
            "intent": "show where entities gather",
            "grid": {"cell_m": 10, "bbox": AREA},
            "inputs": [{"id": "pings", "kind": "push", "shape": "point", "meaning": "a ping means an entity is here"}],
            "destination_points": OUTSIDE,
        }
        r = c.post("/v1/apps/compile-outside/compile", json=body)
    assert r.status_code == 422
    assert "outside the app's area" in r.text
    assert fake.calls == []
