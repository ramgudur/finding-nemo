"""SQLite persistence.

Tables: apps, inputs, events, connectors, weights_history, trust, pois, trace, changes,
backtests, keys, label_proposals. This schema is shared: post on the board when it changes (AGENTS.md).

Pydantic contracts (``AppSpec``, ``InputSpec``, ``Event``, ``POI``, ``TraceEntry``,
``Change``, ``Backtest``) are stored as their own JSON (``model_dump_json`` /
``model_validate_json``), alongside plain columns for the fields callers filter or sort
on. ``weights_history`` and ``trust`` are internal bookkeeping with no contract in
``core/models.py``, so they use the small record types defined here instead.

Time is always a float (POSIX seconds). This module never reads a clock itself — every
write that needs "now" takes ``t`` from the caller, which reads it from
``core/clock.py``. Never log a row from the ``keys`` table.
"""

from __future__ import annotations

import secrets
import sqlite3
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple

from app.core.models import (
    POI as PoiModel,
)
from app.core.models import (
    AppSpec,
    Backtest,
    Change,
    ConnectorConfig,
    Event,
    InputSpec,
    LabelProposal,
    LabelProposalStatus,
    PoiStatus,
    TraceEntry,
    Visibility,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS apps (
    app_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS inputs (
    app_id TEXT NOT NULL REFERENCES apps(app_id),
    input_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    tier TEXT NOT NULL,
    visibility TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    PRIMARY KEY (app_id, input_id)
);

CREATE TABLE IF NOT EXISTS events (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id TEXT NOT NULL,
    input_id TEXT NOT NULL,
    ts REAL NOT NULL,
    received_at REAL NOT NULL,
    event_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_lookup ON events(app_id, input_id, ts);

CREATE TABLE IF NOT EXISTS connectors (
    app_id TEXT NOT NULL,
    input_id TEXT NOT NULL,
    config_json TEXT NOT NULL,
    last_polled_at REAL,
    last_status TEXT,
    PRIMARY KEY (app_id, input_id)
);

CREATE TABLE IF NOT EXISTS weights_history (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id TEXT NOT NULL,
    layer_id TEXT NOT NULL,
    weight REAL NOT NULL,
    tier TEXT NOT NULL,
    t REAL NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    backtest_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_weights_lookup ON weights_history(app_id, layer_id, t);

CREATE TABLE IF NOT EXISTS trust (
    app_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    score REAL NOT NULL,
    updated_at REAL NOT NULL,
    last_increase_at REAL,
    PRIMARY KEY (app_id, user_id)
);

CREATE TABLE IF NOT EXISTS pois (
    app_id TEXT NOT NULL,
    poi_id TEXT NOT NULL,
    visibility TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at REAL NOT NULL,
    poi_json TEXT NOT NULL,
    PRIMARY KEY (app_id, poi_id)
);

CREATE TABLE IF NOT EXISTS trace (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id TEXT NOT NULL,
    t REAL NOT NULL,
    entry_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trace_lookup ON trace(app_id, t);

CREATE TABLE IF NOT EXISTS changes (
    id TEXT PRIMARY KEY,
    app_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    t REAL NOT NULL,
    change_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_changes_lookup ON changes(app_id, t);

CREATE TABLE IF NOT EXISTS backtests (
    id TEXT PRIMARY KEY,
    app_id TEXT NOT NULL,
    t REAL NOT NULL,
    backtest_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_backtests_lookup ON backtests(app_id, t);

CREATE TABLE IF NOT EXISTS label_proposals (
    id TEXT PRIMARY KEY,
    app_id TEXT NOT NULL,
    status TEXT NOT NULL,
    t REAL NOT NULL,
    proposal_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_label_proposals_lookup ON label_proposals(app_id, status, t);

CREATE TABLE IF NOT EXISTS keys (
    app_id TEXT PRIMARY KEY,
    ingest_key TEXT NOT NULL,
    authority_key TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


class WeightRecord(NamedTuple):
    layer_id: str
    weight: float
    tier: str
    t: float
    actor: str
    reason: str
    backtest_id: str | None


class TrustRecord(NamedTuple):
    user_id: str
    score: float
    updated_at: float
    last_increase_at: float | None


class Keys(NamedTuple):
    ingest_key: str
    authority_key: str


def _key_matches(stored: str, provided: Any) -> bool:
    """Constant-time comparison that never raises: anything that isn't a non-empty
    string is just a wrong key (a caller may send anything over the wire). Compared as
    UTF-8 bytes, since ``secrets.compare_digest`` on ``str`` requires ASCII and would
    otherwise raise on a non-ASCII key."""
    if not isinstance(provided, str) or not provided:
        return False
    return secrets.compare_digest(stored.encode(), provided.encode())


class Db:
    """One SQLite database. Not thread-safe across connections; this class serializes
    writes on a lock so a single instance can be shared across threads."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # RLock: a locked method (e.g. set_trust) may call another locked method
        # (get_trust) on the same thread without deadlocking.
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def _query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        """A locked SELECT returning every row. FastAPI runs sync endpoints in a
        threadpool and the tick reads from its own worker thread, so every access to
        the shared connection -- reads included -- goes through this lock."""
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    # -- apps -----------------------------------------------------------------

    def upsert_app(self, spec: AppSpec, t: float) -> None:
        with self._lock:
            row = self._conn.execute("SELECT created_at FROM apps WHERE app_id = ?", (spec.app_id,)).fetchone()
            created_at = row["created_at"] if row else t
            self._conn.execute(
                "INSERT OR REPLACE INTO apps (app_id, status, spec_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (spec.app_id, spec.status, spec.model_dump_json(), created_at, t),
            )
            self._conn.execute("DELETE FROM inputs WHERE app_id = ?", (spec.app_id,))
            self._conn.executemany(
                "INSERT INTO inputs (app_id, input_id, kind, tier, visibility, spec_json) VALUES (?, ?, ?, ?, ?, ?)",
                [(spec.app_id, i.id, i.kind, i.tier, i.visibility, i.model_dump_json()) for i in spec.inputs],
            )
            self._conn.commit()

    def get_app(self, app_id: str) -> AppSpec | None:
        row = self._query_one("SELECT spec_json FROM apps WHERE app_id = ?", (app_id,))
        return AppSpec.model_validate_json(row["spec_json"]) if row else None

    def list_apps(self) -> list[AppSpec]:
        rows = self._query("SELECT spec_json FROM apps ORDER BY created_at")
        return [AppSpec.model_validate_json(r["spec_json"]) for r in rows]

    # -- inputs -----------------------------------------------------------------

    def get_input(self, app_id: str, input_id: str) -> InputSpec | None:
        row = self._query_one("SELECT spec_json FROM inputs WHERE app_id = ? AND input_id = ?", (app_id, input_id))
        return InputSpec.model_validate_json(row["spec_json"]) if row else None

    def list_inputs(self, app_id: str) -> list[InputSpec]:
        rows = self._query("SELECT spec_json FROM inputs WHERE app_id = ? ORDER BY input_id", (app_id,))
        return [InputSpec.model_validate_json(r["spec_json"]) for r in rows]

    # -- events -----------------------------------------------------------------

    def insert_event(self, app_id: str, input_id: str, event: Event, received_at: float) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events (app_id, input_id, ts, received_at, event_json) VALUES (?, ?, ?, ?, ?)",
                (app_id, input_id, event.ts.timestamp(), received_at, event.model_dump_json()),
            )
            self._conn.commit()
            return cur.lastrowid

    def list_events(self, app_id: str, input_id: str | None = None, since: float | None = None) -> list[Event]:
        clauses, params = ["app_id = ?"], [app_id]
        if input_id is not None:
            clauses.append("input_id = ?")
            params.append(input_id)
        if since is not None:
            clauses.append("ts > ?")
            params.append(since)
        rows = self._query(f"SELECT event_json FROM events WHERE {' AND '.join(clauses)} ORDER BY ts", params)
        return [Event.model_validate_json(r["event_json"]) for r in rows]

    # -- connectors ---------------------------------------------------------------

    def upsert_connector(self, app_id: str, input_id: str, config: ConnectorConfig) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO connectors (app_id, input_id, config_json, last_polled_at, last_status) "
                "VALUES (?, ?, ?, NULL, NULL) "
                "ON CONFLICT(app_id, input_id) DO UPDATE SET config_json = excluded.config_json",
                (app_id, input_id, config.model_dump_json()),
            )
            self._conn.commit()

    def get_connector(self, app_id: str, input_id: str) -> ConnectorConfig | None:
        row = self._query_one(
            "SELECT config_json FROM connectors WHERE app_id = ? AND input_id = ?", (app_id, input_id)
        )
        return ConnectorConfig.model_validate_json(row["config_json"]) if row else None

    def list_connectors(self, app_id: str) -> list[tuple[str, ConnectorConfig]]:
        rows = self._query("SELECT input_id, config_json FROM connectors WHERE app_id = ? ORDER BY input_id", (app_id,))
        return [(r["input_id"], ConnectorConfig.model_validate_json(r["config_json"])) for r in rows]

    def record_connector_poll(self, app_id: str, input_id: str, polled_at: float, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE connectors SET last_polled_at = ?, last_status = ? WHERE app_id = ? AND input_id = ?",
                (polled_at, status, app_id, input_id),
            )
            self._conn.commit()

    # -- weights_history ------------------------------------------------------------

    def record_weight(
        self,
        app_id: str,
        layer_id: str,
        weight: float,
        tier: str,
        t: float,
        actor: str,
        reason: str,
        backtest_id: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO weights_history (app_id, layer_id, weight, tier, t, actor, reason, backtest_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (app_id, layer_id, weight, tier, t, actor, reason, backtest_id),
            )
            self._conn.commit()

    def current_weight(self, app_id: str, layer_id: str) -> WeightRecord | None:
        row = self._query_one(
            "SELECT layer_id, weight, tier, t, actor, reason, backtest_id FROM weights_history "
            "WHERE app_id = ? AND layer_id = ? ORDER BY t DESC LIMIT 1",
            (app_id, layer_id),
        )
        return WeightRecord(*row) if row else None

    def current_weights(self, app_id: str) -> dict[str, WeightRecord]:
        rows = self._query(
            "SELECT layer_id, weight, tier, t, actor, reason, backtest_id FROM weights_history "
            "WHERE app_id = ? AND row_id IN "
            "(SELECT MAX(row_id) FROM weights_history WHERE app_id = ? GROUP BY layer_id)",
            (app_id, app_id),
        )
        return {r["layer_id"]: WeightRecord(*r) for r in rows}

    def weight_history_since(self, app_id: str, layer_id: str, since: float) -> list[WeightRecord]:
        rows = self._query(
            "SELECT layer_id, weight, tier, t, actor, reason, backtest_id FROM weights_history "
            "WHERE app_id = ? AND layer_id = ? AND t >= ? ORDER BY t",
            (app_id, layer_id, since),
        )
        return [WeightRecord(*r) for r in rows]

    # -- trust --------------------------------------------------------------------

    def get_trust(self, app_id: str, user_id: str) -> TrustRecord | None:
        row = self._query_one(
            "SELECT user_id, score, updated_at, last_increase_at FROM trust WHERE app_id = ? AND user_id = ?",
            (app_id, user_id),
        )
        return TrustRecord(*row) if row else None

    def set_trust(self, app_id: str, user_id: str, score: float, t: float) -> TrustRecord:
        """Set a user's trust. ``last_increase_at`` only advances when ``score`` rises
        relative to the current value (a never-seen user's implicit value is
        ``DEFAULT_TRUST`` = 1.0, per `engine/trust.py`), so guards can enforce the
        per-5-minute increase cap without mistaking a first-ever decrease for a rise."""
        with self._lock:
            prev = self.get_trust(app_id, user_id)
            baseline = prev.score if prev else 1.0
            last_increase_at = prev.last_increase_at if prev else None
            if score > baseline:
                last_increase_at = t
            self._conn.execute(
                "INSERT INTO trust (app_id, user_id, score, updated_at, last_increase_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(app_id, user_id) DO UPDATE SET score = excluded.score, "
                "updated_at = excluded.updated_at, last_increase_at = excluded.last_increase_at",
                (app_id, user_id, score, t, last_increase_at),
            )
            self._conn.commit()
            return TrustRecord(user_id, score, t, last_increase_at)

    def list_trust(self, app_id: str) -> dict[str, float]:
        """Current trust per user who has a record for this app. A missing user has the
        implicit default (1.0, per `engine/trust.py`); callers merge that in themselves."""
        rows = self._query("SELECT user_id, score FROM trust WHERE app_id = ?", (app_id,))
        return {r["user_id"]: r["score"] for r in rows}

    # -- pois -----------------------------------------------------------------------

    def upsert_poi(self, app_id: str, poi: PoiModel, t: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO pois (app_id, poi_id, visibility, status, updated_at, poi_json) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(app_id, poi_id) DO UPDATE SET visibility = excluded.visibility, "
                "status = excluded.status, updated_at = excluded.updated_at, poi_json = excluded.poi_json",
                (app_id, poi.id, poi.visibility, poi.status, t, poi.model_dump_json()),
            )
            self._conn.commit()

    def get_poi(self, app_id: str, poi_id: str) -> PoiModel | None:
        row = self._query_one("SELECT poi_json FROM pois WHERE app_id = ? AND poi_id = ?", (app_id, poi_id))
        return PoiModel.model_validate_json(row["poi_json"]) if row else None

    def list_pois(
        self, app_id: str, visibility: Visibility | None = None, status: PoiStatus | None = None
    ) -> list[PoiModel]:
        clauses, params = ["app_id = ?"], [app_id]
        if visibility is not None:
            clauses.append("visibility = ?")
            params.append(visibility)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        rows = self._query(f"SELECT poi_json FROM pois WHERE {' AND '.join(clauses)} ORDER BY updated_at", params)
        return [PoiModel.model_validate_json(r["poi_json"]) for r in rows]

    # -- trace ----------------------------------------------------------------------

    def add_trace(self, entry: TraceEntry) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO trace (app_id, t, entry_json) VALUES (?, ?, ?)",
                (entry.app_id, entry.t.timestamp(), entry.model_dump_json()),
            )
            self._conn.commit()

    def list_trace(self, app_id: str, since: float | None = None, limit: int | None = None) -> list[TraceEntry]:
        clauses, params = ["app_id = ?"], [app_id]
        if since is not None:
            clauses.append("t > ?")
            params.append(since)
        sql = f"SELECT entry_json FROM trace WHERE {' AND '.join(clauses)} ORDER BY t"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self._query(sql, params)
        return [TraceEntry.model_validate_json(r["entry_json"]) for r in rows]

    # -- changes --------------------------------------------------------------------

    def add_change(self, change: Change) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO changes (id, app_id, kind, t, change_json) VALUES (?, ?, ?, ?, ?)",
                (change.id, change.app_id, change.kind, change.t.timestamp(), change.model_dump_json()),
            )
            self._conn.commit()

    def get_change(self, change_id: str) -> Change | None:
        row = self._query_one("SELECT change_json FROM changes WHERE id = ?", (change_id,))
        return Change.model_validate_json(row["change_json"]) if row else None

    def list_changes(self, app_id: str, kind: str | None = None, since: float | None = None) -> list[Change]:
        clauses, params = ["app_id = ?"], [app_id]
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if since is not None:
            clauses.append("t > ?")
            params.append(since)
        rows = self._query(f"SELECT change_json FROM changes WHERE {' AND '.join(clauses)} ORDER BY t", params)
        return [Change.model_validate_json(r["change_json"]) for r in rows]

    # -- backtests ------------------------------------------------------------------

    def add_backtest(self, app_id: str, backtest: Backtest) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO backtests (id, app_id, t, backtest_json) VALUES (?, ?, ?, ?)",
                (backtest.id, app_id, backtest.t.timestamp(), backtest.model_dump_json()),
            )
            self._conn.commit()

    def get_backtest(self, app_id: str, backtest_id: str) -> Backtest | None:
        row = self._query_one("SELECT backtest_json FROM backtests WHERE app_id = ? AND id = ?", (app_id, backtest_id))
        return Backtest.model_validate_json(row["backtest_json"]) if row else None

    def list_backtests(self, app_id: str) -> list[Backtest]:
        rows = self._query("SELECT backtest_json FROM backtests WHERE app_id = ? ORDER BY t", (app_id,))
        return [Backtest.model_validate_json(r["backtest_json"]) for r in rows]

    # -- label_proposals ------------------------------------------------------------

    def add_label_proposal(self, app_id: str, proposal: LabelProposal) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO label_proposals (id, app_id, status, t, proposal_json) VALUES (?, ?, ?, ?, ?)",
                (proposal.id, app_id, proposal.status, proposal.t.timestamp(), proposal.model_dump_json()),
            )
            self._conn.commit()

    def get_label_proposal(self, app_id: str, proposal_id: str) -> LabelProposal | None:
        row = self._query_one(
            "SELECT proposal_json FROM label_proposals WHERE app_id = ? AND id = ?", (app_id, proposal_id)
        )
        return LabelProposal.model_validate_json(row["proposal_json"]) if row else None

    def list_label_proposals(self, app_id: str, status: LabelProposalStatus | None = None) -> list[LabelProposal]:
        clauses, params = ["app_id = ?"], [app_id]
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        rows = self._query(
            f"SELECT proposal_json FROM label_proposals WHERE {' AND '.join(clauses)} ORDER BY t, rowid", params
        )
        return [LabelProposal.model_validate_json(r["proposal_json"]) for r in rows]

    def set_label_proposal_status(
        self, app_id: str, proposal_id: str, status: LabelProposalStatus
    ) -> LabelProposal | None:
        """Set a proposal's status; returns the updated proposal, or None if it doesn't exist."""
        with self._lock:
            current = self.get_label_proposal(app_id, proposal_id)
            if current is None:
                return None
            new = current.model_copy(update={"status": status})
            self._conn.execute(
                "UPDATE label_proposals SET status = ?, proposal_json = ? WHERE app_id = ? AND id = ?",
                (status, new.model_dump_json(), app_id, proposal_id),
            )
            self._conn.commit()
            return new

    # -- keys -----------------------------------------------------------------------

    def create_keys(self, app_id: str, t: float) -> Keys:
        """Generate and store a fresh ingest/authority key pair for an app, shown once
        at activation. Overwrites any existing pair for the app."""
        keys = Keys(ingest_key=secrets.token_urlsafe(32), authority_key=secrets.token_urlsafe(32))
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO keys (app_id, ingest_key, authority_key, created_at) VALUES (?, ?, ?, ?)",
                (app_id, keys.ingest_key, keys.authority_key, t),
            )
            self._conn.commit()
        return keys

    def check_ingest_key(self, app_id: str, key: Any) -> bool:
        row = self._query_one("SELECT ingest_key FROM keys WHERE app_id = ?", (app_id,))
        return row is not None and _key_matches(row["ingest_key"], key)

    def check_authority_key(self, app_id: str, key: Any) -> bool:
        row = self._query_one("SELECT authority_key FROM keys WHERE app_id = ?", (app_id,))
        return row is not None and _key_matches(row["authority_key"], key)
