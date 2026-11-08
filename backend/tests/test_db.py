from datetime import UTC, datetime

import pytest

from app.core.db import Db
from app.core.models import (
    POI,
    AppSpec,
    BBox,
    Change,
    ConnectorConfig,
    Event,
    GridSpec,
    InputField,
    InputSpec,
    OperatorConfig,
    TraceEntry,
)


@pytest.fixture
def db():
    d = Db(":memory:")
    yield d
    d.close()


def make_spec(app_id: str = "campus-alert", status: str = "draft") -> AppSpec:
    return AppSpec(
        app_id=app_id,
        intent="test app",
        status=status,
        grid=GridSpec(cell_m=5.0, bbox=BBox(min_lat=0, min_lon=0, max_lat=0.01, max_lon=0.01)),
        inputs=[
            InputSpec(
                id="panic",
                kind="push",
                shape="track",
                tier="flexible",
                meaning="a panic button press starts a GPS stream",
                fields=[InputField(name="pressed", type="boolean")],
                operators=[OperatorConfig(op="flow_source", weight=1.0)],
            )
        ],
    )


def t(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


# -- apps / inputs ----------------------------------------------------------------


def test_upsert_and_get_app_round_trips(db):
    spec = make_spec()
    db.upsert_app(spec, t=100.0)
    got = db.get_app("campus-alert")
    assert got == spec


def test_upsert_app_preserves_created_at_but_bumps_updated_at(db):
    spec = make_spec()
    db.upsert_app(spec, t=100.0)
    spec.status = "active"
    db.upsert_app(spec, t=200.0)
    row = db._conn.execute("SELECT created_at, updated_at FROM apps WHERE app_id = ?", ("campus-alert",)).fetchone()
    assert row["created_at"] == 100.0
    assert row["updated_at"] == 200.0
    assert db.get_app("campus-alert").status == "active"


def test_get_app_missing_returns_none(db):
    assert db.get_app("nope") is None


def test_list_apps(db):
    db.upsert_app(make_spec("a"), t=1.0)
    db.upsert_app(make_spec("b"), t=2.0)
    assert {a.app_id for a in db.list_apps()} == {"a", "b"}


def test_inputs_table_mirrors_app_spec_and_resyncs_on_update(db):
    spec = make_spec()
    db.upsert_app(spec, t=1.0)
    inputs = db.list_inputs("campus-alert")
    assert len(inputs) == 1
    assert inputs[0].id == "panic"
    assert db.get_input("campus-alert", "panic").tier == "flexible"
    assert db.get_input("campus-alert", "nope") is None

    spec.inputs = []
    db.upsert_app(spec, t=2.0)
    assert db.list_inputs("campus-alert") == []


# -- events -------------------------------------------------------------------------


def test_insert_and_list_events_filters_by_input_and_since(db):
    e1 = Event(user_id="u1", lat=1.0, lon=2.0, ts=t("2026-01-01T00:00:00Z"), pressed=True)
    e2 = Event(user_id="u2", lat=1.1, lon=2.1, ts=t("2026-01-01T00:01:00Z"), pressed=True)
    db.insert_event("campus-alert", "panic", e1, received_at=10.0)
    db.insert_event("campus-alert", "panic", e2, received_at=20.0)
    db.insert_event("campus-alert", "other-input", e1, received_at=10.0)

    assert len(db.list_events("campus-alert")) == 3
    assert len(db.list_events("campus-alert", input_id="panic")) == 2
    since = db.list_events("campus-alert", input_id="panic", since=e1.ts.timestamp())
    assert [e.user_id for e in since] == ["u2"]


# -- connectors ---------------------------------------------------------------------


def test_connector_upsert_update_and_poll_bookkeeping(db):
    cfg = ConnectorConfig(type="rss", url="http://example.com/feed")
    db.upsert_connector("campus-alert", "news", cfg)
    assert db.get_connector("campus-alert", "news") == cfg

    cfg2 = ConnectorConfig(type="rss", url="http://example.com/feed2")
    db.upsert_connector("campus-alert", "news", cfg2)
    assert db.get_connector("campus-alert", "news") == cfg2
    assert db.list_connectors("campus-alert") == [("news", cfg2)]

    db.record_connector_poll("campus-alert", "news", polled_at=5.0, status="ok")
    row = db._conn.execute(
        "SELECT last_polled_at, last_status FROM connectors WHERE app_id = ? AND input_id = ?",
        ("campus-alert", "news"),
    ).fetchone()
    assert row["last_polled_at"] == 5.0
    assert row["last_status"] == "ok"


# -- weights_history ------------------------------------------------------------------


def test_weight_history_tracks_latest_and_full_series(db):
    db.record_weight("campus-alert", "panic.flow_source", 1.0, "flexible", t=10.0, actor="dev", reason="init")
    db.record_weight("campus-alert", "panic.flow_source", 1.1, "flexible", t=20.0, actor="agent", reason="critic bump")
    current = db.current_weight("campus-alert", "panic.flow_source")
    assert current.weight == 1.1
    assert current.actor == "agent"

    history = db.weight_history_since("campus-alert", "panic.flow_source", since=0.0)
    assert [w.weight for w in history] == [1.0, 1.1]
    assert db.weight_history_since("campus-alert", "panic.flow_source", since=15.0) == [
        w for w in history if w.t >= 15.0
    ]


def test_current_weights_covers_multiple_layers(db):
    db.record_weight("app", "a", 1.0, "flexible", t=1.0, actor="dev", reason="init")
    db.record_weight("app", "b", 2.0, "stiff", t=1.0, actor="dev", reason="init")
    db.record_weight("app", "a", 1.5, "flexible", t=2.0, actor="dev", reason="bump")
    current = db.current_weights("app")
    assert current["a"].weight == 1.5
    assert current["b"].weight == 2.0


def test_current_weight_missing_returns_none(db):
    assert db.current_weight("app", "nope") is None


# -- trust --------------------------------------------------------------------------


def test_trust_tracks_last_increase_only_on_increase(db):
    db.set_trust("app", "u1", 0.8, t=10.0)
    r = db.get_trust("app", "u1")
    assert r.score == 0.8 and r.last_increase_at is None  # below the 1.0 default: not an increase

    db.set_trust("app", "u1", 0.5, t=20.0)  # decrease: last_increase_at unchanged
    r = db.get_trust("app", "u1")
    assert r.score == 0.5 and r.last_increase_at is None

    db.set_trust("app", "u1", 0.6, t=30.0)  # increase: last_increase_at advances
    r = db.get_trust("app", "u1")
    assert r.score == 0.6 and r.last_increase_at == 30.0


def test_a_first_write_above_the_default_counts_as_an_increase(db):
    # never-seen users have an implicit trust of 1.0 (engine/trust.py DEFAULT_TRUST)
    r = db.set_trust("app", "u1", 1.0, t=10.0)
    assert r.last_increase_at is None  # exactly the default: no increase happened

    r = db.set_trust("app", "u2", 0.4, t=10.0)
    assert r.last_increase_at is None  # a first-ever decrease must never look like an increase


def test_get_trust_missing_returns_none(db):
    assert db.get_trust("app", "nope") is None


def test_list_trust_covers_every_recorded_user(db):
    assert db.list_trust("app") == {}
    db.set_trust("app", "u1", 0.8, t=1.0)
    db.set_trust("app", "u2", 0.3, t=1.0)
    db.set_trust("app", "u1", 0.9, t=2.0)
    assert db.list_trust("app") == {"u1": 0.9, "u2": 0.3}
    assert db.list_trust("other-app") == {}


# -- pois -----------------------------------------------------------------------------


def test_poi_upsert_get_and_filter(db):
    poi = POI(
        id="p1",
        candidate_id="c1",
        lat=1.0,
        lon=2.0,
        radius_m=5.0,
        label="gathering point",
        confidence=0.9,
        visibility="public",
        status="active",
        created_at=t("2026-01-01T00:00:00Z"),
        updated_at=t("2026-01-01T00:00:00Z"),
    )
    db.upsert_poi("app", poi, t=1.0)
    assert db.get_poi("app", "p1") == poi
    assert db.list_pois("app", visibility="public") == [poi]
    assert db.list_pois("app", visibility="authority") == []
    assert db.list_pois("app", status="retired") == []

    poi.status = "retired"
    db.upsert_poi("app", poi, t=2.0)
    assert db.list_pois("app", status="retired") == [poi]
    assert db.get_poi("app", "missing") is None


# -- trace ----------------------------------------------------------------------------


def test_trace_append_and_filter_since(db):
    e1 = TraceEntry(t=t("2026-01-01T00:00:00Z"), app_id="app", agent="critic", saw="x")
    e2 = TraceEntry(t=t("2026-01-01T00:01:00Z"), app_id="app", agent="critic", saw="y")
    db.add_trace(e1)
    db.add_trace(e2)
    assert db.list_trace("app") == [e1, e2]
    assert db.list_trace("app", since=e1.t.timestamp()) == [e2]
    assert db.list_trace("app", limit=1) == [e1]


# -- changes --------------------------------------------------------------------------


def test_change_add_get_and_filter(db):
    c1 = Change(
        id="c1",
        app_id="app",
        kind="weight",
        target="layer.x",
        reason="bump",
        actor="agent",
        t=t("2026-01-01T00:00:00Z"),
    )
    c2 = Change(
        id="c2",
        app_id="app",
        kind="trust",
        target="u1",
        reason="spoof",
        actor="agent",
        t=t("2026-01-01T00:01:00Z"),
    )
    db.add_change(c1)
    db.add_change(c2)
    assert db.get_change("c1") == c1
    assert db.get_change("missing") is None
    assert db.list_changes("app") == [c1, c2]
    assert db.list_changes("app", kind="trust") == [c2]
    assert db.list_changes("app", since=c1.t.timestamp()) == [c2]


# -- backtests --------------------------------------------------------------------------


def test_backtest_add_get_list(db):
    from app.core.models import Backtest

    bt = Backtest(
        id="bt1",
        kind="single",
        candidates_tested=1,
        candidates_valid=1,
        passed=True,
        device="cpu",
        elapsed_ms=5.0,
        t=t("2026-01-01T00:00:00Z"),
    )
    db.add_backtest("app", bt)
    assert db.get_backtest("app", "bt1") == bt
    assert db.get_backtest("app", "missing") is None
    assert db.list_backtests("app") == [bt]
    assert db.list_backtests("other-app") == []


# -- keys ------------------------------------------------------------------------------


def test_keys_are_created_unique_and_checked(db):
    keys = db.create_keys("app", t=1.0)
    assert keys.ingest_key != keys.authority_key
    assert db.check_ingest_key("app", keys.ingest_key) is True
    assert db.check_ingest_key("app", "wrong") is False
    assert db.check_authority_key("app", keys.authority_key) is True
    assert db.check_authority_key("app", keys.ingest_key) is False


def test_keys_missing_app_never_matches(db):
    assert db.check_ingest_key("nope", "anything") is False
    assert db.check_authority_key("nope", "anything") is False


def test_create_keys_overwrites_existing_pair(db):
    first = db.create_keys("app", t=1.0)
    second = db.create_keys("app", t=2.0)
    assert first != second
    assert db.check_ingest_key("app", first.ingest_key) is False
    assert db.check_ingest_key("app", second.ingest_key) is True


@pytest.mark.parametrize("bad_key", [None, 123, True, [], {}, "", "ééé"])
def test_key_checks_never_raise_on_non_string_empty_or_non_ascii_keys(db, bad_key):
    db.create_keys("app", t=1.0)
    assert db.check_ingest_key("app", bad_key) is False
    assert db.check_authority_key("app", bad_key) is False


def test_key_checks_accept_a_non_ascii_key_that_actually_matches(db):
    keys = db.create_keys("app", t=1.0)
    # the stored key is always URL-safe ASCII, but the comparison itself must not choke
    # on a non-ASCII candidate even when it's wrong
    assert db.check_ingest_key("app", keys.ingest_key + "é") is False


# -- concurrency (#71: unlocked reads interleaving with writes on the shared connection) --


def test_concurrent_writes_and_reads_never_corrupt_the_shared_connection(db):
    """Regression for #71: a reader thread (the tick) and writer threads (ingest)
    hitting the single shared sqlite3 connection at once used to raise
    sqlite3.InterfaceError and surface as a false "not found"."""
    import threading

    spec = make_spec()
    db.upsert_app(spec, t=0.0)
    n_writers, events_per_writer = 8, 50
    errors: list[BaseException] = []
    stop = threading.Event()

    def write(i: int) -> None:
        try:
            for j in range(events_per_writer):
                e = Event(user_id=f"u{i}-{j}", lat=1.0, lon=2.0, ts=t("2026-01-01T00:00:00Z"))
                db.insert_event("campus-alert", "panic", e, received_at=float(j))
        except BaseException as exc:  # noqa: BLE001 - captured to fail the test, not crash the thread
            errors.append(exc)

    def read() -> None:
        try:
            while not stop.is_set():
                db.get_app("campus-alert")
                db.list_events("campus-alert", "panic")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    readers = [threading.Thread(target=read) for _ in range(4)]
    writers = [threading.Thread(target=write, args=(i,)) for i in range(n_writers)]
    for r in readers:
        r.start()
    for w in writers:
        w.start()
    for w in writers:
        w.join()
    stop.set()
    for r in readers:
        r.join()

    assert errors == []
    assert len(db.list_events("campus-alert", "panic")) == n_writers * events_per_writer
    assert db.get_app("campus-alert") == spec
