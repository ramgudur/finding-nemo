"""End to end, build step 1: campus_alert sim -> ingest -> tick -> /map and /hotspots.

`StubPlatform` stands in for the ingest and output API until #9 lands; then it is replaced by a
FastAPI TestClient on `app.main` with the same calls (post_event / get_map / get_hotspots).
Everything runs on a manual SimClock: no network, no LLM, fixed seed.
"""

import math

import numpy as np
import pytest
from pydantic import ValidationError
from sim import campus_alert as ca
from sim.common import LocalFrame, load_spec

from app.core.clock import SimClock
from app.core.models import AppSpec, Event
from app.engine.tick import InMemoryEventSource, TickEngine

T0 = 1_800_000_000.0
SEED = 7
TICK_S = 2
SNAPSHOTS = (20, 58, 62, 148, 152, 200, 204)
CLEAR_AT = 202  # the test (not the sim) clears the police marker here


class StubPlatform:
    """Minimal ingest + outputs over the real tick engine. Replace with TestClient once #9 lands."""

    def __init__(self, spec: AppSpec, clock: SimClock) -> None:
        self.spec = spec
        self.source = InMemoryEventSource()
        self.engine = TickEngine(self.source, clock=clock)
        self.runtime = self.engine.add_app(spec)
        self.ingest_key, self.authority_key = "ingest-test", "authority-test"
        self.inputs = {i.id: i for i in spec.inputs}

    def post_event(self, app_id: str, input_id: str, body: dict, ingest_key: str) -> int:
        if ingest_key != self.ingest_key:
            return 401
        inp = self.inputs.get(input_id)
        if app_id != self.spec.app_id or inp is None or inp.kind != "push":
            return 404
        try:
            ev = Event.model_validate(body)
        except ValidationError:
            return 422
        declared = {f.name: f for f in inp.fields}
        extras = ev.model_extra or {}
        if set(extras) - set(declared) or any(f.required and n not in extras for n, f in declared.items()):
            return 422
        self.source.add(
            app_id, input_id, [{"user_id": ev.user_id, "t": ev.ts.timestamp(), "lat": ev.lat, "lon": ev.lon, **extras}]
        )
        return 202

    def _visible(self, layer_id: str, authority_key: str | None) -> bool:
        if authority_key == self.authority_key:
            return True
        return any(o.id == layer_id and o.visibility == "public" for o in self.spec.outputs.layers)

    def get_map(self, app_id: str, authority_key: str | None = None) -> dict:
        r = self.engine.latest(app_id)
        grid = self.runtime.grid
        arrays = {"score": r.score, **dict(zip(r.layer_ids, r.normalized, strict=True))}
        layers = {
            lid: grid.to_geojson(arr, min_value=0.01, property_name="value")
            for lid, arr in arrays.items()
            if self._visible(lid, authority_key)
        }
        return {"app_id": app_id, "t": r.t, "cell_m": grid.cell_m, "bbox": list(grid.bbox), "layers": layers}

    def get_hotspots(self, app_id: str, authority_key: str | None = None) -> dict:
        r = self.engine.latest(app_id)
        hs = [
            {
                "id": h.id,
                "lat": h.lat,
                "lon": h.lon,
                "radius_m": math.sqrt(h.area_m2 / math.pi),
                "score": h.peak,
                "mean": h.mean,
                "area_m2": h.area_m2,
                "n_cells": h.n_cells,
                "persisted_ticks": h.persisted_ticks,
                "contributing_layers": list(h.contributing_layers),
                "visibility": "authority",
            }
            for h in r.hotspots
        ]
        visible = hs if authority_key == self.authority_key else [h for h in hs if h["visibility"] == "public"]
        return {"app_id": app_id, "t": r.t, "hotspots": visible}


RAW = load_spec("campus-alert")
FRAME = LocalFrame.from_spec(RAW)


@pytest.fixture(scope="module")
def run():
    """Activate the spec, replay the whole scenario through ingest, tick every 2 s, keep snapshots."""
    spec = AppSpec.model_validate({**RAW, "status": "active"})  # activation stands in for dev confirmation
    clock = SimClock(start=T0)
    p = StubPlatform(spec, clock)
    sc = ca.build(SEED)
    posts = sorted(sc.posts)
    statuses, snaps, i = [], {}, 0
    police = next(x for x in posts if x.input_id == "police_marker")
    for k in range(0, int(ca.DURATION) + 1, TICK_S):
        while i < len(posts) and posts[i].sim_t <= k:
            statuses.append(p.post_event(spec.app_id, posts[i].input_id, posts[i].stamped(T0), p.ingest_key))
            i += 1
        if k == CLEAR_AT:
            clear = {**police.body, "cleared": True, "ts": ca.iso(T0 + k)}
            statuses.append(p.post_event(spec.app_id, "police_marker", clear, p.ingest_key))
        clock.set(T0 + k)
        p.engine.tick_once()
        if k in SNAPSHOTS:
            r = p.engine.latest(spec.app_id)
            snaps[k] = {
                "result": r,
                "map": p.get_map(spec.app_id, p.authority_key),
                "public_map": p.get_map(spec.app_id),
                "hotspots": p.get_hotspots(spec.app_id, p.authority_key),
                "public_hotspots": p.get_hotspots(spec.app_id),
            }
    return {"platform": p, "scenario": sc, "statuses": statuses, "snaps": snaps, "police": police}


def _dist_m(lat: float, lon: float, xy: tuple[float, float]) -> float:
    return math.dist(FRAME.to_xy(lat, lon), xy)


def test_every_sim_event_is_accepted(run):
    assert run["statuses"] and set(run["statuses"]) == {202}
    assert len(run["statuses"]) == len(run["scenario"].posts) + 1  # + the test's clear event


def test_bad_ingest_key_is_rejected(run):
    p = run["platform"]
    body = run["scenario"].posts[0].stamped(T0)
    assert p.post_event("campus-alert", "panic", body, "wrong") == 401


def test_map_and_hotspots_follow_the_output_contracts(run):
    snap = run["snaps"][62]
    m = snap["map"]
    assert set(m) == {"app_id", "t", "cell_m", "bbox", "layers"}
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
    r = run["snaps"][20]["result"]
    top = max(r.hotspots, key=lambda h: h.peak)
    assert _dist_m(top.lat, top.lon, ca.threat_at(20.0)) < 40


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
