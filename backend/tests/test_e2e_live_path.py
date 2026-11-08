"""End to end, build step 7 (the live path), on recorded LLM fixtures.

The dev hands the Compiler the Flyway intent and raw inputs (specs/compile/flyway.json: no tiers,
operators or plausibility) through `POST /v1/apps/flyway/compile`. The proposal waits at
`pending_confirmation` until the dev confirms it (`POST /activate`, what the Builder's "Confirm and
activate" button calls). Then the Flyway replay goes through the ingest API on a SimClock, the
engine ticks, and the POI Labeler, woken by its own scheduler, names a stopover from a code-made
dwell candidate.

The same GPS-track input compiles differently by meaning: dwell (+ density) when birds land and
linger, flow_source only when the same `tags` input's meaning says owners move away from a danger
(and flow_source for Campus Alert's panic phones). No network, no live LLM: every model turn is
replayed from tests/fixtures/llm/{compiler_flyway,compiler_fleeing_tags,compiler_campus_alert,
poi_labeler_flyway}.json.
"""

import asyncio
import json
import math

import pytest
from fake_llm import SPECS, FakeAsyncOpenAI
from fastapi.testclient import TestClient
from sim import flyway
from sim.common import LocalFrame

from app.agents.llm import LlmRouter
from app.agents.poi_labeler import PoiLabelerScheduler
from app.core import clock as clock_mod
from app.core.clock import SimClock
from app.core.config import Settings
from app.core.db import Db
from app.core.models import CompileRequest
from app.engine.tick import TickEngine
from app.main import DbEventSource, create_app
from app.tools import geo_tools

T0 = 1_800_000_000.0
TICK_S = 60
NEAR_M = 150.0
COMPILE_REQUEST = json.loads((SPECS / "compile" / "flyway.json").read_text(encoding="utf-8"))


def _as_compile_request(spec: dict) -> dict:
    """What a dev would hand the Compiler for a hand-written spec: no tiers/operators/plausibility."""
    keep = ("id", "kind", "shape", "meaning", "fields")
    return {
        "intent": spec["intent"],
        "grid": spec["grid"],
        "inputs": [{k: i[k] for k in keep if k in i} for i in spec["inputs"]],
    }


CAMPUS_REQUEST = _as_compile_request(json.loads((SPECS / "campus-alert.json").read_text(encoding="utf-8")))
FLEEING_MEANING = "Phones stream their GPS position about every second while their owners move away from a danger."
# The very same tags input (id, kind, shape, fields); only the dev's meaning differs.
FLEEING_REQUEST = {
    **COMPILE_REQUEST,
    "inputs": [{**COMPILE_REQUEST["inputs"][0], "meaning": FLEEING_MEANING}],
}


def _router(script: str) -> LlmRouter:
    fake = FakeAsyncOpenAI(script)
    return LlmRouter(Settings(_env_file=None), clients={"nvidia": fake, "openrouter": fake})


def _ops(spec: dict, input_id: str) -> set[str]:
    return {op["op"] for i in spec["inputs"] if i["id"] == input_id for op in i["operators"]}


@pytest.fixture(scope="module")
def run():
    clock = SimClock(start=T0)
    prev = clock_mod.set_clock(clock)
    mp = pytest.MonkeyPatch()
    # osm_features_near would call Overpass: answer with a lakeshore instead (no network).
    water = {"type": "way", "id": 1, "center": {"lat": 32.825, "lon": -96.7225}, "tags": {"natural": "water"}}
    lake = {"elements": [water]}
    mp.setattr(geo_tools, "OVERPASS", geo_tools.OverpassClient(fetch=lambda q: lake, min_interval_s=0))
    try:
        db = Db(":memory:")
        engine = TickEngine(DbEventSource(db), clock=clock)
        poi_llm = _router("poi_labeler_flyway")
        scheduler = PoiLabelerScheduler(db, engine, llm=poi_llm)
        compile_llm = _router("compiler_flyway")
        app = create_app(db=db, engine=engine, llm=compile_llm, poi_labeler_scheduler=scheduler, autostart_engine=False)
        client = TestClient(app)

        r = client.post("/v1/apps/flyway/compile", json=COMPILE_REQUEST)
        assert r.status_code == 200, r.text
        proposed = r.json()
        stored_before_confirm = db.get_app("flyway")

        r = client.post("/v1/apps/flyway/activate")  # the dev's confirmation in the Builder
        assert r.status_code == 200, r.text
        keys = r.json()

        sc = flyway.load_replay(flyway.DEFAULT_REPLAY)
        statuses, i = [], 0
        end = int(flyway.DURATION_S)
        headers = {"X-Ingest-Key": keys["ingest_key"]}
        for k in range(0, end + 1, TICK_S):
            clock.set(T0 + k)
            while i < len(sc.posts) and sc.posts[i].sim_t <= k:
                post = sc.posts[i]
                url = f"/v1/apps/flyway/inputs/{post.input_id}/events"
                statuses.append(client.post(url, json=post.stamped(T0), headers=headers).status_code)
                i += 1
            engine.tick_once()

        asyncio.run(scheduler.check_once())  # one POI Labeler wake-up, as the live loop would do

        app.state.llm = _router("compiler_campus_alert")  # the same API, another app's intent
        r = client.post("/v1/apps/campus-alert/compile", json=CAMPUS_REQUEST)
        assert r.status_code == 200, r.text
        campus = r.json()

        app.state.llm = _router("compiler_fleeing_tags")  # same tags input, a fleeing meaning
        r = client.post("/v1/apps/flee-check/compile", json=FLEEING_REQUEST)
        assert r.status_code == 200, r.text
        yield {
            "fleeing": r.json(),
            "db": db,
            "engine": engine,
            "proposed": proposed,
            "stored_before_confirm": stored_before_confirm,
            "campus": campus,
            "statuses": statuses,
            "sc": sc,
            "poi_calls": poi_llm._provider("nvidia").client.calls,
        }
    finally:
        mp.undo()
        clock_mod.set_clock(prev)


def test_the_stored_compile_request_is_the_flyway_spec_without_the_compilers_choices():
    flyway_spec = json.loads((SPECS / "flyway.json").read_text(encoding="utf-8"))
    CompileRequest.model_validate(COMPILE_REQUEST)  # the body tools/demo --live POSTs
    assert COMPILE_REQUEST == _as_compile_request(flyway_spec)
    assert not any({"tier", "operators", "plausibility"} & set(i) for i in COMPILE_REQUEST["inputs"])


def test_compile_proposes_a_spec_and_waits_for_the_dev(run):
    spec = run["proposed"]
    assert spec["app_id"] == "flyway" and spec["status"] == "pending_confirmation"
    assert run["stored_before_confirm"].status == "pending_confirmation"
    assert [i["id"] for i in spec["inputs"]] == [i["id"] for i in COMPILE_REQUEST["inputs"]]
    for got, given in zip(spec["inputs"], COMPILE_REQUEST["inputs"], strict=True):
        assert got["meaning"] == given["meaning"]  # the dev's words, verbatim


def test_the_same_tags_input_becomes_dwell_or_flow_source_by_its_meaning(run):
    lingering = _ops(run["proposed"], "tags")
    assert lingering == {"dwell", "density"}  # birds land and linger
    fleeing_spec = run["fleeing"]
    assert fleeing_spec["status"] == "pending_confirmation"
    (fleeing_tags,) = fleeing_spec["inputs"]
    assert fleeing_tags["meaning"] == FLEEING_MEANING
    for k in ("id", "kind", "shape", "fields"):
        assert fleeing_tags[k] == COMPILE_REQUEST["inputs"][0][k]  # otherwise the same input
    ops = [(o["op"], o["weight"]) for o in fleeing_tags["operators"]]
    assert ops == [("flow_source", 1.0)]  # owners move away from a danger: flow_source only
    pl = fleeing_tags["plausibility"]
    assert pl["flag_straight_line"] is False and pl["flag_constant_speed"] is False


def test_a_gps_track_becomes_flow_source_for_campus_alert(run):
    panic = _ops(run["campus"], "panic")
    assert "flow_source" in panic and "dwell" not in panic
    shapes = {i["id"]: i["shape"] for i in [*run["proposed"]["inputs"], *run["campus"]["inputs"]]}
    assert shapes["tags"] == shapes["panic"] == "track"


def test_compiler_proposes_bird_plausibility_stiff_wind_labels_and_visibility(run):
    spec = run["proposed"]
    by_id = {i["id"]: i for i in spec["inputs"]}
    pl = by_id["tags"]["plausibility"]
    assert by_id["tags"]["tier"] == "flexible"
    assert pl["max_speed_mps"] >= 20 and not pl["flag_straight_line"] and not pl["flag_constant_speed"]
    wind = by_id["wind"]
    assert wind["tier"] == "stiff" and wind["connector"]["preset"] == "open_meteo"
    labels = {lb["label"] for lb in spec["outputs"]["poi_labels"]["label_set"]}
    assert labels == {"watering hole", "roost", "feeding ground", "stopover"}
    layers = {ol["id"]: ol["visibility"] for ol in spec["outputs"]["layers"]}
    assert layers["score"] == "authority"
    assert spec["routing"]["enabled"] is False


def test_confirmation_is_a_dev_change_and_the_replay_is_accepted(run):
    (change,) = run["db"].list_changes("flyway", kind="spec_confirm")
    assert change.actor == "dev" and change.before == "pending_confirmation" and change.after == "active"
    assert run["statuses"] and set(run["statuses"]) == {202}


def test_poi_labeler_names_a_true_stopover_from_a_dwell_candidate(run):
    assert run["poi_calls"], "the scheduler never woke the POI Labeler"
    (poi,) = run["db"].list_pois("flyway", status="active")
    assert poi.label in {"stopover", "watering hole"}
    cands = {c.id: c for c in run["engine"].latest("flyway").candidates}
    cand = cands[poi.candidate_id]
    assert cand.source == "dwell_clusters"
    assert (poi.lat, poi.lon, poi.radius_m) == (cand.lat, cand.lon, cand.radius_m)  # copied, never invented
    frame = LocalFrame.from_spec(json.loads((SPECS / "flyway.json").read_text(encoding="utf-8")))
    sites = run["sc"].truth["stopovers"]
    nearest = min(math.dist(frame.to_xy(poi.lat, poi.lon), frame.to_xy(s["lat"], s["lon"])) for s in sites)
    assert nearest < NEAR_M


def test_the_poi_is_authority_only(run):
    # Its candidate comes from authority-only tag layers, so the POI is authority too (rule 8).
    (poi,) = run["db"].list_pois("flyway", status="active")
    assert poi.visibility == "authority"
    assert run["db"].list_pois("flyway", visibility="public") == []


def test_tools_demo_live_replays_the_recorded_compile_through_the_cache():
    """`tools/demo flyway --live`: the backend's compile cache is seeded from the recorded clean run,
    so the real /compile route answers without any model call and still traces the Compiler."""
    from tools import demo_server

    no_llm = _router({"scenario": "none", "description": "must not be called", "steps": []})
    app = create_app(db=Db(":memory:"), llm=no_llm, compiler_cache=demo_server.seeded_cache(["flyway"]))
    with TestClient(app) as client:
        r = client.post("/v1/apps/flyway/compile", json=COMPILE_REQUEST)
    assert r.status_code == 200, r.text
    spec = r.json()
    assert spec["status"] == "pending_confirmation"
    assert _ops(spec, "tags") == {"dwell", "density"}
    assert no_llm._provider("nvidia").client.calls == []
    traces = [t for t in app.state.db.list_trace("flyway") if t.agent == "compiler"]
    assert traces and traces[-1].why == "cache hit, no LLM" and traces[-1].action == "output"
