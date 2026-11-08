"""`POST /v1/apps/{app}/simulate` line mode and `DELETE /simulate`: clusters along a line, one
user-id set per cluster, waves from a background job on the Clock. No network, fixed seeds."""

import asyncio
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.api import simulate as sim_mod
from app.api.simulate import get_sim_rng
from app.core import clock as clock_mod
from app.core.clock import SimClock
from app.core.db import Db
from app.main import create_app

T0 = 1_800_000_000.0
BBOX = {"min_lat": 10.0, "min_lon": 20.0, "max_lat": 10.02, "max_lon": 20.02}
APP = "line-app"
A = {"lat": 10.003, "lon": 20.003}
B = {"lat": 10.017, "lon": 20.017}
M_PER_DEG = 111_320.0


def _spec() -> dict:
    return {
        "app_id": APP,
        "intent": "map where items are reported",
        "grid": {"cell_m": 25, "bbox": BBOX},
        "inputs": [
            {
                "id": "pings",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "a report from our own app",
                "fields": [{"name": "label", "type": "string", "required": True}],
                "operators": [{"op": "density", "weight": 1.0}],
            }
        ],
    }


@pytest.fixture()
def p():
    clock = SimClock(start=T0)
    prev = clock_mod.set_clock(clock)
    try:
        db = Db(":memory:")
        app = create_app(db=db, autostart_engine=False)
        rng = np.random.default_rng(11)
        app.dependency_overrides[get_sim_rng] = lambda: rng
        with TestClient(app) as c:  # one event loop for the whole test, so background jobs live on
            assert c.post("/v1/apps", json=_spec()).status_code == 201
            keys = c.post(f"/v1/apps/{APP}/activate").json()
            yield {"c": c, "db": db, "app": app, "auth": {"X-Authority-Key": keys["authority_key"]}, "clock": clock}
    finally:
        clock_mod.set_clock(prev)


def _body(**over) -> dict:
    return {
        "input_id": "pings",
        "mode": "line",
        "from_point": A,
        "to_point": B,
        "clusters": 5,
        "count": 4,
        "radius_m": 40,
        "duration_s": 0,
        **over,
    }


def _post(p, body, headers=None):
    return p["c"].post(f"/v1/apps/{APP}/simulate", json=body, headers=p["auth"] if headers is None else headers)


def _jobs(p):
    return p["app"].state.sim_line_jobs


def _wait_done(p, timeout=10.0):
    end = time.monotonic() + timeout
    while _jobs(p).get(APP) is not None:
        assert time.monotonic() < end, "line job did not finish"
        time.sleep(0.01)


def _dist_to_line_m(lat, lon):
    ax, ay = A["lon"] * M_PER_DEG * np.cos(np.radians(10.01)), A["lat"] * M_PER_DEG
    bx, by = B["lon"] * M_PER_DEG * np.cos(np.radians(10.01)), B["lat"] * M_PER_DEG
    px, py = lon * M_PER_DEG * np.cos(np.radians(10.01)), lat * M_PER_DEG
    t = np.clip(((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / ((bx - ax) ** 2 + (by - ay) ** 2), 0, 1)
    return np.hypot(px - (ax + t * (bx - ax)), py - (ay + t * (by - ay))), t


def test_one_wave_places_clusters_along_the_line_with_their_own_user_ids(p):
    r = _post(p, _body())
    assert r.status_code == 202
    assert r.json() == {"accepted": 20, "rejected": 0, "user_id_prefix": "line-"}  # no job for duration 0
    events = p["db"].list_events(APP, "pings")
    assert sorted(e.user_id for e in events) == sorted(f"line-{k}-{n}" for k in range(1, 6) for n in range(1, 5))
    along_by_cluster = {}
    for e in events:
        d, t = _dist_to_line_m(e.lat, e.lon)
        assert d <= 40 * 1.25 + 1  # within radius_m (+ the centre's jitter) of the line
        along_by_cluster.setdefault(int(e.user_id.split("-")[1]), []).append(t)
        assert e.model_extra == {"label": "simulated"}
        assert T0 - 10 <= e.ts.timestamp() <= T0
    means = [np.mean(along_by_cluster[k]) for k in range(1, 6)]
    assert means == sorted(means)  # cluster k sits further along the line than cluster k-1
    assert means[0] < 0.1 and means[-1] > 0.9  # end to end
    assert np.allclose(np.diff(means), 0.25, atol=0.08)  # evenly spaced
    (entry,) = [t for t in p["db"].list_trace(APP) if t.action == "simulate"]
    assert "SIMULATED" in entry.why and "line-1-1..line-5-4" in entry.why


def test_a_duration_job_posts_waves_on_the_clock_then_ends(p, monkeypatch):
    clock = p["clock"]

    async def fake_sleep(s):
        clock.advance(s)
        await asyncio.sleep(0)

    monkeypatch.setattr(sim_mod, "_sleep", fake_sleep)
    r = _post(p, _body(duration_s=60))
    assert r.status_code == 202
    body = r.json()
    assert body["accepted"] == 20 and body["duration_s"] == 60 and body["job_id"]
    _wait_done(p)
    events = p["db"].list_events(APP, "pings")
    assert len(events) == 20 * 6  # the first wave + waves at +10..+50 s; +60 s ends the job
    per_user = {}
    for e in events:
        per_user.setdefault(e.user_id, []).append((e.lat, e.lon))
    assert len(per_user) == 20
    for pts in per_user.values():  # each reporter stays near its home point: nothing moves implausibly
        lat, lon = np.array(pts).T
        spread = np.hypot((lat - lat.mean()) * M_PER_DEG, (lon - lon.mean()) * M_PER_DEG * np.cos(np.radians(10)))
        assert spread.max() <= 2 * 2.0 + 0.5  # jitter is min(5 m, 5% of 40 m) = 2 m
    ended = [t for t in p["db"].list_trace(APP) if t.action == "simulate" and "ended" in (t.saw or "")]
    assert len(ended) == 1 and "finished" in ended[0].saw and ended[0].result.startswith("6 wave(s)")


def test_one_job_per_app_a_new_request_replaces_it_and_delete_stops_it(p):
    r1 = _post(p, _body(duration_s=600))  # real asyncio.sleep(10): the job waits between waves
    r2 = _post(p, _body(duration_s=600))
    assert r1.status_code == r2.status_code == 202
    assert r1.json()["job_id"] != r2.json()["job_id"]
    job = _jobs(p).get(APP)
    assert job is not None and job.job_id == r2.json()["job_id"]
    assert p["c"].delete(f"/v1/apps/{APP}/simulate", headers=p["auth"]).status_code == 204
    end = time.monotonic() + 5
    while True:
        ended = [t.saw for t in p["db"].list_trace(APP) if t.action == "simulate" and "ended" in (t.saw or "")]
        if len(ended) == 2 or time.monotonic() > end:
            break
        time.sleep(0.01)
    assert _jobs(p).get(APP) is None
    assert any(r1.json()["job_id"] in s and "replaced" in s for s in ended)
    assert any(r2.json()["job_id"] in s and "stopped by the dev" in s for s in ended)
    assert p["c"].delete(f"/v1/apps/{APP}/simulate", headers=p["auth"]).status_code == 204  # idempotent


def test_delete_needs_the_authority_key(p):
    assert p["c"].delete(f"/v1/apps/{APP}/simulate").status_code == 401
    assert p["c"].delete(f"/v1/apps/{APP}/simulate", headers={"X-Authority-Key": "wrong"}).status_code == 401
    assert p["c"].delete("/v1/apps/nope/simulate", headers=p["auth"]).status_code == 404


@pytest.mark.parametrize(
    "over",
    [
        {"from_point": None},
        {"to_point": {"lat": 0.0, "lon": 0.0}},  # outside the area
        {"from_point": {"lat": 11.0, "lon": 20.01}},
        {"clusters": 1},
        {"clusters": 21},
        {"duration_s": 901},
        {"duration_s": -1},
        {"count": 201},
    ],
)
def test_bad_line_bodies_are_422(p, over):
    body = {k: v for k, v in _body(**over).items() if v is not None}
    assert _post(p, body).status_code == 422
    assert p["db"].list_events(APP) == []


def test_line_needs_the_authority_key_and_never_echoes_events(p):
    assert _post(p, _body(), headers={}).status_code == 401
    r = _post(p, _body())
    assert set(r.json()) == {"accepted", "rejected", "user_id_prefix"}  # counts only (rule 8)


def test_normal_mode_still_needs_a_centre(p):
    body = {"input_id": "pings", "count": 3, "radius_m": 50, "mode": "normal"}
    assert _post(p, body).status_code == 422
