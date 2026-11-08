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
from pydantic import ValidationError

from app.api.ingest import _check_fields
from app.connectors import fields as conn_fields
from app.connectors import json_api, json_list, local_file, rss, url_guard
from app.core import clock as clock_mod
from app.core.config import get_settings
from app.core.db import Db
from app.core.models import AppSpec, BBox, ConnectorConfig, Event, InputField, InputSpec, TraceEntry

log = logging.getLogger(__name__)

FETCH_TIMEOUT_S = 10.0
CHECK_INTERVAL_S = 1.0
DEFAULT_CONFIDENCE = 0.6
MAX_LOCATE_PER_POLL = 5
MAX_CONCURRENT_POLLS = 4  # due polls run concurrently, at most this many at once
DEFAULT_USER_AGENT = "geo-heatmap-platform/1.0"  # some hosts refuse the default python-httpx agent
MAX_STATUS_CHARS = 200

# (app spec, input, item {doc_id, title, description, ts}) -> (lat, lon, confidence) or None
Locator = Callable[[AppSpec, InputSpec, dict[str, Any]], Awaitable[tuple[float, float, float] | None]]


@dataclass
class PollResult:
    app_id: str
    input_id: str
    ok: bool
    events_written: int = 0
    error: str | None = None
    dropped_outside: int = 0  # json_list / local_file items outside the app's area (dropped, not written)
    note: str | None = None  # counts shown in a healthy poll's status ("ok (<note>)"); local_file only


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


def area_points(conn: ConnectorConfig, bbox: BBox | None) -> list[json_api.AreaPoint] | None:
    """The points a `json_api` preset with `area_grid` samples across the app's bbox, or None
    (no such preset, or no bbox known: the config's own lat/lon are then the one point)."""
    if conn.type != "json_api" or conn.preset is None or bbox is None:
        return None
    grid = json_api.area_grid(json_api.resolve_preset(conn.preset, conn.config))
    if grid is None:
        return None
    return json_api.area_points(bbox.min_lat, bbox.min_lon, bbox.max_lat, bbox.max_lon, grid)


def _json_api_events(
    raw_json: Any, preset: str | None, config: dict[str, Any], now: float, points: list[json_api.AreaPoint] | None
) -> list[dict[str, Any]]:
    spec = json_api.resolve_preset(preset, config)
    if points is not None:
        events = json_api.extract_area_events(raw_json, spec, points)
    else:
        events = [json_api.extract_fields(raw_json, spec, config)]
    ts = datetime.fromtimestamp(now, tz=UTC)
    for event in events:
        event["ts"] = ts
    return events


JSON_TYPES = ("json_api", "json_list")  # connector types whose response is read as JSON


def _list_url(conn: ConnectorConfig) -> str | None:
    """A `json_list` URL: the connector's own, else `config.url` (the contract allows either)."""
    if conn.type != "json_list":
        return None
    url = conn.config.get("url")
    return url if isinstance(url, str) and url else None


def connector_url(conn: ConnectorConfig) -> str | None:
    """The URL this connector fetches (the preset's, else its own), without rendering the query."""
    preset_url = json_api.resolve_preset(conn.preset, conn.config).get("url") if conn.type == "json_api" else None
    return preset_url or conn.url or _list_url(conn)


def _request(conn: ConnectorConfig, bbox: BBox | None = None) -> str:
    """The URL this connector fetches, with a preset's rendered query merged into whatever query
    the URL already has: a custom URL's own `?a=b` is kept, never replaced by an empty params
    dict. The connector type decides how to read the response, not what's in it (rule 10:
    config, never code, drives the request itself). A preset with `area_grid` asks for all of
    its points across `bbox` in this one request."""
    spec = json_api.resolve_preset(conn.preset, conn.config) if conn.type == "json_api" else {}
    url = spec.get("url") or conn.url or _list_url(conn)
    if url is None:
        raise ValueError("connector has no url (and no preset with one)")
    if conn.type == "json_api" and conn.preset is None:
        json_api.check_custom_config(conn.config)  # data-only read config: refuse a bad one before fetching
    if conn.type == "json_list":
        json_list.check_config(conn.config)  # the same: an item mapping, data only (rule 10)
    config = conn.config
    points = area_points(conn, bbox)
    if points is not None:
        config = json_api.area_config(config, json_api.area_grid(spec) or {}, points)
    params = json_api.render_query(spec.get("query", {}), config) if conn.type == "json_api" else {}
    return str(httpx.URL(url).copy_merge_params(params)) if params else url


def _headers() -> dict[str, str]:
    """A descriptive User-Agent (`NOMINATIM_USER_AGENT` if set, else a generic one)."""
    return {"User-Agent": get_settings().nominatim_user_agent or DEFAULT_USER_AGENT}


async def fetch_raw(client: httpx.AsyncClient, conn: ConnectorConfig, bbox: BBox | None = None) -> Any:
    url = _request(conn, bbox)
    url_guard.check_url(url)  # every poll: an agent may have chosen this URL
    headers = _headers()
    resp = await client.get(url, headers=headers, timeout=FETCH_TIMEOUT_S)  # redirects followed by hand below
    hops = 0
    while resp.status_code in url_guard.REDIRECT_CODES:  # each hop re-checked (SSRF)
        url = url_guard.next_hop(str(resp.url), resp.headers.get("location"), hops=hops)
        hops += 1
        resp = await client.get(url, headers=headers, timeout=FETCH_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json() if conn.type in JSON_TYPES else resp.text


def fetch_raw_sync(client: httpx.Client, conn: ConnectorConfig, bbox: BBox | None = None) -> Any:
    """For a one-shot check (`tools.compiler_tools.test_connector`) outside the async
    poll loop -- tool handlers are synchronous (`tools/registry.py`)."""
    url = _request(conn, bbox)
    url_guard.check_url(url)
    headers = _headers()
    resp = client.get(url, headers=headers, timeout=FETCH_TIMEOUT_S)  # redirects followed by hand below
    hops = 0
    while resp.status_code in url_guard.REDIRECT_CODES:  # each hop re-checked (SSRF)
        url = url_guard.next_hop(str(resp.url), resp.headers.get("location"), hops=hops)
        hops += 1
        resp = client.get(url, headers=headers, timeout=FETCH_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json() if conn.type in JSON_TYPES else resp.text


def events_from_raw(
    conn: ConnectorConfig,
    raw: Any,
    now: float,
    bbox: BBox | None = None,
    declared: list[InputField] | None = None,
) -> list[dict[str, Any]]:
    """Event bodies from one fetched response. `bbox` and `declared` are read by `json_list` (items
    outside the area dropped, values coerced to their declared types; `json_list.parse`); `bbox`
    also gives a `json_api` preset with `area_grid` its points (pass the bbox the fetch used)."""
    if conn.type == "rss":
        return _rss_events(raw, conn.config, now)
    if conn.type == "json_api":
        return _json_api_events(raw, conn.preset, conn.config, now, area_points(conn, bbox))
    if conn.type == "json_list":
        return json_list.parse(raw, conn.config, now, bbox=bbox, declared=declared).events
    if conn.type == "local_file":
        return local_file.parse(raw, conn.config, now, bbox).events
    raise ValueError(f"connector type {conn.type!r} has no fetcher")


def _event_key(raw: dict[str, Any]) -> tuple[Any, ...] | None:
    try:
        e = Event.model_validate(raw)
    except Exception:  # noqa: BLE001 - an invalid item is reported by write_events, not here
        return None
    return (e.user_id, round(e.ts.timestamp(), 3), round(e.lat, 7), round(e.lon, 7))


def _new_list_events(db: Db, app_id: str, input_id: str, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A `json_list` with its own timestamps lists the same items poll after poll: keep only items
    whose (user_id, ts, lat, lon) isn't stored for this input yet, nor repeated in this batch."""
    keyed = [(_event_key(e), e) for e in events]
    stamps = [k[1] for k, _ in keyed if k is not None]
    if not stamps:
        return events
    seen = {
        (e.user_id, round(e.ts.timestamp(), 3), round(e.lat, 7), round(e.lon, 7))
        for e in db.list_events(app_id, input_id, since=min(stamps) - 1.0)
    }
    out = []
    for key, e in keyed:
        if key is not None:
            if key in seen:
                continue
            seen.add(key)
        out.append(e)
    return out


def _list_poll(
    db: Db, app_id: str, inp: InputSpec, raw: Any, now: float, spec: AppSpec | None
) -> tuple[list[dict[str, Any]], json_list.ListParse]:
    """One `json_list` poll's new event bodies: items mapped, those outside the area dropped (and
    counted), values coerced to their declared types, already-stored items skipped."""
    assert inp.connector is not None  # noqa: S101 - only called for pull inputs with a connector
    declared = conn_fields.with_implied(inp.fields, inp.connector)
    bbox = spec.grid.bbox if spec is not None else None
    parsed = json_list.parse(raw, inp.connector.config, now, bbox=bbox, declared=declared)
    if not parsed.events and parsed.unmapped:
        raise ValueError(f"no item could be mapped ({parsed.unmapped} of {parsed.items}): {parsed.first_error}")
    events = parsed.events
    if inp.connector.config.get("ts"):
        events = _new_list_events(db, app_id, inp.id, events)
    return events, parsed


def write_events(db: Db, app_id: str, inp: InputSpec, raw_events: list[dict[str, Any]], now: float) -> PollResult:
    # A poll can see the same document again (an unchanged feed, an overlapping window);
    # skip a (user_id, doc_id) already written for this input, and within this batch too.
    seen_docs = {
        (e.user_id, e.model_extra["doc_id"])
        for e in db.list_events(app_id, inp.id)
        if e.model_extra and e.model_extra.get("doc_id")
    }
    # The connector's own implied fields count as declared (`connectors/fields.py`), so a spec
    # stored before they were declared automatically still accepts its connector's events.
    declared = conn_fields.with_implied(inp.fields, inp.connector)
    written, errors = 0, []
    for raw in raw_events:
        try:
            event = Event.model_validate(raw)
        except ValidationError as e:  # one bad item must not drop the rest
            # Where and what only: never echo the item's values into a status or trace.
            where = sorted({".".join(str(p) for p in err["loc"]) or "event" for err in e.errors()})
            errors.append(f"invalid event ({', '.join(where)})")
            continue
        except Exception as e:  # noqa: BLE001 - one bad item must not drop the rest
            errors.append(f"invalid event: {type(e).__name__}")
            continue
        doc_id = (event.model_extra or {}).get("doc_id")
        if doc_id and (event.user_id, doc_id) in seen_docs:
            continue
        reason = _check_fields(declared, event.model_extra or {})
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


def check_connector_sync(
    client: httpx.Client, conn: ConnectorConfig, now: float, bbox: BBox | None = None
) -> list[dict[str, Any]]:
    """Fetch once and parse, without writing anything -- what `test_connector` needs to
    honestly say it saw at least one item. With `bbox`, a `json_list` counts only items inside the
    area, and says why when none are."""
    if conn.type == "local_file":  # no fetch: the feeds-folder file, read as text
        return local_file.check(conn.config, now, bbox).events
    raw = fetch_raw_sync(client, conn, bbox)
    if conn.type == "rss":
        # Count every parsed item, located or not: the live poller places items without a point via
        # the locator (extract + geocode), so a normal news feed with no coordinates is a working source.
        return rss.parse_items(raw, now, include_unlocated=True)
    if conn.type == "json_list":
        parsed = json_list.parse(raw, conn.config, now, bbox=bbox)
        if not parsed.events and parsed.outside:
            raise ValueError(f"all {parsed.outside} mapped item(s) are outside the app's area")
        if not parsed.events and parsed.unmapped:
            raise ValueError(f"no item could be mapped ({parsed.unmapped} of {parsed.items}): {parsed.first_error}")
        return parsed.events
    return events_from_raw(conn, raw, now, bbox=bbox)


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
    outside = 0
    if inp.connector.type == "local_file":
        return _file_poll(db, app_id, inp, now, spec)
    try:
        if spec is None and inp.connector.type == "json_api":
            spec = db.get_app(app_id)  # its bbox: a preset with `area_grid` samples across it
        bbox = spec.grid.bbox if spec is not None else None
        raw = await fetch_raw(client, inp.connector, bbox)
        if locate is not None and spec is not None and inp.connector.type == "rss":
            raw_events = await _located_rss_events(db, spec, inp, raw, now, locate, attempted)
        elif inp.connector.type == "json_list":
            raw_events, parsed = _list_poll(db, app_id, inp, raw, now, spec if spec is not None else db.get_app(app_id))
            outside = parsed.outside
        else:
            raw_events = events_from_raw(inp.connector, raw, now, bbox=bbox)
    except Exception as e:  # noqa: BLE001 - a bad feed must not crash the poll loop
        return PollResult(app_id, inp.id, ok=False, error=describe_error(e))
    result = write_events(db, app_id, inp, raw_events, now)
    result.dropped_outside = outside
    if outside:
        log.info("connector %s/%s: %d item(s) outside the area dropped", app_id, inp.id, outside)
    return result


def _file_poll(db: Db, app_id: str, inp: InputSpec, now: float, spec: AppSpec | None) -> PollResult:
    """One `local_file` poll: read the feeds-folder file, keep the lines inside the area, write
    the lines not stored yet (stable doc_id). The counts go into the status note."""
    assert inp.connector is not None  # noqa: S101 - only called for pull inputs with a connector
    try:
        spec = spec if spec is not None else db.get_app(app_id)
        bbox = spec.grid.bbox if spec is not None else None
        parsed = local_file.parse(local_file.read_text(inp.connector.config), inp.connector.config, now, bbox)
    except Exception as e:  # noqa: BLE001 - a missing or unreadable file must not crash the poll loop
        return PollResult(app_id, inp.id, ok=False, error=describe_error(e))
    if not parsed.events:
        return PollResult(
            app_id,
            inp.id,
            ok=False,
            error=f"no valid lines inside the area ({parsed.note()})",
            dropped_outside=parsed.outside,
        )
    result = write_events(db, app_id, inp, parsed.events, now)
    result.dropped_outside = parsed.outside
    if result.ok:
        result.note = parsed.note()
    return result


def describe_error(e: BaseException) -> str:
    """A short, safe reason a poll failed: the HTTP status, or the exception type plus a capped
    message. Never the URL (a custom URL's query may carry a key) and never a response body."""
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}"
    if isinstance(e, httpx.HTTPError):  # connect / read / timeout errors: their text can name the URL
        return type(e).__name__
    if isinstance(e, url_guard.UnsafeUrl):  # the guard's own reasons name at most a host, never a query
        return f"UnsafeUrl: {e}"[:MAX_STATUS_CHARS]
    msg = " ".join(str(e).split())
    return f"{type(e).__name__}: {msg}"[:MAX_STATUS_CHARS]


def poll_status(result: PollResult | None) -> str:
    """What the connector row's `last_status` holds: "ok" or "error: <reason>". No counts, so an
    unchanged healthy (or failing) source keeps the same status from poll to poll."""
    if result is None:
        return "error: internal error"
    if result.ok:
        return f"ok ({result.note})"[:MAX_STATUS_CHARS] if result.note else "ok"
    return f"error: {result.error or 'unknown'}"[:MAX_STATUS_CHARS]


def _is_ok(status: str | None) -> bool:
    return status is not None and (status == "ok" or status.startswith("ok ("))


def record_poll(db: Db, spec: AppSpec, inp: InputSpec, result: PollResult | None, now: float) -> None:
    """Store this poll's status on the connector row (`last_polled_at`, `last_status`). A
    TraceEntry is written only when the status changes to a new error, or recovers from one, so a
    failing feed shows up in the trace once rather than every poll. Agent "scout" (connectors are
    the Scout's area; `AgentName` has no connector value), action "connector_poll"."""
    assert inp.connector is not None  # noqa: S101 - only pull inputs with a connector are polled
    status = poll_status(result)
    previous = db.get_connector_status(spec.app_id, inp.id)
    prev_status = previous[1] if previous is not None else None
    db.upsert_connector(spec.app_id, inp.id, inp.connector)  # the row tracks the live config
    db.record_connector_poll(spec.app_id, inp.id, polled_at=now, status=status)
    failing = not _is_ok(status)
    recovered = not failing and prev_status is not None and not _is_ok(prev_status)
    if not ((failing and status != prev_status) or recovered):
        return
    try:
        host = httpx.URL(connector_url(inp.connector) or "").host or "?"
    except Exception:  # noqa: BLE001 - an unknown preset or a malformed URL
        host = "?"
    if inp.connector.type == "local_file":
        host = f"file {inp.connector.config.get('filename', '?')}"
    written = result.events_written if result is not None else 0
    db.add_trace(
        TraceEntry(
            t=datetime.fromtimestamp(now, tz=UTC),
            app_id=spec.app_id,
            agent="scout",
            saw=f"connector for input {inp.id!r} ({inp.connector.type}, host {host})",
            result=status if failing else f"ok again: {written} event(s) written",
            action="connector_poll",
            why="the source's poll failed; no events arrive from it until it recovers"
            if failing
            else f"recovered after {prev_status}",
        )
    )


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
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()  # set to run a check now instead of after check_interval_s

    def _make_due(self, app_id: str) -> None:
        for key in [k for k in self._last_polled_at if k[0] == app_id]:
            self._last_polled_at.pop(key, None)

    def _backfill(self, app_id: str) -> None:
        self._make_due(app_id)
        self._wake.set()

    def poll_app_now(self, app_id: str) -> bool:
        """Backfill (#267): make every pull connector of `app_id` due and wake the poll loop, so its
        first poll runs right away instead of after the loop's next wait. Never blocks and never
        raises: safe from a sync request handler on another thread. The poll itself is a normal
        one (same URL guard, same `record_poll` status and trace). Returns whether a running loop
        was woken (False when the engine isn't started: the app is then due at its next
        `check_once`)."""
        loop = self._loop
        if loop is not None and self._task is not None and not self._task.done() and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._backfill, app_id)
                return True
            except RuntimeError:  # the loop closed in between
                pass
        self._make_due(app_id)
        return False

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
                result: PollResult | None
                try:
                    result = await poll_once(
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
                    result = None
                try:
                    record_poll(self.db, spec, inp, result, now)
                except Exception:  # noqa: BLE001 - status bookkeeping must never stop polling
                    log.exception("recording the poll of %s/%s failed", spec.app_id, inp.id)
                return result

        results = await asyncio.gather(*(one(*d) for d in due))
        return [r for r in results if r is not None]

    async def run(self) -> None:
        self._stop.clear()
        while not self._stop.is_set():
            self._wake.clear()
            await self.check_once()
            if self._stop.is_set():
                break
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.check_interval_s)
            except TimeoutError:
                pass

    def start(self) -> asyncio.Task[None]:
        if self._task is None or self._task.done():
            self._loop = asyncio.get_running_loop()
            self._task = self._loop.create_task(self.run())
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            await self._task
            self._task = None
        await self.client.aclose()
