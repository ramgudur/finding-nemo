"""`POST /v1/apps/{app}/webhooks/{input}` (#222): another service's JSON, mapped by config."""

import json

import pytest
from fastapi.testclient import TestClient

from app.core.db import Db
from app.main import create_app

BBOX = {"min_lat": 0.0, "min_lon": 0.0, "max_lat": 0.01, "max_lon": 0.01}

WEBHOOK = {
    "items_path": "reports",
    "lat": "location.lat",
    "lon": "location.lon",
    "ts": "seen_at",
    "ts_format": "iso",
    "user_id": "reporter",
    "fields": {"confidence": "conf"},
}


def _spec(**over) -> dict:
    return {
        "app_id": "test-app",
        "intent": "find where people report things",
        "grid": {"cell_m": 10, "bbox": BBOX},
        "inputs": [
            {
                "id": "pings",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "a ping means someone reported something",
                "fields": [{"name": "confidence", "type": "number", "required": False}],
                "operators": [{"op": "density", "weight": 1.0}],
                "webhook": over.get("webhook", WEBHOOK),
            },
            {
                "id": "nohook",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "a push input with no webhook mapping",
                "operators": [{"op": "density", "weight": 1.0}],
            },
            {
                "id": "weather",
                "kind": "pull",
                "shape": "field",
                "tier": "stiff",
                "meaning": "wind speed",
                "connector": {"type": "json_api", "preset": "open_meteo"},
            },
        ],
        "outputs": {"layers": [{"id": "score", "visibility": "public"}]},
    }


@pytest.fixture
def app():
    return create_app(db=Db(":memory:"), autostart_engine=False)


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def keys(client):
    client.post("/v1/apps", json=_spec())
    body = client.post("/v1/apps/test-app/activate").json()
    return body


def _post(client, ingest_key, body, input_id="pings", **kw):
    return client.post(
        f"/v1/apps/test-app/webhooks/{input_id}",
        content=json.dumps(body),
        headers={"X-Ingest-Key": ingest_key, "Content-Type": "application/json"},
        **kw,
    )


def _payload(*reports) -> dict:
    return {"reports": list(reports)}


def _report(**over):
    r = {"location": {"lat": 0.004, "lon": 0.006}, "seen_at": "2026-01-01T00:00:00Z", "reporter": "u1", "conf": 0.8}
    r.update(over)
    return r


def test_valid_webhook_delivers_mapped_events(client, keys):
    r = _post(client, keys["ingest_key"], _payload(_report(), _report(conf=0.5)))
    assert r.status_code == 202, r.text
    body = r.json()
    assert body == {"received": 2, "accepted": 2, "rejected": []}


def test_items_path_empty_treats_the_body_as_one_item(client, keys):
    spec = _spec(webhook={**WEBHOOK, "items_path": ""})
    with TestClient(create_app(db=Db(":memory:"), autostart_engine=False)) as c:
        c.post("/v1/apps", json=spec)
        key = c.post("/v1/apps/test-app/activate").json()["ingest_key"]
        r = _post(c, key, _report())
    assert r.status_code == 202
    assert r.json() == {"received": 1, "accepted": 1, "rejected": []}


def test_list_index_in_path(client, keys):
    spec = _spec(webhook={**WEBHOOK, "lat": "location.0", "lon": "location.1", "user_id": None, "fields": {}})
    with TestClient(create_app(db=Db(":memory:"), autostart_engine=False)) as c:
        c.post("/v1/apps", json=spec)
        key = c.post("/v1/apps/test-app/activate").json()["ingest_key"]
        r = _post(c, key, _payload({"location": [0.004, 0.006], "seen_at": "2026-01-01T00:00:00Z"}))
    assert r.status_code == 202
    assert r.json() == {"received": 1, "accepted": 1, "rejected": []}


@pytest.mark.parametrize(
    ("ts_format", "value"),
    [("epoch_s", 1_767_225_600), ("epoch_ms", 1_767_225_600_000), ("iso", "2026-01-01T00:00:00Z")],
)
def test_ts_formats(client, ts_format, value):
    spec = _spec(webhook={**WEBHOOK, "ts_format": ts_format})
    with TestClient(create_app(db=Db(":memory:"), autostart_engine=False)) as c:
        c.post("/v1/apps", json=spec)
        key = c.post("/v1/apps/test-app/activate").json()["ingest_key"]
        r = _post(c, key, _payload(_report(seen_at=value)))
    assert r.status_code == 202
    assert r.json()["accepted"] == 1


def test_ts_none_uses_receive_time(client, keys):
    spec = _spec(webhook={**WEBHOOK, "ts": None})
    with TestClient(create_app(db=Db(":memory:"), autostart_engine=False)) as c:
        c.post("/v1/apps", json=spec)
        key = c.post("/v1/apps/test-app/activate").json()["ingest_key"]
        report = _report()
        del report["seen_at"]
        r = _post(c, key, _payload(report))
    assert r.status_code == 202
    assert r.json()["accepted"] == 1


def test_a_bad_item_is_rejected_without_failing_the_others(client, keys):
    bad = _report()
    del bad["location"]  # lat/lon path won't resolve
    r = _post(client, keys["ingest_key"], _payload(_report(), bad, _report()))
    assert r.status_code == 202
    body = r.json()
    assert body["received"] == 3 and body["accepted"] == 2
    assert body["rejected"] == [{"index": 1, "reason": "path 'location.lat' not found"}]


def test_undeclared_mapped_field_rejects_every_item(client, keys):
    spec = _spec(webhook={**WEBHOOK, "fields": {"not_declared": "conf"}})
    with TestClient(create_app(db=Db(":memory:"), autostart_engine=False)) as c:
        c.post("/v1/apps", json=spec)
        key = c.post("/v1/apps/test-app/activate").json()["ingest_key"]
        r = _post(c, key, _payload(_report()))
    assert r.status_code == 202
    body = r.json()
    assert body["accepted"] == 0 and "undeclared" in body["rejected"][0]["reason"]


def test_rejected_list_is_capped_at_20(client, keys):
    bad = _report()
    del bad["location"]
    r = _post(client, keys["ingest_key"], _payload(*([bad] * 30)))
    assert r.status_code == 202
    body = r.json()
    assert body["received"] == 30 and body["accepted"] == 0
    assert len(body["rejected"]) == 20


def test_query_param_key_works(client, keys):
    r = client.post(
        "/v1/apps/test-app/webhooks/pings",
        params={"key": keys["ingest_key"]},
        content=json.dumps(_payload(_report())),
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 202


def test_wrong_key_is_401(client, keys):
    r = _post(client, "wrong", _payload(_report()))
    assert r.status_code == 401


def test_missing_key_is_401(client, keys):
    r = client.post("/v1/apps/test-app/webhooks/pings", content=json.dumps(_payload(_report())))
    assert r.status_code == 401


def test_unknown_app_is_401_no_keys_exist():
    # same precedent as /events: the key check runs first, and an app that never existed never
    # has a matching key, so this is a 401, not a 404
    with TestClient(create_app(db=Db(":memory:"), autostart_engine=False)) as c:
        r = c.post("/v1/apps/nope/webhooks/pings", content=json.dumps(_payload()), headers={"X-Ingest-Key": "x"})
    assert r.status_code == 401


def test_unknown_input_is_404(client, keys):
    r = _post(client, keys["ingest_key"], _payload(_report()), input_id="nope")
    assert r.status_code == 404


def test_inactive_app_is_401_no_keys_exist_yet():
    with TestClient(create_app(db=Db(":memory:"), autostart_engine=False)) as c:
        c.post("/v1/apps", json=_spec())
        r = c.post("/v1/apps/test-app/webhooks/pings", content=json.dumps(_payload()), headers={"X-Ingest-Key": "x"})
    assert r.status_code == 401  # no keys exist yet for a draft app, same as /events


def test_pull_input_is_409(client, keys):
    r = _post(client, keys["ingest_key"], _payload(_report()), input_id="weather")
    assert r.status_code == 409


def test_push_input_without_webhook_mapping_is_409(client, keys):
    r = _post(client, keys["ingest_key"], _payload(_report()), input_id="nohook")
    assert r.status_code == 409


def test_body_over_1mb_is_413(client, keys):
    huge = _payload(*([_report()] * 1))
    huge["padding"] = "x" * 1_100_000
    r = _post(client, keys["ingest_key"], huge)
    assert r.status_code == 413


def test_body_over_1mb_is_413_even_without_a_content_length_header(client, keys):
    # a generator body is sent chunked, with no Content-Length -- the cap must still be enforced
    # on the stream itself, not only by trusting the header
    huge = _payload(*([_report()] * 1))
    huge["padding"] = "x" * 1_100_000
    data = json.dumps(huge).encode()

    def chunks():
        for i in range(0, len(data), 4096):
            yield data[i : i + 4096]

    r = client.post(
        "/v1/apps/test-app/webhooks/pings",
        content=chunks(),
        headers={"X-Ingest-Key": keys["ingest_key"], "Content-Type": "application/json"},
    )
    assert r.status_code == 413


def test_over_500_items_is_413(client, keys):
    r = _post(client, keys["ingest_key"], _payload(*([_report()] * 501)))
    assert r.status_code == 413


def test_invalid_json_is_422(client, keys):
    r = client.post(
        "/v1/apps/test-app/webhooks/pings",
        content="not json",
        headers={"X-Ingest-Key": keys["ingest_key"], "Content-Type": "application/json"},
    )
    assert r.status_code == 422


def test_items_path_not_found_is_422(client, keys):
    r = client.post(
        "/v1/apps/test-app/webhooks/pings",
        content=json.dumps({"something_else": []}),
        headers={"X-Ingest-Key": keys["ingest_key"], "Content-Type": "application/json"},
    )
    assert r.status_code == 422


def test_only_the_mapped_fields_are_stored_not_the_raw_body(client, keys, app):
    # an unmapped raw key must never leak into the stored event (rule 8: raw body never stored)
    r = _post(client, keys["ingest_key"], _payload(_report(internal_note="should never be stored")))
    assert r.status_code == 202
    (event,) = app.state.db.list_events("test-app", "pings")
    dumped = event.model_dump_json()
    assert "internal_note" not in dumped and "should never be stored" not in dumped
    assert event.user_id == "u1" and event.model_extra == {"confidence": 0.8}


def test_item_outside_the_grid_bbox_is_rejected_without_failing_the_others(client, keys):
    far = _report(location={"lat": 40.0, "lon": 0.006})
    r = _post(client, keys["ingest_key"], _payload(_report(), far, _report()))
    assert r.status_code == 202
    body = r.json()
    assert body["received"] == 3 and body["accepted"] == 2
    assert body["rejected"] == [{"index": 1, "reason": "position outside the app's area"}]


def test_items_path_not_found_names_the_path(client, keys):
    r = _post(client, keys["ingest_key"], {"something_else": []})
    assert r.status_code == 422
    assert r.json()["detail"] == "items_path: path 'reports' not found"


def test_numeric_string_coordinates_are_accepted_like_events(client, keys):
    # Same lax coercion as POST /events (Event.model_validate): "0.004" is a number.
    r = _post(client, keys["ingest_key"], _payload(_report(location={"lat": "0.004", "lon": "0.006"})))
    assert r.status_code == 202 and r.json()["accepted"] == 1
    ev = client.post(
        "/v1/apps/test-app/inputs/pings/events",
        json={"lat": "0.004", "lon": "0.006", "ts": "2026-01-01T00:00:00Z"},
        headers={"X-Ingest-Key": keys["ingest_key"]},
    )
    assert ev.status_code == 202


def test_validation_errors_are_one_short_line(client, keys):
    r = _post(client, keys["ingest_key"], _payload(_report(location={"lat": "north", "lon": 0.006})))
    assert r.status_code == 202
    (rej,) = r.json()["rejected"]
    assert rej["reason"].startswith("lat: ")
    assert chr(10) not in rej["reason"] and "http" not in rej["reason"] and "north" not in rej["reason"]
