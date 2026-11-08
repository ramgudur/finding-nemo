"""`local_file` connector: one located document per line of a file in data/feeds. No network,
SimClock, a temporary feeds folder."""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.connectors import engine as conn_engine
from app.connectors import local_file
from app.core import clock as clock_mod
from app.core.clock import SimClock
from app.core.db import Db
from app.core.models import AppSpec, BBox, ConnectorConfig
from app.engine.grid import Grid
from app.engine.operators.base import Events
from app.engine.operators.event_points import EventPointsParams, event_points
from app.main import create_app
from app.tools import registry
from app.tools.context import ToolContext

T0 = 1_800_000_000.0
BBOX = {"min_lat": 10.0, "min_lon": 20.0, "max_lat": 10.02, "max_lon": 20.02}
APP = "file-app"

GOOD = """# reports, one per line
10.010, 20.010 | something reported at the middle

10.012, 20.011, 0.5 | a second item, lower confidence
11.5, 20.01 | outside the area
not a line at all
10.01, 20.01, 1.7 | confidence out of range
"""


@pytest.fixture()
def feeds(tmp_path, monkeypatch):
    folder = tmp_path / "feeds"
    folder.mkdir()
    monkeypatch.setattr(local_file, "feeds_dir", lambda: folder)
    return folder


def _conn(name="items.txt", **cfg) -> ConnectorConfig:
    return ConnectorConfig(type="local_file", config={"filename": name, **cfg})


def _spec(conn: ConnectorConfig | None = None, status="active") -> AppSpec:
    return AppSpec.model_validate(
        {
            "app_id": APP,
            "intent": "map reported items",
            "status": status,
            "grid": {"cell_m": 25, "bbox": BBOX},
            "inputs": [
                {
                    "id": "docs",
                    "kind": "pull",
                    "shape": "document",
                    "tier": "stiff",
                    "meaning": "items a person writes into a file",
                    "connector": (conn or _conn()).model_dump(mode="json"),
                    "operators": [{"op": "event_points", "weight": 1.0}],
                }
            ],
        }
    )


# -- config -----------------------------------------------------------------------------


def test_config_defaults_to_a_one_minute_poll_and_takes_only_a_bare_filename():
    assert _conn().poll_s == 60
    assert ConnectorConfig(type="local_file", poll_s=5, config={"filename": "a.txt"}).poll_s == 5


@pytest.mark.parametrize(
    "name",
    [
        "../x.txt",
        "..\\x.txt",
        "a/b.txt",
        "a\\b.txt",
        "/etc/passwd.txt",
        "C:x.txt",
        "x.csv",
        "x.TXT",
        "",
        None,
        7,
        ".hidden.txt",
        "a..b.txt",
        "CON.txt",
        "nul.txt",
        "x" * 70 + ".txt",
    ],
)
def test_bad_or_traversing_filenames_are_rejected(name):
    with pytest.raises(ValidationError):
        ConnectorConfig(type="local_file", config={"filename": name})


def test_url_or_preset_is_rejected():
    with pytest.raises(ValidationError):
        ConnectorConfig(type="local_file", url="https://example.org/a.txt", config={"filename": "a.txt"})
    with pytest.raises(ValidationError):
        ConnectorConfig(type="local_file", preset="open_meteo", config={"filename": "a.txt"})


def test_a_symlink_out_of_the_folder_is_refused(feeds, tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("10.01, 20.01 | x\n")
    link = feeds / "link.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available here")
    with pytest.raises(ValueError, match="inside data/feeds"):
        local_file.read_text({"filename": "link.txt"})


# -- parsing ----------------------------------------------------------------------------


def test_parse_keeps_valid_lines_inside_the_area_and_counts_the_rest():
    out = local_file.parse(GOOD, {"filename": "items.txt"}, T0, BBox(**BBOX))
    assert [(e["lat"], e["lon"], e["confidence"]) for e in out.events] == [(10.01, 20.01, 0.9), (10.012, 20.011, 0.5)]
    assert out.events[0]["text"] == "something reported at the middle"
    assert all(e["user_id"] == "local_file" and e["ts"].timestamp() == T0 for e in out.events)
    assert (out.lines, out.outside, out.malformed) == (5, 1, 2)
    assert out.note() == "2 valid line(s), 1 outside the area, 2 malformed"


def test_doc_id_is_stable_and_ignores_whitespace():
    a = local_file.parse("10.01, 20.01 | same  text\n", {"filename": "a.txt"}, T0).events[0]["doc_id"]
    b = local_file.parse("  10.01,   20.01 |   same text  \n", {"filename": "a.txt"}, T0 + 99).events[0]["doc_id"]
    c = local_file.parse("10.01, 20.01 | other text\n", {"filename": "a.txt"}, T0).events[0]["doc_id"]
    assert a == b and a != c and len(a) == 16


def test_missing_file_has_a_clear_reason(feeds):
    with pytest.raises(ValueError, match="file not found in data/feeds"):
        local_file.check({"filename": "missing.txt"}, T0)


def test_check_needs_one_valid_line(feeds):
    (feeds / "items.txt").write_text("# only a comment\nbroken\n")
    with pytest.raises(ValueError, match="no valid lines"):
        local_file.check({"filename": "items.txt"}, T0)
    (feeds / "items.txt").write_text("50.0, 20.0 | far away\n")
    with pytest.raises(ValueError, match="outside"):
        local_file.check({"filename": "items.txt"}, T0, BBox(**BBOX))


# -- polling ----------------------------------------------------------------------------


def _poll(db, spec, now):
    async def go():
        async with httpx.AsyncClient() as client:  # never used: local_file does no fetch
            return await conn_engine.poll_once(client, db, APP, spec.inputs[0], now, spec=spec)

    return asyncio.run(go())


def test_poll_writes_each_line_once_and_the_status_carries_the_counts(feeds):
    (feeds / "items.txt").write_text(GOOD)
    db = Db(":memory:")
    spec = _spec()
    db.upsert_app(spec, t=T0)
    r1 = _poll(db, spec, T0)
    assert r1.ok and r1.events_written == 2 and r1.dropped_outside == 1
    assert conn_engine.poll_status(r1) == "ok (2 valid line(s), 1 outside the area, 2 malformed)"
    r2 = _poll(db, spec, T0 + 60)  # unchanged file: nothing new
    assert r2.ok and r2.events_written == 0
    with (feeds / "items.txt").open("a") as f:
        f.write("10.015, 20.015 | a new line\n")
    r3 = _poll(db, spec, T0 + 120)
    assert r3.events_written == 1
    events = db.list_events(APP, "docs")
    assert len(events) == 3
    assert {e.model_extra["text"] for e in events} >= {"a new line"}
    assert len({e.model_extra["doc_id"] for e in events}) == 3


def test_poll_of_a_missing_file_fails_softly(feeds):
    db = Db(":memory:")
    spec = _spec()
    db.upsert_app(spec, t=T0)
    r = _poll(db, spec, T0)
    assert not r.ok and "file not found in data/feeds" in conn_engine.poll_status(r)
    conn_engine.record_poll(db, spec, spec.inputs[0], r, T0)
    (entry,) = [t for t in db.list_trace(APP) if t.action == "connector_poll"]
    assert "file items.txt" in entry.saw  # the file name, no path


def test_local_file_events_feed_event_points_like_located_documents(feeds):
    (feeds / "items.txt").write_text(GOOD)
    db = Db(":memory:")
    spec = _spec()
    db.upsert_app(spec, t=T0)
    _poll(db, spec, T0)
    rows = db.list_events(APP, "docs")
    ev = Events.from_records(
        [{"user_id": e.user_id, "t": e.ts.timestamp(), "lat": e.lat, "lon": e.lon, **e.model_extra} for e in rows]
    )
    grid = Grid((BBOX["min_lon"], BBOX["min_lat"], BBOX["max_lon"], BBOX["max_lat"]), 25)

    class C:
        def now(self):
            return T0

    out = event_points(ev, EventPointsParams(), None, C(), grid)
    assert out.extras["n_docs"] == 2
    assert sorted(round(d["w"], 3) for d in out.extras["docs"]) == [0.5, 0.9]  # confidence x reliability
    lowered = event_points(ev, EventPointsParams(), {"local_file": 0.5}, C(), grid)  # source reliability
    assert sorted(round(d["w"], 3) for d in lowered.extras["docs"]) == [0.25, 0.45]


# -- test_connector and the API ---------------------------------------------------------


def _tool_ctx(db, spec, clock):
    from app.engine.tick import InMemoryEventSource, TickEngine

    eng = TickEngine(InMemoryEventSource(), clock=clock)
    return ToolContext(app_id=APP, db=db, engine=eng, agent="scout", clock=clock)


def test_test_connector_passes_with_a_valid_line_and_removes_a_failing_agent_connector(feeds):
    clock = SimClock(T0)
    db = Db(":memory:")
    spec = _spec(status="draft")
    db.upsert_app(spec, t=T0)
    ctx = _tool_ctx(db, spec, clock)
    (feeds / "items.txt").write_text(GOOD)
    r = registry.call_tool(ctx, "test_connector", {"connector_id": "docs"})
    assert r["ok"], r
    assert r["result"]["items_parsed"] == 2 and r["result"]["items_located"] == 2
    (feeds / "items.txt").unlink()
    r = registry.call_tool(ctx, "test_connector", {"connector_id": "docs"})
    assert not r["ok"] and "file not found in data/feeds" in r["error"]
    assert db.get_app(APP).inputs[0].connector is None  # an agent's failing connector is removed (a Change)


def test_sources_status_shows_local_file_with_its_counts(feeds):
    clock = SimClock(T0)
    prev = clock_mod.set_clock(clock)
    try:
        (feeds / "items.txt").write_text(GOOD)
        db = Db(":memory:")
        app = create_app(db=db, autostart_engine=False)
        c = TestClient(app)
        assert c.post("/v1/apps", json=_spec(status="draft").model_dump(mode="json")).status_code == 201
        spec = db.get_app(APP)
        assert {f.name for f in spec.inputs[0].fields} == {"text", "confidence", "doc_id"}  # implied fields
        key = c.post(f"/v1/apps/{APP}/activate").json()["authority_key"]
        spec = db.get_app(APP)
        r = _poll(db, spec, T0)
        conn_engine.record_poll(db, spec, spec.inputs[0], r, T0)
        (row,) = c.get(f"/v1/apps/{APP}/sources/status", headers={"X-Authority-Key": key}).json()
        assert row["via"] == "local_file" and row["poll_s"] == 60
        assert row["last_status"] == "ok (2 valid line(s), 1 outside the area, 2 malformed)"
        assert row["events_total"] == 2
        assert c.get(f"/v1/apps/{APP}/sources/status").status_code == 401  # rule 8
    finally:
        clock_mod.set_clock(prev)


def test_create_app_rejects_a_traversing_filename():
    db = Db(":memory:")
    c = TestClient(create_app(db=db, autostart_engine=False))
    body = _spec(status="draft").model_dump(mode="json")
    body["inputs"][0]["connector"] = {"type": "local_file", "poll_s": 60, "config": {"filename": "../app.db"}}
    assert c.post("/v1/apps", json=body).status_code == 422
