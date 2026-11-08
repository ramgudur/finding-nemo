"""End-to-end platform sweep with three neutral example apps of different shapes.

Each app goes through the real dev path: POST /compile with a SCRIPTED fake LLM (synthetic tool calls
written in this file, not recorded model output) that proposes a hand-written neutral AppSpec via
`finalize_app`, then POST /activate (dev confirmation), then events through the public ingest API
with the ingest key, ticked deterministically on a manual SimClock. No network, no live LLM, fixed seeds.

A) points-only:   point input -> density; hotspots, persistent_hotspots candidates, routing, visibility.
B) documents + field: located documents -> event_points, station values -> field with a vector field
                  that shifts the document layer; event_clusters candidates, combined score.
C) tracks:        track input -> flow_source + dwell; point estimate near the true source,
                  flow_endpoints and dwell_clusters candidates.
Plus a scripted POI Labeler run (synthetic script) creating POIs from code-made candidates, and
REST + WebSocket visibility checks (rule 8).

Tests marked xfail(strict=True) document gaps found by this sweep that are still open.
"""

from __future__ import annotations

import asyncio
import math
from datetime import UTC, datetime

import numpy as np
import pytest
from fake_llm import FakeAsyncOpenAI
from fastapi.testclient import TestClient

from app.agents.llm import LlmRouter
from app.agents.poi_labeler import run_poi_labeler
from app.core import clock as clock_mod
from app.core.clock import SimClock
from app.core.config import Settings
from app.core.db import Db
from app.engine.grid import Grid
from app.engine.tick import TickEngine
from app.main import DbEventSource, create_app
from app.tools import registry
from app.tools.context import CandidateStore, ToolContext

T0 = 1_800_000_000.0
TICK_S = 2
CELL_M = 20.0
# an arbitrary ~1 km square; nothing about the place matters
BBOX = {"min_lat": 10.0, "min_lon": 20.0, "max_lat": 10.009, "max_lon": 20.00914}
GRID = Grid.from_spec({"cell_m": CELL_M, "bbox": BBOX})


def iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def latlon(x: float, y: float) -> tuple[float, float]:
    lat, lon = GRID.to_latlon(np.array([x]), np.array([y]))
    return float(lat[0]), float(lon[0])


def xy(lat: float, lon: float) -> tuple[float, float]:
    x, y = GRID.to_xy(np.array([lat]), np.array([lon]))
    return float(x[0]), float(y[0])


def dist_m(lat: float, lon: float, p: tuple[float, float]) -> float:
    return math.dist(xy(lat, lon), p)


def synthetic_compile_script(spec: dict) -> dict:
    """A scripted Compiler run: one finalize_app call proposing `spec`, then a closing line.
    Synthetic (written for this test), never presented as model output."""
    return {
        "scenario": f"synthetic_compile_{spec['app_id']}",
        "description": "synthetic: scripted Compiler tool calls for a neutral test app (not recorded model output)",
        "steps": [
            {"response": {"tool_calls": [{"id": "s1", "name": "finalize_app", "arguments": {"spec": spec}}]}},
            {"response": {"content": "Proposed spec ready for confirmation.", "finish_reason": "stop"}},
        ],
    }


def synthetic_labeler_script(label: str, confidence: float = 0.8, source: str | None = None) -> dict:
    """A scripted POI Labeler run: read candidates, label the first one (or the first from `source`)."""
    pick = "candidates.0.id"
    return {
        "scenario": f"synthetic_poi_{label.replace(' ', '_')}",
        "description": "synthetic: scripted POI Labeler tool calls for a neutral test app (not recorded model output)",
        "steps": [
            {"response": {"tool_calls": [{"id": "g1", "name": "get_poi_candidates", "arguments": {}}]}},
            {
                "response": {
                    "tool_calls": [
                        {
                            "id": "p1",
                            "name": "create_poi",
                            "arguments": {
                                "candidate_id": f"${{tool_result:g1:{pick}}}",
                                "label": label,
                                "confidence": confidence,
                                "evidence": [{"kind": "layer_stats", "text": "persistent high values"}],
                            },
                        }
                    ]
                }
            },
            {"response": {"content": "Labeled 1 candidate.", "finish_reason": "stop"}},
        ],
    }


def router(script: dict) -> LlmRouter:
    fake = FakeAsyncOpenAI(script)
    return LlmRouter(Settings(_env_file=None), clients={"nvidia": fake, "openrouter": fake})


def compile_request(spec: dict) -> dict:
    """What a dev types in the Builder: intent, grid, raw inputs (no tiers/operators/plausibility)."""
    keep = ("id", "kind", "shape", "meaning", "fields")
    return {
        "intent": spec["intent"],
        "grid": spec["grid"],
        "inputs": [{k: i[k] for k in keep if k in i} for i in spec["inputs"]],
        "destination_points": (spec.get("routing") or {}).get("destination_points", []),
    }


class Platform:
    """The real API on an in-memory DB with a scripted LLM; the test drives the tick."""

    def __init__(self, spec: dict, clock: SimClock) -> None:
        self.clock = clock
        self.db = Db(":memory:")
        self.engine = TickEngine(DbEventSource(self.db), clock=clock)
        self.fake_llm = router(synthetic_compile_script(spec))
        app = create_app(db=self.db, engine=self.engine, llm=self.fake_llm, autostart_engine=False)
        self.client = TestClient(app)
        self.app_id = spec["app_id"]
        r = self.client.post(f"/v1/apps/{self.app_id}/compile", json=compile_request(spec))
        assert r.status_code == 200, r.text
        self.compiled = r.json()
        assert self.compiled["status"] == "pending_confirmation"
        assert self.app_id not in self.engine.apps  # not live before the dev confirms
        r = self.client.post(f"/v1/apps/{self.app_id}/activate")
        assert r.status_code == 200, r.text
        self.ingest_key = r.json()["ingest_key"]
        self.authority_key = r.json()["authority_key"]
        self.runtime = self.engine.apps[self.app_id]
        self.statuses: list[int] = []

    def label(self, script: dict) -> str | None:
        ctx = ToolContext(self.app_id, self.db, self.engine, agent="poi_labeler", clock=self.clock)
        return asyncio.run(run_poi_labeler(ctx, "scheduled check", router(script)))

    def ws_frames(self, key: str | None = None) -> list[dict]:
        with self.client.websocket_connect(f"/v1/apps/{self.app_id}/stream") as ws:
            frames = []
            if key is not None:
                ws.send_json({"type": "auth", "key": key})
                frames.append(ws.receive_json())
            frames.append(ws.receive_json())
        return frames

    def post(self, input_id: str, body: dict) -> int:
        r = self.client.post(
            f"/v1/apps/{self.app_id}/inputs/{input_id}/events",
            json=body,
            headers={"X-Ingest-Key": self.ingest_key},
        )
        self.statuses.append(r.status_code)
        return r.status_code

    def get(self, path: str, key: str | None = None):
        headers = {"X-Authority-Key": key} if key else {}
        return self.client.get(f"/v1/apps/{self.app_id}{path}", headers=headers)

    def run(self, until_s: int, posts_at) -> None:
        """Tick every TICK_S up to `until_s`; `posts_at(k)` yields (input_id, body) to post at sim second k."""
        for k in range(0, until_s + 1, TICK_S):
            self.clock.set(T0 + k)
            for input_id, body in posts_at(k):
                self.post(input_id, body)
            self.engine.tick_once()

    def latest(self):
        return self.engine.latest(self.app_id)


def _module_clock():
    clock = SimClock(start=T0)
    prev = clock_mod.set_clock(clock)
    return clock, prev


# =============================================================================================
# A) points-only app: point input -> density
# =============================================================================================

A_CENTER = (500.0, 500.0)
A_START = (150.0, 500.0)
A_DEST = (850.0, 500.0)
A_END_S = 40


def spec_a() -> dict:
    dlat, dlon = latlon(*A_DEST)
    return {
        "app_id": "neutral-points",
        "intent": "show where sensor-a reports concentrate and guide entities to a fixed exit point",
        "grid": {"cell_m": CELL_M, "bbox": BBOX},
        "inputs": [
            {
                "id": "reports",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "each report means sensor-a observed an entity at this spot",
                "fields": [{"name": "level", "type": "number", "required": False}],
                "plausibility": {"max_speed_mps": 50},
                "operators": [{"op": "density", "weight": 1.0, "half_life_s": 300, "sigma_m": 30}],
            }
        ],
        "verification": [{"method": "predictive_check", "priority": 1}],
        "outputs": {
            "layers": [],  # every layer authority-only
            "hotspot_threshold": 0.5,
            "poi_labels": {
                "mode": "auto",
                "min_confidence": 0.5,
                "label_set": [
                    {"label": "busy area"},
                    {"label": "marked group", "sensitive": True},
                    {"label": "exit point", "destination": True},
                ],
            },
        },
        "routing": {
            "enabled": True,
            "destination_points": [{"lat": dlat, "lon": dlon, "label": "exit point e"}],
            "impassable_score": 0.8,
            "alpha": 20.0,
            "fallback_text": "No route available.",
        },
    }


def posts_a(rng: np.random.Generator):
    reports = []
    for i in range(30):
        k = 2 * (i % 10)  # spread over the first 20 s
        x, y = A_CENTER[0] + rng.normal(0, 15), A_CENTER[1] + rng.normal(0, 15)
        reports.append((k, f"src-{i:02d}", x, y))

    def at(k: int):
        for kk, uid, x, y in reports:
            if kk == k:
                lat, lon = latlon(x, y)
                yield "reports", {"user_id": uid, "lat": lat, "lon": lon, "ts": iso(T0 + k), "level": 1.0}

    return at


@pytest.fixture(scope="module")
def app_a():
    clock, prev = _module_clock()
    try:
        p = Platform(spec_a(), clock)
        p.run(A_END_S, posts_a(np.random.default_rng(11)))
        yield p
    finally:
        clock_mod.set_clock(prev)


def test_a_all_events_accepted(app_a):
    assert app_a.statuses and set(app_a.statuses) == {202}


def test_a_hotspot_sits_on_the_reports(app_a):
    hs = app_a.get("/hotspots", app_a.authority_key).json()["hotspots"]
    assert hs
    top = max(hs, key=lambda h: h["score"])
    assert dist_m(top["lat"], top["lon"], A_CENTER) < 2 * CELL_M
    assert top["contributing_layers"] == ["reports.density"]
    assert top["visibility"] == "authority"


def test_a_persistent_hotspot_candidates_exist(app_a):
    cands = app_a.latest().candidates
    ph = [c for c in cands if c.source == "persistent_hotspots"]
    assert ph, [c.source for c in cands]
    assert dist_m(ph[0].lat, ph[0].lon, A_CENTER) < 2 * CELL_M
    assert ph[0].persisted_ticks > 0  # followed across ticks


def test_a_instruction_routes_around_the_hotspot(app_a):
    slat, slon = latlon(*A_START)
    r = app_a.client.post(f"/v1/apps/{app_a.app_id}/instruction", json={"lat": slat, "lon": slon})
    assert r.status_code == 200, r.text
    ins = r.json()
    assert ins["guard"]["passed"] is True, ins["guard"]
    assert ins["destination"]["label"] == "exit point e"
    assert len(ins["text"]) <= 360
    score = app_a.latest().score
    # the straight line would cross the hotspot ...
    lr, lc, _ = GRID.xy_to_cell(np.linspace(A_START[0], A_DEST[0], 50), np.full(50, A_START[1]))
    assert score[lr, lc].max() >= 0.5
    # ... the route never enters it
    lat = np.array([pt[0] for pt in ins["route"]])
    lon = np.array([pt[1] for pt in ins["route"]])
    rows, cols, inside = GRID.cell_of(lat, lon)
    assert inside.all()
    assert (score[rows, cols] < 0.5).all()
    assert math.dist(xy(*ins["route"][-1]), A_DEST) < 2 * CELL_M
    assert "marked group" not in ins["text"].lower()
    assert "src-" not in r.text  # no other entity's id leaks into the public answer


def test_a_public_sees_no_authority_data(app_a):
    pub_map = app_a.get("/map?format=geojson").json()
    assert pub_map["layers"] == {}
    assert app_a.get("/hotspots").json()["hotspots"] == []
    assert app_a.get("/trace").status_code == 401
    assert app_a.get("/trace", "wrong").status_code == 401
    raw = app_a.get("/inputs/reports/raw")
    assert raw.status_code != 200 and "src-" not in raw.text
    (frame,) = app_a.ws_frames()
    assert frame["type"] == "tick"
    assert frame["map"]["layers"] == {} and frame["hotspots"]["hotspots"] == []
    bad_ok, bad_tick = app_a.ws_frames("wrong")
    assert bad_ok == {"type": "auth_ok", "authority": False}
    assert bad_tick["map"]["layers"] == {} and bad_tick["hotspots"]["hotspots"] == []


def test_a_ws_authority_frame_unlocks_layers(app_a):
    ok, tick = app_a.ws_frames(app_a.authority_key)
    assert ok == {"type": "auth_ok", "authority": True}
    assert {"score", "reports.density"} <= set(tick["map"]["layers"])
    assert tick["hotspots"]["hotspots"]


def test_a_compile_proposed_then_dev_confirmed(app_a):
    assert app_a.compiled["inputs"][0]["meaning"] == spec_a()["inputs"][0]["meaning"]
    (change,) = app_a.db.list_changes(app_a.app_id, kind="spec_confirm")
    assert change.actor == "dev" and change.before == "pending_confirmation"
    assert any(t.agent == "compiler" for t in app_a.db.list_trace(app_a.app_id))


def test_a_authority_sees_layers_and_trace(app_a):
    m = app_a.get("/map?format=geojson", app_a.authority_key).json()
    assert {"score", "reports.density"} <= set(m["layers"])
    assert app_a.get("/trace", app_a.authority_key).status_code == 200


def test_a_raw_events_for_authority(app_a):
    r = app_a.get("/inputs/reports/raw?since=0", app_a.authority_key)
    assert r.status_code == 200
    assert "src-" in r.text
    assert app_a.get("/inputs/reports/raw?since=0").status_code == 401


# --- POI path: a scripted POI Labeler run (synthetic), plus direct tool guards ----------------


def _poi_ctx(p: Platform, agent: str = "poi_labeler") -> ToolContext:
    return ToolContext(p.app_id, p.db, p.engine, agent=agent, clock=p.clock, candidates=CandidateStore())


@pytest.fixture(scope="module")
def poi_a(app_a):
    prev = clock_mod.set_clock(app_a.clock)
    try:
        cand = app_a.latest().candidates[0]
        out = app_a.label(synthetic_labeler_script("busy area"))
        yield {"cand": cand, "out": out, "pois": app_a.db.list_pois(app_a.app_id)}
    finally:
        clock_mod.set_clock(prev)


def test_a_scripted_labeler_creates_a_poi_from_a_candidate(app_a, poi_a):
    assert poi_a["out"] == "Labeled 1 candidate."
    (poi,) = poi_a["pois"]
    cand = poi_a["cand"]
    assert cand.source == "persistent_hotspots"  # points-only app: no tracks, still a candidate
    assert poi.candidate_id == cand.id and poi.label == "busy area"
    assert (poi.lat, poi.lon, poi.radius_m) == (cand.lat, cand.lon, cand.radius_m)  # never invented
    assert poi.visibility == "authority"  # built from authority-only layers
    assert app_a.db.list_changes(app_a.app_id, kind="poi")
    assert any(t.agent == "poi_labeler" for t in app_a.db.list_trace(app_a.app_id))


def test_a_poi_tools_refuse_other_agents_and_invented_places(app_a, poi_a):
    args = {"candidate_id": poi_a["cand"].id, "label": "busy area", "confidence": 0.9}
    assert not registry.call_tool(_poi_ctx(app_a, "critic"), "create_poi", args)["ok"]
    bad = registry.call_tool(_poi_ctx(app_a), "create_poi", {**args, "candidate_id": "invented"})
    assert not bad["ok"]


def test_a_public_instruction_never_names_the_authority_poi(app_a, poi_a):
    slat, slon = latlon(*A_START)
    ins = app_a.client.post(f"/v1/apps/{app_a.app_id}/instruction", json={"lat": slat, "lon": slon}).json()
    assert "busy area" not in ins["text"].lower()


def test_a_pois_endpoint_filters_by_visibility(app_a, poi_a):
    pid = poi_a["pois"][0].id
    authority = app_a.get("/pois", app_a.authority_key)
    assert authority.status_code == 200
    assert pid in authority.text
    public = app_a.get("/pois")
    assert public.status_code == 200
    assert pid not in public.text


# --- Compiler-side check -----------------------------------------------------------------------


def test_compile_keeps_the_devs_inputs_verbatim():
    """Was a strict xfail (GAP found by this sweep); fixed in #164: finalize_app pins the dev's inputs."""
    clock, prev = _module_clock()
    try:
        spec = spec_a()
        dev_req = compile_request(spec)
        base = spec["inputs"][0]
        invented = {
            **base,
            "id": "invented",
            "meaning": "an input the dev never declared",
            "operators": [{"op": "density", "weight": 0.5}],
        }
        proposed = {
            **spec,
            "app_id": "neutral-drift",
            "inputs": [{**base, "meaning": "a reworded meaning the dev never wrote"}, invented],
        }
        db = Db(":memory:")
        eng = TickEngine(DbEventSource(db), clock=clock)
        llm = router(synthetic_compile_script(proposed))
        c = TestClient(create_app(db=db, engine=eng, llm=llm, autostart_engine=False))
        r = c.post("/v1/apps/neutral-drift/compile", json=dev_req)
        if r.status_code == 200:
            got = r.json()["inputs"]
            assert [i["id"] for i in got] == [i["id"] for i in dev_req["inputs"]]
            assert [i["meaning"] for i in got] == [i["meaning"] for i in dev_req["inputs"]]
        else:
            assert r.status_code == 422
    finally:
        clock_mod.set_clock(prev)


# =============================================================================================
# B) documents + field app
# =============================================================================================

B_DOC = (300.0, 600.0)
B_STATIONS = {  # station id -> (x, y, value)
    "st-w": (100.0, 500.0, 1.0),
    "st-e": (900.0, 500.0, 3.0),
    "st-s": (500.0, 100.0, 2.0),
    "st-n": (500.0, 900.0, 2.0),
}
B_VEC_SPEED, B_VEC_DIR = 1.0, 90.0  # 1 m/s heading east ("to" convention)
B_SHIFT_S = 60.0
B_END_S = 20


def spec_b() -> dict:
    return {
        "app_id": "neutral-docs-field",
        "intent": "combine located documents with gridded station readings and a vector field",
        "grid": {"cell_m": CELL_M, "bbox": BBOX},
        "inputs": [
            {
                "id": "docs",
                "kind": "push",
                "shape": "document",
                "tier": "stiff",
                "meaning": "each document reports something at an already-located place",
                "fields": [
                    {"name": "doc_id", "type": "string", "required": False},
                    {"name": "confidence", "type": "number", "required": False},
                ],
                "operators": [{"op": "event_points", "weight": 0.6, "sigma_m": 40}],
            },
            {
                "id": "stations",
                "kind": "push",
                "shape": "field",
                "tier": "stiff",
                "meaning": "station readings of a scalar value, plus a vector field (speed and heading)",
                "fields": [
                    {"name": "value", "type": "number", "required": True},
                    {"name": "vec_speed", "type": "number", "required": True},
                    {"name": "vec_dir", "type": "number", "required": True},
                ],
                "operators": [
                    {
                        "op": "field",
                        "weight": 0.4,
                        "value_field": "value",
                        "vector_speed_field": "vec_speed",
                        "vector_dir_field": "vec_dir",
                        "vector_dir_convention": "to",
                        "shift_layer": "docs.event_points",
                        "shift_horizon_s": B_SHIFT_S,
                        "diffusion_m2_s": 0.0,
                    }
                ],
            },
        ],
        "verification": [{"method": "cross_input_agreement", "priority": 1}],
        "outputs": {
            "layers": [{"id": "stations.field", "visibility": "public"}],
            "poi_labels": {
                "mode": "on",
                "min_confidence": 0.5,
                "label_set": [{"label": "assembly spot", "destination": True}, {"label": "reported place"}],
            },
        },
    }


def posts_b():
    def at(k: int):
        if k == 0:
            for sid, (x, y, v) in B_STATIONS.items():
                lat, lon = latlon(x, y)
                yield (
                    "stations",
                    {
                        "user_id": sid,
                        "lat": lat,
                        "lon": lon,
                        "ts": iso(T0 + k),
                        "value": v,
                        "vec_speed": B_VEC_SPEED,
                        "vec_dir": B_VEC_DIR,
                    },
                )
        if k in (2, 4, 6):
            lat, lon = latlon(B_DOC[0] + (k - 4), B_DOC[1])
            yield (
                "docs",
                {
                    "user_id": f"source-{k}",
                    "lat": lat,
                    "lon": lon,
                    "ts": iso(T0 + k),
                    "doc_id": f"d{k}",
                    "confidence": 0.9,
                },
            )

    return at


@pytest.fixture(scope="module")
def app_b():
    clock, prev = _module_clock()
    try:
        p = Platform(spec_b(), clock)
        p.run(B_END_S, posts_b())
        yield p
    finally:
        clock_mod.set_clock(prev)


def _layer(res, layer_id: str) -> np.ndarray:
    return res.normalized[res.layer_ids.index(layer_id)]


def test_b_all_events_accepted(app_b):
    assert app_b.statuses and set(app_b.statuses) == {202}
    assert app_b.latest().errors == []


def test_b_event_clusters_candidate_on_the_documents(app_b):
    ec = [c for c in app_b.latest().candidates if c.source == "event_clusters"]
    assert len(ec) == 1, [c.source for c in app_b.latest().candidates]
    assert dist_m(ec[0].lat, ec[0].lon, B_DOC) < CELL_M
    assert ec[0].stats["n_docs"] == 3.0
    assert ec[0].contributing_layers == ["docs.event_points"]


def test_b_field_layer_is_nonzero_and_follows_the_stations(app_b):
    res = app_b.latest()
    f = _layer(res, "stations.field")
    assert (f > 0).all()
    (rw, cw), (re, ce) = (
        (int(v[0]) for v in GRID.xy_to_cell(np.array([x]), np.array([y]))[:2])
        for x, y in ((100.0, 500.0), (900.0, 500.0))
    )
    assert f[re, ce] > f[rw, cw]  # higher reading in the east
    assert res.extras["stations.field"]["n_points"] == 4
    u = res.extras["stations.field"]["vector_u"]
    assert np.allclose(u, B_VEC_SPEED) and np.allclose(res.extras["stations.field"]["vector_v"], 0, atol=1e-9)


def test_b_document_layer_is_carried_along_the_vector_field(app_b):
    d = _layer(app_b.latest(), "docs.event_points")
    assert d.max() > 0
    yy, xx = np.mgrid[0 : d.shape[0], 0 : d.shape[1]]
    cx = float((d * (xx + 0.5)).sum() / d.sum()) * CELL_M
    cy = float((d * (yy + 0.5)).sum() / d.sum()) * CELL_M
    expected = (B_DOC[0] + B_VEC_SPEED * B_SHIFT_S, B_DOC[1])
    assert math.dist((cx, cy), expected) < CELL_M


def test_b_score_combines_both_layers(app_b):
    res = app_b.latest()
    d, f = _layer(res, "docs.event_points"), _layer(res, "stations.field")
    expected = np.clip(0.6 * d + 0.4 * f, 0, 1)
    np.testing.assert_allclose(res.score, expected, atol=1e-6)
    assert d.max() > 0 and f.max() > 0
    m = app_b.get("/map?format=geojson", app_b.authority_key).json()
    assert {"score", "docs.event_points", "stations.field"} <= set(m["layers"])
    assert set(app_b.get("/map?format=geojson").json()["layers"]) == {"stations.field"}


def test_b_ws_public_frame_carries_only_the_public_layer(app_b):
    (frame,) = app_b.ws_frames()
    assert set(frame["map"]["layers"]) == {"stations.field"}
    assert frame["map"]["layers"]["stations.field"]["features"]
    assert all(h["visibility"] == "public" for h in frame["hotspots"]["hotspots"])
    _, tick = app_b.ws_frames(app_b.authority_key)
    assert {"score", "docs.event_points", "stations.field"} <= set(tick["map"]["layers"])


@pytest.fixture(scope="module")
def poi_b(app_b):
    prev = clock_mod.set_clock(app_b.clock)
    try:
        cand = app_b.latest().candidates[0]
        out = app_b.label(synthetic_labeler_script("assembly spot"))
        yield {"cand": cand, "out": out, "pois": app_b.db.list_pois(app_b.app_id)}
    finally:
        clock_mod.set_clock(prev)


def test_b_scripted_labeler_names_a_document_cluster(app_b, poi_b):
    assert poi_b["out"] == "Labeled 1 candidate."
    (poi,) = poi_b["pois"]
    assert poi_b["cand"].source == "event_clusters"
    assert poi.candidate_id == poi_b["cand"].id and poi.label == "assembly spot"
    assert poi.visibility == "public"  # destination label: public by the lead's #113 decision


def test_b_public_pois_include_the_destination_poi(app_b, poi_b):
    r = app_b.get("/pois")
    assert r.status_code == 200
    assert poi_b["pois"][0].id in r.text


# =============================================================================================
# C) tracks app: track input -> flow_source + dwell
# =============================================================================================

C_SOURCE = (420.0, 560.0)
C_DWELL = (200.0, 200.0)
C_END_S = 100
N_MOVERS, N_STAYERS = 40, 6


def spec_c() -> dict:
    return {
        "app_id": "neutral-tracks",
        "intent": "find where entities move away from and where they stay",
        "grid": {"cell_m": CELL_M, "bbox": BBOX},
        "inputs": [
            {
                "id": "tracks",
                "kind": "push",
                "shape": "track",
                "tier": "flexible",
                "meaning": "each entity streams its position every two seconds",
                "plausibility": {"max_speed_mps": 20},
                "operators": [
                    {"op": "flow_source", "weight": 0.5},
                    {"op": "dwell", "weight": 0.5, "radius_m": 25, "min_dwell_s": 40},
                ],
            }
        ],
        "verification": [{"method": "predictive_check", "priority": 1}],
        "outputs": {"layers": []},
    }


def posts_c(rng: np.random.Generator):
    movers = []
    for i in range(N_MOVERS):
        ang = rng.uniform(0, 2 * math.pi)
        r0 = rng.uniform(5, 15)
        speed = rng.uniform(1.5, 2.5)
        movers.append((f"ent-m{i:02d}", ang, r0, speed))
    stayers = [(f"ent-s{i}", rng.normal(0, 6), rng.normal(0, 6)) for i in range(N_STAYERS)]

    def at(k: int):
        for uid, ang, r0, speed in movers:
            r = r0 + speed * k
            x = C_SOURCE[0] + r * math.cos(ang) + rng.normal(0, 2)
            y = C_SOURCE[1] + r * math.sin(ang) + rng.normal(0, 2)
            if 0 < x < GRID.width_m and 0 < y < GRID.height_m:
                lat, lon = latlon(x, y)
                yield "tracks", {"user_id": uid, "lat": lat, "lon": lon, "ts": iso(T0 + k)}
        for uid, ox, oy in stayers:
            lat, lon = latlon(C_DWELL[0] + ox + rng.normal(0, 2), C_DWELL[1] + oy + rng.normal(0, 2))
            yield "tracks", {"user_id": uid, "lat": lat, "lon": lon, "ts": iso(T0 + k)}

    return at


@pytest.fixture(scope="module")
def app_c():
    clock, prev = _module_clock()
    try:
        p = Platform(spec_c(), clock)
        p.run(C_END_S, posts_c(np.random.default_rng(23)))
        yield p
    finally:
        clock_mod.set_clock(prev)


def test_c_all_events_accepted(app_c):
    assert app_c.statuses and set(app_c.statuses) == {202}
    assert app_c.latest().errors == []


def test_c_flow_source_point_estimate_is_near_the_true_source(app_c):
    ex = app_c.latest().extras["tracks.flow_source"]
    assert ex["kind"] == "source"
    assert ex["n_tracks"] >= 30
    pt = ex["point"]
    assert dist_m(pt["lat"], pt["lon"], C_SOURCE) < 30


def test_c_flow_endpoints_candidate_near_the_source(app_c):
    fe = [c for c in app_c.latest().candidates if c.source == "flow_endpoints"]
    assert fe, [c.source for c in app_c.latest().candidates]
    assert min(dist_m(c.lat, c.lon, C_SOURCE) for c in fe) < 30


def test_c_dwell_clusters_candidate_on_the_stayers(app_c):
    dc = [c for c in app_c.latest().candidates if c.source == "dwell_clusters"]
    assert dc, [c.source for c in app_c.latest().candidates]
    assert min(dist_m(c.lat, c.lon, C_DWELL) for c in dc) < 2 * CELL_M
    assert app_c.latest().extras["tracks.dwell"]["clusters"][0]["n_users"] == N_STAYERS
    # movers never form a stay
    assert all(dist_m(c.lat, c.lon, C_DWELL) < 3 * CELL_M for c in dc)


def test_c_score_has_both_layers_and_public_sees_nothing(app_c):
    res = app_c.latest()
    assert _layer(res, "tracks.flow_source").max() > 0
    assert _layer(res, "tracks.dwell").max() > 0
    assert app_c.get("/map?format=geojson").json()["layers"] == {}
    assert app_c.get("/hotspots").json()["hotspots"] == []
    (frame,) = app_c.ws_frames()
    assert frame["map"]["layers"] == {} and frame["hotspots"]["hotspots"] == []
    assert "point" not in str(frame)  # no flow_source point estimate leaks into the public stream
    _, tick = app_c.ws_frames(app_c.authority_key)
    assert {"tracks.flow_source", "tracks.dwell", "score"} <= set(tick["map"]["layers"])


def test_c_routing_off_means_no_instructions(app_c):
    lat, lon = latlon(100.0, 100.0)
    assert app_c.client.post(f"/v1/apps/{app_c.app_id}/instruction", json={"lat": lat, "lon": lon}).status_code == 404
