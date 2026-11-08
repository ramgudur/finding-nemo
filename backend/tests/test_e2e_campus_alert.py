"""End to end, build step 1: campus_alert sim -> ingest API -> tick -> /map and /hotspots.

Drives the real app (`app.main.create_app`) through a TestClient on an in-memory DB: create the app
from specs/campus-alert.json, activate it, post every sim event with `X-Ingest-Key`, tick the engine
on a manual SimClock, and read the outputs. No network, no LLM, fixed seed.
"""

import math

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sim import campus_alert as ca
from sim.common import LocalFrame, load_spec

from app.core import clock as clock_mod
from app.core.clock import SimClock
from app.core.db import Db
from app.core.models import AppSpec
from app.engine.tick import TickEngine
from app.main import DbEventSource, create_app

T0 = 1_800_000_000.0
SEED = 7
TICK_S = 2
SNAPSHOTS = (20, 58, 62, 148, 152, 200, 204)
CLEAR_AT = 202  # the test (not the sim) clears the police marker here


class ApiPlatform:
    """The real API on an isolated in-memory DB; the test drives the tick instead of the loop."""

    def __init__(self, raw_spec: dict, clock: SimClock) -> None:
        self.db = Db(":memory:")
        self.engine = TickEngine(DbEventSource(self.db), clock=clock)
        self.client = TestClient(create_app(db=self.db, engine=self.engine, autostart_engine=False))
        self.app_id = raw_spec["app_id"]
        r = self.client.post("/v1/apps", json=raw_spec)
        assert r.status_code == 201, r.text
        r = self.client.post(f"/v1/apps/{self.app_id}/activate")
        assert r.status_code == 200, r.text
        keys = r.json()
        self.ingest_key, self.authority_key = keys["ingest_key"], keys["authority_key"]
        self.runtime = self.engine.apps[self.app_id]

    def post_event(self, app_id: str, input_id: str, body: dict, ingest_key: str) -> int:
        url = f"/v1/apps/{app_id}/inputs/{input_id}/events"
        return self.client.post(url, json=body, headers={"X-Ingest-Key": ingest_key}).status_code

    def _get(self, path: str, authority_key: str | None) -> dict:
        headers = {"X-Authority-Key": authority_key} if authority_key else {}
        r = self.client.get(path, headers=headers)
        assert r.status_code == 200, r.text
        return r.json()

    def get_map(self, app_id: str, authority_key: str | None = None) -> dict:
        return self._get(f"/v1/apps/{app_id}/map?format=geojson", authority_key)

    def get_hotspots(self, app_id: str, authority_key: str | None = None) -> dict:
        return self._get(f"/v1/apps/{app_id}/hotspots", authority_key)


RAW = load_spec("campus-alert")
FRAME = LocalFrame.from_spec(RAW)


@pytest.fixture(scope="module")
def run():
    """Create + activate via the API, replay the whole scenario through ingest, tick every 2 s, keep snapshots."""
    clock = SimClock(start=T0)
    prev = clock_mod.set_clock(clock)  # the API stamps activation/ingest from the process clock
    try:
        p = ApiPlatform(RAW, clock)
        app_id = p.app_id
        sc = ca.build(SEED)
        posts = sorted(sc.posts)
        statuses, snaps, i = [], {}, 0
        police = next(x for x in posts if x.input_id == "police_marker")
        for k in range(0, int(ca.DURATION) + 1, TICK_S):
            clock.set(T0 + k)
            while i < len(posts) and posts[i].sim_t <= k:
                statuses.append(p.post_event(app_id, posts[i].input_id, posts[i].stamped(T0), p.ingest_key))
                i += 1
            if k == CLEAR_AT:
                clear = {**police.body, "cleared": True, "ts": ca.iso(T0 + k)}
                statuses.append(p.post_event(app_id, "police_marker", clear, p.ingest_key))
            p.engine.tick_once()
            if k in SNAPSHOTS:
                snaps[k] = {
                    "result": p.engine.latest(app_id),
                    "map": p.get_map(app_id, p.authority_key),
                    "public_map": p.get_map(app_id),
                    "hotspots": p.get_hotspots(app_id, p.authority_key),
                    "public_hotspots": p.get_hotspots(app_id),
                }
        yield {"platform": p, "scenario": sc, "statuses": statuses, "snaps": snaps, "police": police}
    finally:
        clock_mod.set_clock(prev)


def _dist_m(lat: float, lon: float, xy: tuple[float, float]) -> float:
    return math.dist(FRAME.to_xy(lat, lon), xy)


def test_every_sim_event_is_accepted(run):
    assert run["statuses"] and set(run["statuses"]) == {202}
    assert len(run["statuses"]) == len(run["scenario"].posts) + 1  # + the test's clear event


def test_bad_keys_are_rejected(run):
    p = run["platform"]
    body = run["scenario"].posts[0].stamped(T0)
    assert p.post_event("campus-alert", "panic", body, "wrong") == 401
    assert p.client.get("/v1/apps/campus-alert/map", headers={"X-Authority-Key": "wrong"}).status_code == 401


def test_undeclared_field_is_rejected(run):
    p = run["platform"]
    body = {**run["scenario"].posts[0].stamped(T0), "not_a_field": 1}
    assert p.post_event("campus-alert", "panic", body, p.ingest_key) == 422


def test_map_and_hotspots_follow_the_output_contracts(run):
    snap = run["snaps"][62]
    m = snap["map"]
    assert set(m) == {"app_id", "t", "cell_m", "bbox", "layers", "vectors"}
    assert m["bbox"] == list(AppSpec.model_validate(RAW).grid.bbox.as_geojson())
    assert "score" in m["layers"] and "panic.flow_source" in m["layers"]
    for fc in m["layers"].values():
        assert fc["type"] == "FeatureCollection"
        for f in fc["features"]:
            assert set(f["properties"]) == {"value", "row", "col"}
            assert f["properties"]["value"] >= 0.01  # near-zero cells omitted
    hs = snap["hotspots"]["hotspots"]
    assert hs
    for h in hs:
        assert set(h) == {
            "id", "lat", "lon", "radius_m", "score", "mean", "area_m2",
            "n_cells", "persisted_ticks", "contributing_layers", "visibility",
        }  # fmt: skip


def test_public_caller_gets_no_authority_layers_or_hotspots(run):
    # campus-alert marks every layer authority-only (rule 8: public end users get instructions only)
    for snap in run["snaps"].values():
        assert snap["public_map"]["layers"] == {}
        assert snap["public_hotspots"]["hotspots"] == []


def test_top_hotspot_is_near_the_threat_early(run):
    top = max(run["snaps"][20]["hotspots"]["hotspots"], key=lambda h: h["score"])
    assert _dist_m(top["lat"], top["lon"], ca.threat_at(20.0)) < 40


def test_flow_source_points_back_at_the_threat(run):
    pt = run["snaps"][20]["result"].extras["panic.flow_source"]["point"]
    assert _dist_m(pt["lat"], pt["lon"], ca.threat_at(10.0)) < 30


def _score_near(run, k: int, lat: float, lon: float) -> float:
    grid = run["platform"].runtime.grid
    row, col, inside = grid.cell_of(np.array([lat]), np.array([lon]))
    assert bool(inside[0])
    return float(run["snaps"][k]["result"].score[int(row[0]), int(col[0])])


def test_room_button_override_starts_at_60s(run):
    btn = next(x for x in run["scenario"].posts if x.input_id == "room_button")
    lat, lon = btn.body["lat"], btn.body["lon"]
    assert _score_near(run, 58, lat, lon) < 1.0
    assert _score_near(run, 62, lat, lon) == 1.0


def test_police_override_shows_up_and_clears(run):
    lat, lon = run["police"].body["lat"], run["police"].body["lon"]
    assert _score_near(run, 148, lat, lon) < 1.0
    assert _score_near(run, 152, lat, lon) == 1.0
    assert _score_near(run, 200, lat, lon) == 1.0
    assert _score_near(run, 204, lat, lon) < 1.0


def test_spoof_checks_separate_the_sim_spoofers(run):
    """Each input's spec plausibility (panic: people on foot) flags exactly the scripted spoofers."""
    from app.engine.trust import suspicious_users_by_input

    p = run["platform"]
    spec = p.runtime.spec
    track_inputs = [i for i in spec.inputs if i.kind == "push" and i.shape in ("point", "track")]
    events = {i.id: p.engine.source.events(p.app_id, i.id, T0 - 1, T0 + ca.DURATION) for i in track_inputs}
    suspects = suspicious_users_by_input(events, {i.id: i.plausibility for i in track_inputs})
    flagged = {s.user_id for s in suspects if s.flags}
    assert flagged == set(run["scenario"].truth["spoof_users"])
