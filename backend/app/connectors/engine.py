"""`connectors/engine.py`: polls pull-input connectors and writes validated events.

A connector config is config, not code (rule 10): this only interprets the `type`/`url`/
`poll_s`/`config`/`preset` fields of `InputSpec.connector`, never runs anything a config
supplies. The config is always read fresh from `db.get_app` each poll, so a dev or Scout
changing it (`tools.compiler_tools.create_connector`) takes effect on the running engine
without a restart or any separate cache to go stale.

Poll scheduling is in-process and in-memory, the same safe-default philosophy as the tick
engine's own state: a restart just starts polling again from "now".
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.api.ingest import _check_fields
from app.connectors import json_api, rss
from app.core.db import Db
from app.core.models import ConnectorConfig, Event, InputSpec

log = logging.getLogger(__name__)

FETCH_TIMEOUT_S = 10.0
CHECK_INTERVAL_S = 1.0
DEFAULT_CONFIDENCE = 0.6


@dataclass
class PollResult:
    app_id: str
    input_id: str
    ok: bool
    events_written: int = 0
    error: str | None = None


def _rss_events(raw_text: str, config: dict[str, Any]) -> list[dict[str, Any]]:
    source_id = str(config.get("source_id", "rss"))
    confidence = float(config.get("default_confidence", DEFAULT_CONFIDENCE))
    out = []
    for item in rss.parse_items(raw_text):
        out.append(
            {
                "user_id": source_id,
                "lat": item["lat"],
                "lon": item["lon"],
                "ts": item["ts"],
                "doc_id": item["doc_id"],
                "confidence": confidence,
            }
        )
    return out


def _json_api_events(
    raw_json: dict[str, Any], preset: str | None, config: dict[str, Any], now: float
) -> list[dict[str, Any]]:
    spec = json_api.resolve_preset(preset, config)
    event = json_api.extract_fields(raw_json, spec, config)
    event["ts"] = datetime.fromtimestamp(now, tz=UTC)
    return [event]


def _request(conn: ConnectorConfig) -> tuple[str, dict[str, Any] | None]:
    """`(url, params)` for this connector. The connector type decides how to read the
    response, not what's in it (rule 10: config, never code, drives the request itself)."""
    spec = json_api.resolve_preset(conn.preset, conn.config) if conn.type == "json_api" else {}
    url = spec.get("url") or conn.url
    if url is None:
        raise ValueError("connector has no url (and no preset with one)")
    params = json_api.render_query(spec.get("query", {}), conn.config) if conn.type == "json_api" else None
    return url, params


async def fetch_raw(client: httpx.AsyncClient, conn: ConnectorConfig) -> Any:
    url, params = _request(conn)
    resp = await client.get(url, params=params, timeout=FETCH_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json() if conn.type == "json_api" else resp.text


def fetch_raw_sync(client: httpx.Client, conn: ConnectorConfig) -> Any:
    """For a one-shot check (`tools.compiler_tools.test_connector`) outside the async
    poll loop -- tool handlers are synchronous (`tools/registry.py`)."""
    url, params = _request(conn)
    resp = client.get(url, params=params, timeout=FETCH_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json() if conn.type == "json_api" else resp.text


def events_from_raw(conn: ConnectorConfig, raw: Any, now: float) -> list[dict[str, Any]]:
    if conn.type == "rss":
        return _rss_events(raw, conn.config)
    if conn.type == "json_api":
        return _json_api_events(raw, conn.preset, conn.config, now)
    raise ValueError(f"connector type {conn.type!r} has no fetcher")


def write_events(db: Db, app_id: str, inp: InputSpec, raw_events: list[dict[str, Any]], now: float) -> PollResult:
    written, errors = 0, []
    for raw in raw_events:
        try:
            event = Event.model_validate(raw)
        except Exception as e:  # noqa: BLE001 - one bad item must not drop the rest
            errors.append(f"invalid event: {e}")
            continue
        reason = _check_fields(inp.fields, event.model_extra or {})
        if reason is not None:
            errors.append(reason)
            continue
        db.insert_event(app_id, inp.id, event, received_at=now)
        written += 1
    if written == 0 and errors:
        return PollResult(app_id, inp.id, ok=False, error="; ".join(errors[:3]))
    return PollResult(app_id, inp.id, ok=True, events_written=written)


def check_connector_sync(client: httpx.Client, conn: ConnectorConfig, now: float) -> list[dict[str, Any]]:
    """Fetch once and parse, without writing anything -- what `test_connector` needs to
    honestly say it saw at least one item."""
    raw = fetch_raw_sync(client, conn)
    return events_from_raw(conn, raw, now)


async def poll_once(client: httpx.AsyncClient, db: Db, app_id: str, inp: InputSpec, now: float) -> PollResult:
    assert inp.connector is not None  # noqa: S101 - callers only pass pull inputs with a connector
    try:
        raw = await fetch_raw(client, inp.connector)
        raw_events = events_from_raw(inp.connector, raw, now)
    except Exception as e:  # noqa: BLE001 - a bad feed must not crash the poll loop
        return PollResult(app_id, inp.id, ok=False, error=f"{type(e).__name__}: {e}")
    return write_events(db, app_id, inp, raw_events, now)


class ConnectorEngine:
    """Polls every active app's pull inputs with a connector, each on its own `poll_s`."""

    def __init__(
        self, db: Db, client: httpx.AsyncClient | None = None, check_interval_s: float = CHECK_INTERVAL_S
    ) -> None:
        self.db = db
        self.client = client or httpx.AsyncClient()
        self.check_interval_s = check_interval_s
        self._last_polled_at: dict[tuple[str, str], float] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def check_once(self, now: float | None = None) -> list[PollResult]:
        now = time.time() if now is None else now
        results = []
        for spec in self.db.list_apps():
            if spec.status != "active":
                continue
            for inp in spec.inputs:
                if inp.kind != "pull" or inp.connector is None:
                    continue
                key = (spec.app_id, inp.id)
                last = self._last_polled_at.get(key, float("-inf"))
                if now - last < inp.connector.poll_s:
                    continue
                self._last_polled_at[key] = now
                try:
                    results.append(await poll_once(self.client, self.db, spec.app_id, inp, now))
                except Exception:  # noqa: BLE001 - one connector must not stop the others
                    log.exception("connector poll failed for %s/%s", spec.app_id, inp.id)
        return results

    async def run(self) -> None:
        self._stop.clear()
        while not self._stop.is_set():
            await self.check_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.check_interval_s)
            except TimeoutError:
                pass

    def start(self) -> asyncio.Task[None]:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self.run())
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None
        await self.client.aclose()
