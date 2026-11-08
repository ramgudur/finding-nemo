import pytest
from fastapi.testclient import TestClient

from app.core.clock import SimClock
from app.core.db import Db
from app.engine.tick import TickEngine
from app.main import DbEventSource, create_app

T0 = 1_800_000_000.0


def _iso(t: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(t, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _spec() -> dict:
    return {
        "app_id": "test-app",
        "intent": "find where people gather",
        "grid": {"cell_m": 10, "bbox": {"min_lat": 0.0, "min_lon": 0.0, "max_lat": 0.01, "max_lon": 0.01}},
        "inputs": [
            {
                "id": "pings",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "a ping means someone is here",
                "operators": [{"op": "density", "weight": 1.0}],
            }
        ],
        "outputs": {"layers": [{"id": "score", "visibility": "public"}, {"id": "pings.density"}]},
    }


@pytest.fixture
def setup():
    db = Db(":memory:")
    clock = SimClock(start=T0)
    engine = TickEngine(DbEventSource(db), clock=clock)
    app_obj = create_app(db=db, engine=engine, autostart_engine=False)
    with TestClient(app_obj) as client:
        client.post("/v1/apps", json=_spec())
        keys = client.post("/v1/apps/test-app/activate").json()
        yield client, db, engine, clock, keys


def _post_pings(client, ingest_key, n=5):
    for i in range(n):
        client.post(
            "/v1/apps/test-app/inputs/pings/events",
            json={"user_id": f"u{i}", "lat": 0.005, "lon": 0.005, "ts": _iso(T0)},
            headers={"X-Ingest-Key": ingest_key},
        )


def test_map_before_any_tick_is_404(setup):
    client, db, engine, clock, keys = setup
    r = client.get("/v1/apps/test-app/map", headers={"X-Authority-Key": keys["authority_key"]})
    assert r.status_code == 404


def test_map_unknown_app_is_404(setup):
    client, *_ = setup
    assert client.get("/v1/apps/nope/map").status_code == 404


def test_map_bad_format_is_400(setup):
    client, db, engine, clock, keys = setup
    _post_pings(client, keys["ingest_key"])
    engine.tick_once()
    r = client.get("/v1/apps/test-app/map?format=csv", headers={"X-Authority-Key": keys["authority_key"]})
    assert r.status_code == 400


def test_map_shows_public_layer_without_key_and_all_layers_with_key(setup):
    client, db, engine, clock, keys = setup
    _post_pings(client, keys["ingest_key"])
    engine.tick_once()

    pub = client.get("/v1/apps/test-app/map").json()
    assert set(pub["layers"]) <= {"score"}

    auth = client.get("/v1/apps/test-app/map", headers={"X-Authority-Key": keys["authority_key"]}).json()
    assert "score" in auth["layers"] and "pings.density" in auth["layers"]

    for fc in auth["layers"].values():
        assert fc["type"] == "FeatureCollection"
        for f in fc["features"]:
            assert set(f["properties"]) == {"value", "row", "col"}
            assert f["properties"]["value"] >= 0.01


def test_map_shape_matches_contract(setup):
    client, db, engine, clock, keys = setup
    _post_pings(client, keys["ingest_key"])
    engine.tick_once()
    m = client.get("/v1/apps/test-app/map", headers={"X-Authority-Key": keys["authority_key"]}).json()
    assert set(m) == {"app_id", "t", "cell_m", "bbox", "layers"}
    assert m["app_id"] == "test-app"
    assert m["cell_m"] == 10


def test_hotspots_are_authority_only_for_this_spec(setup):
    client, db, engine, clock, keys = setup
    _post_pings(client, keys["ingest_key"])
    engine.tick_once()

    pub = client.get("/v1/apps/test-app/hotspots").json()
    assert pub["hotspots"] == []

    auth = client.get("/v1/apps/test-app/hotspots", headers={"X-Authority-Key": keys["authority_key"]}).json()
    assert auth["hotspots"]
    for h in auth["hotspots"]:
        assert set(h) == {
            "id", "lat", "lon", "radius_m", "score", "mean", "area_m2",
            "n_cells", "persisted_ticks", "contributing_layers", "visibility",
        }  # fmt: skip
        assert h["visibility"] == "authority"


def test_hotspot_is_public_when_every_contributing_layer_is_public():
    db = Db(":memory:")
    clock = SimClock(start=T0)
    engine = TickEngine(DbEventSource(db), clock=clock)
    app_obj = create_app(db=db, engine=engine, autostart_engine=False)
    spec = _spec()
    spec["outputs"]["layers"] = [
        {"id": "score", "visibility": "public"},
        {"id": "pings.density", "visibility": "public"},
    ]
    with TestClient(app_obj) as client:
        client.post("/v1/apps", json=spec)
        keys = client.post("/v1/apps/test-app/activate").json()
        _post_pings(client, keys["ingest_key"])
        engine.tick_once()
        pub = client.get("/v1/apps/test-app/hotspots").json()
        assert pub["hotspots"]
        assert all(h["visibility"] == "public" for h in pub["hotspots"])


def test_map_with_wrong_authority_key_is_401_not_public(setup):
    client, db, engine, clock, keys = setup
    _post_pings(client, keys["ingest_key"])
    engine.tick_once()
    r = client.get("/v1/apps/test-app/map", headers={"X-Authority-Key": "wrong"})
    assert r.status_code == 401


def test_hotspots_with_wrong_authority_key_is_401_not_public(setup):
    client, db, engine, clock, keys = setup
    _post_pings(client, keys["ingest_key"])
    engine.tick_once()
    r = client.get("/v1/apps/test-app/hotspots", headers={"X-Authority-Key": "wrong"})
    assert r.status_code == 401


def test_hotspots_before_any_tick_is_404(setup):
    client, db, engine, clock, keys = setup
    r = client.get("/v1/apps/test-app/hotspots", headers={"X-Authority-Key": keys["authority_key"]})
    assert r.status_code == 404


# -- GET /pois and GET /inputs/{input}/raw (rule 8) ---------------------------------------


def _poi(poi_id: str, visibility: str, status: str = "active"):
    from datetime import UTC, datetime

    from app.core.models import POI

    now = datetime.fromtimestamp(T0, UTC)
    return POI(
        id=poi_id,
        candidate_id=f"c-{poi_id}",
        lat=0.005,
        lon=0.005,
        radius_m=10.0,
        label=f"label-{poi_id}",
        confidence=0.8,
        visibility=visibility,
        status=status,
        created_at=now,
        updated_at=now,
    )


def test_pois_are_filtered_per_item(setup):
    client, db, engine, clock, keys = setup
    db.upsert_poi("test-app", _poi("pub", "public"), t=T0)
    db.upsert_poi("test-app", _poi("priv", "authority"), t=T0)
    db.upsert_poi("test-app", _poi("old", "public", status="retired"), t=T0)

    public = client.get("/v1/apps/test-app/pois")
    assert public.status_code == 200
    assert [p["id"] for p in public.json()] == ["pub"]

    auth = client.get("/v1/apps/test-app/pois", headers={"X-Authority-Key": keys["authority_key"]})
    assert sorted(p["id"] for p in auth.json()) == ["priv", "pub"]

    assert client.get("/v1/apps/test-app/pois", headers={"X-Authority-Key": "wrong"}).status_code == 401
    assert client.get("/v1/apps/nope/pois").status_code == 404


def test_raw_is_authority_only(setup):
    client, db, engine, clock, keys = setup
    _post_pings(client, keys["ingest_key"], n=3)

    assert client.get("/v1/apps/test-app/inputs/pings/raw").status_code == 401
    assert client.get("/v1/apps/test-app/inputs/pings/raw", headers={"X-Authority-Key": "wrong"}).status_code == 401
    auth = {"X-Authority-Key": keys["authority_key"]}
    r = client.get("/v1/apps/test-app/inputs/pings/raw", headers=auth)
    assert r.status_code == 200
    assert sorted(e["user_id"] for e in r.json()) == ["u0", "u1", "u2"]
    assert client.get("/v1/apps/test-app/inputs/nope/raw", headers=auth).status_code == 404


def test_raw_since_and_limit(setup):
    client, db, engine, clock, keys = setup
    auth = {"X-Authority-Key": keys["authority_key"]}
    for i in range(4):
        client.post(
            "/v1/apps/test-app/inputs/pings/events",
            json={"user_id": f"u{i}", "lat": 0.005, "lon": 0.005, "ts": _iso(T0 + i)},
            headers={"X-Ingest-Key": keys["ingest_key"]},
        )
    since = client.get("/v1/apps/test-app/inputs/pings/raw", params={"since": _iso(T0 + 1)}, headers=auth).json()
    assert [e["user_id"] for e in since] == ["u2", "u3"]
    last = client.get("/v1/apps/test-app/inputs/pings/raw", params={"limit": 1}, headers=auth).json()
    assert [e["user_id"] for e in last] == ["u3"]
