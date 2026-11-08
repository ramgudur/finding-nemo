"""`connectors/engine.py`: polls pull-input connectors and writes validated events.

A connector config is config, not code (rule 10): this only interprets the `type`/`url`/
`poll_s`/`config`/`preset` fields of `InputSpec.connector`, never runs anything a config
supplies. The config is always read fresh from `db.get_app` each poll, so a dev or Scout
changing it (`tools.compiler_tools.create_connector`) takes effect on the running engine
without a restart or any separate cache to go stale.

Poll scheduling is in-process and in-memory, the same safe-default philosophy as the tick
engine's own state: a restart just starts polling again from "now".

Locating documents without coordinates (optional): with a `locate` callable, RSS items that have
no `<georss:point>` are kept, de-duplicated against documents already written and against
documents already tried this process (so each one costs at most one extraction), capped at
`MAX_LOCATE_PER_POLL` per poll (rate budget), and passed to
`locate(app, input, item)`. It returns `(lat, lon, confidence)` or None; a location outside the
app's bbox is dropped. `make_extract_locator` builds one from the Scout's per-article extraction and
a geocoder, both injected, so this module never imports agents. Without `locate`, behavior is
unchanged: items without a point are skipped, never guessed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.api.ingest import _check_fields
from app.connectors import json_api, rss, url_guard
from app.core import clock as clock_mod
from app.core.db import Db
from app.core.models import AppSpec, ConnectorConfig, Event, InputSpec

log = logging.getLogger(__name__)

FETCH_TIMEOUT_S = 10.0
CHECK_INTERVAL_S = 1.0
DEFAULT_CONFIDENCE = 0.6
MAX_LOCATE_PER_POLL = 5
MAX_CONCURRENT_POLLS = 4  # due polls run concurrently, at most this many at once

# (app spec, input, item {doc_id, title, description, ts}) -> (lat, lon, confidence) or None
Locator = Callable[[AppSpec, InputSpec, dict[str, Any]], Awaitable[tuple[float, float, float] | None]]


@dataclass
class PollResult:
    app_id: str
    input_id: str
    ok: bool
    events_written: int = 0
    error: str | None = None


def _rss_event(item: dict[str, Any], source_id: str, lat: float, lon: float, confidence: float) -> dict[str, Any]:
    return {
        "user_id": source_id,
        "lat": lat,
        "lon": lon,
        "ts": item["ts"],
        "doc_id": item["doc_id"],
        "confidence": confidence,
    }


def _rss_events(raw_text: str, config: dict[str, Any], now: float) -> list[dict[str, Any]]:
    source_id = str(config.get("source_id", "rss"))
    confidence = float(config.get("default_confidence", DEFAULT_CONFIDENCE))
    return [_rss_event(i, source_id, i["lat"], i["lon"], confidence) for i in rss.parse_items(raw_text, now)]


def _in_bbox(spec: AppSpec, lat: float, lon: float) -> bool:
    b = spec.grid.bbox
    return b.min_lat <= lat <= b.max_lat and b.min_lon <= lon <= b.max_lon


async def _located_rss_events(
    db: Db,
    spec: AppSpec,
    inp: InputSpec,
    raw_text: str,
    now: float,
    locate: Locator,
    attempted: set[str] | None = None,
) -> list[dict[str, Any]]:
    """RSS events, plus items without a point that `locate` places inside the bbox."""
    assert inp.connector is not None  # noqa: S101 - only called for pull inputs with a connector
    config = inp.connector.config
    source_id = str(config.get("source_id", "rss"))
    confidence = float(config.get("default_confidence", DEFAULT_CONFIDENCE))
    items = rss.parse_items(raw_text, now, include_unlocated=True)
    out = [_rss_event(i, source_id, i["lat"], i["lon"], confidence) for i in items if i["lat"] is not None]
    seen = {
        e.model_extra["doc_id"]
        for e in db.list_events(spec.app_id, inp.id)
        if e.user_id == source_id and e.model_extra and e.model_extra.get("doc_id")
    }
    tried = attempted if attempted is not None else set()
    todo = [i for i in items if i["lat"] is None and i["doc_id"] and i["doc_id"] not in seen | tried]
    for item in todo[:MAX_LOCATE_PER_POLL]:
        tried.add(item["doc_id"])  # irrelevant / unplaceable / failed: not retried this process
        try:
            found = await locate(spec, inp, item)
        except Exception:  # noqa: BLE001 - one document must not drop the rest of the poll
            log.exception("locating %s/%s doc %s failed", spec.app_id, inp.id, item["doc_id"])
            continue
        if found is None:
            continue
        lat, lon, conf = found
        if _in_bbox(spec, lat, lon):
            out.append(_rss_event(item, source_id, lat, lon, max(0.0, min(1.0, conf))))
    return out


def make_extract_locator(
    extract: Callable[[AppSpec, InputSpec, str, str], Awaitable[Any]],
    geocode: Callable[[str, AppSpec], Awaitable[tuple[float, float] | None]],
) -> Locator:
    """A Locator from per-article extraction (returns an object with relevant / lat / lon / place /
    confidence, e.g. agents.scout.ArticleExtract) and a geocoder for place names. Irrelevant
    documents and unplaceable ones are dropped; coordinates are never guessed."""

    async def locate(spec: AppSpec, inp: InputSpec, item: dict[str, Any]) -> tuple[float, float, float] | None:
        ex = await extract(spec, inp, item.get("title", ""), item.get("description", ""))
        if ex is None or not ex.relevant:
            return None
        if ex.lat is not None and ex.lon is not None:
            return float(ex.lat), float(ex.lon), float(ex.confidence)
        if ex.place:
            point = await geocode(ex.place, spec)
            if point is not None:
                return float(point[0]), float(point[1]), float(ex.confidence)
        return None

    return locate


def _json_api_events(
    raw_json: dict[str, Any], preset: str | None, config: dict[str, Any], now: float
) -> list[dict[str, Any]]:
    spec = json_api.resolve_preset(preset, config)
    event = json_api.extract_fields(raw_json, spec, config)
    event["ts"] = datetime.fromtimestamp(now, tz=UTC)
    return [event]


def connector_url(conn: ConnectorConfig) -> str | None:
    """The URL this connector fetches (the preset's, else its own), without rendering the query."""
    preset_url = json_api.resolve_preset(conn.preset, conn.config).get("url") if conn.type == "json_api" else None
    return preset_url or conn.url


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
    url_guard.check_url(url)  # every poll: an agent may have chosen this URL
    resp = await client.get(url, params=params, timeout=FETCH_TIMEOUT_S)  # no redirects (httpx default)
    resp.raise_for_status()
    return resp.json() if conn.type == "json_api" else resp.text


def fetch_raw_sync(client: httpx.Client, conn: ConnectorConfig) -> Any:
    """For a one-shot check (`tools.compiler_tools.test_connector`) outside the async
    poll loop -- tool handlers are synchronous (`tools/registry.py`)."""
    url, params = _request(conn)
    url_guard.check_url(url)
    resp = client.get(url, params=params, timeout=FETCH_TIMEOUT_S)  # no redirects (httpx default)
    resp.raise_for_status()
    return resp.json() if conn.type == "json_api" else resp.text


def events_from_raw(conn: ConnectorConfig, raw: Any, now: float) -> list[dict[str, Any]]:
    if conn.type == "rss":
        return _rss_events(raw, conn.config, now)
    if conn.type == "json_api":
        return _json_api_events(raw, conn.preset, conn.config, now)
    raise ValueError(f"connector type {conn.type!r} has no fetcher")


def write_events(db: Db, app_id: str, inp: InputSpec, raw_events: list[dict[str, Any]], now: float) -> PollResult:
    # A poll can see the same document again (an unchanged feed, an overlapping window);
    # skip a (user_id, doc_id) already written for this input, and within this batch too.
    seen_docs = {
        (e.user_id, e.model_extra["doc_id"])
        for e in db.list_events(app_id, inp.id)
        if e.model_extra and e.model_extra.get("doc_id")
    }
    written, errors = 0, []
    for raw in raw_events:
        try:
            event = Event.model_validate(raw)
        except Exception as e:  # noqa: BLE001 - one bad item must not drop the rest
            errors.append(f"invalid event: {e}")
            continue
        doc_id = (event.model_extra or {}).get("doc_id")
        if doc_id and (event.user_id, doc_id) in seen_docs:
            continue
        reason = _check_fields(inp.fields, event.model_extra or {})
        if reason is not None:
            errors.append(reason)
            continue
        db.insert_event(app_id, inp.id, event, received_at=now)
        if doc_id:
            seen_docs.add((event.user_id, doc_id))
        written += 1
    if written == 0 and errors:
        return PollResult(app_id, inp.id, ok=False, error="; ".join(errors[:3]))
    return PollResult(app_id, inp.id, ok=True, events_written=written)


def check_connector_sync(client: httpx.Client, conn: ConnectorConfig, now: float) -> list[dict[str, Any]]:
    """Fetch once and parse, without writing anything -- what `test_connector` needs to
    honestly say it saw at least one item."""
    raw = fetch_raw_sync(client, conn)
    return events_from_raw(conn, raw, now)


async def poll_once(
    client: httpx.AsyncClient,
    db: Db,
    app_id: str,
    inp: InputSpec,
    now: float,
    locate: Locator | None = None,
    spec: AppSpec | None = None,
    attempted: set[str] | None = None,
) -> PollResult:
    assert inp.connector is not None  # noqa: S101 - callers only pass pull inputs with a connector
    try:
        raw = await fetch_raw(client, inp.connector)
        if locate is not None and spec is not None and inp.connector.type == "rss":
            raw_events = await _located_rss_events(db, spec, inp, raw, now, locate, attempted)
        else:
            raw_events = events_from_raw(inp.connector, raw, now)
    except Exception as e:  # noqa: BLE001 - a bad feed must not crash the poll loop
        return PollResult(app_id, inp.id, ok=False, error=f"{type(e).__name__}: {e}")
    return write_events(db, app_id, inp, raw_events, now)


class ConnectorEngine:
    """Polls every active app's pull inputs with a connector, each on its own `poll_s`."""

    def __init__(
        self,
        db: Db,
        client: httpx.AsyncClient | None = None,
        check_interval_s: float = CHECK_INTERVAL_S,
        locate: Locator | None = None,
        max_concurrent_polls: int = MAX_CONCURRENT_POLLS,
    ) -> None:
        self.db = db
        self.max_concurrent_polls = max(1, int(max_concurrent_polls))
        self.locate = locate  # optional: place RSS items that carry no coordinates
        self._attempted: dict[tuple[str, str], set[str]] = {}  # doc_ids already sent to locate
        self.client = client or httpx.AsyncClient()
        self.check_interval_s = check_interval_s
        self._last_polled_at: dict[tuple[str, str], float] = {}
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def check_once(self, now: float | None = None) -> list[PollResult]:
        """Poll every due connector, concurrently (at most `max_concurrent_polls` at once), so one
        slow feed or extraction never delays the others. Every poll is stamped with the same
        Clock `now`; one that raises is logged and dropped without touching the rest. Results
        come back in app/input order."""
        now = clock_mod.now() if now is None else now
        due: list[tuple[AppSpec, InputSpec, tuple[str, str]]] = []
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
                due.append((spec, inp, key))
        if not due:
            return []
        sem = asyncio.Semaphore(self.max_concurrent_polls)

        async def one(spec: AppSpec, inp: InputSpec, key: tuple[str, str]) -> PollResult | None:
            async with sem:
                try:
                    return await poll_once(
                        self.client,
                        self.db,
                        spec.app_id,
                        inp,
                        now,
                        locate=self.locate,
                        spec=spec,
                        attempted=self._attempted.setdefault(key, set()),
                    )
                except Exception:  # noqa: BLE001 - one connector must not stop the others
                    log.exception("connector poll failed for %s/%s", spec.app_id, inp.id)
                    return None

        results = await asyncio.gather(*(one(*d) for d in due))
        return [r for r in results if r is not None]

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
