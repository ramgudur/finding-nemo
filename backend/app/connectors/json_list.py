"""`json_list` pull connector: a JSON list (or a GeoJSON FeatureCollection, or any JSON with a list
in it) that the platform fetches each `poll_s`; every item becomes one event (#267).

The config is the same keys and path semantics as a push input's `WebhookMapping`
(`connectors/mapping.py`), plus the URL:

    {url?, items_path, lat, lon, ts?, ts_format, user_id?, fields: {name: path}}

`url` may sit on the connector itself (`ConnectorConfig.url`, preferred) or in `config`. Data only
(rule 10): paths are checked strings read from each item, never evaluated. Pure parsing, no
network (`connectors/engine.py` fetches, through the URL guard).

- Items outside the app's area are dropped and counted (the engine would map them to no cell).
- At most `MAX_ITEMS` items are read per poll; the rest are counted as truncated.
- A mapped value is coerced to its declared field type only where that is lossless (a numeric
  string for a number field, a number for a string field). A value that still doesn't fit an
  optional field is left out of that event; a required one rejects the item at write time.
- The fields this connector implies (`connectors/fields.py`) are the keys of `fields`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from app.connectors import mapping as item_mapping
from app.connectors.json_api import FIELD_NAME, MAX_CUSTOM_FIELDS, RESERVED, _check_path
from app.core.models import BBox, InputField, WebhookMapping

MAX_ITEMS = 500
_PATH_KEYS = ("items_path", "lat", "lon", "ts", "user_id")


def check_config(config: dict[str, Any]) -> WebhookMapping:
    """The item mapping a `json_list` config describes, or ValueError with a short reason (no values
    echoed beyond the config's own keys and paths)."""
    body = {k: v for k, v in config.items() if k != "url"}
    if "url" in config and not isinstance(config["url"], str):
        raise ValueError("config.url must be a string")
    try:
        m = WebhookMapping.model_validate(body)
    except ValidationError as e:
        parts = [f"config.{'.'.join(map(str, x['loc'])) or 'config'}: {x['msg']}" for x in e.errors()[:3]]
        raise ValueError("; ".join(parts)) from None
    for key in _PATH_KEYS:
        value = getattr(m, key)
        if value is not None:
            _check_path(value, f"config.{key}")
    if len(m.fields) > MAX_CUSTOM_FIELDS:
        raise ValueError(f"config.fields has more than {MAX_CUSTOM_FIELDS} fields")
    for name, path in m.fields.items():
        if not FIELD_NAME.match(name):
            raise ValueError(f"config.fields: {name!r} is not a valid field name")
        if name in RESERVED:
            raise ValueError(f"config.fields: {name!r} is reserved (set by the connector itself)")
        _check_path(path, f"config.fields[{name!r}]")
    return m


def field_names(config: dict[str, Any]) -> list[str]:
    return list(check_config(config).fields)


@dataclass
class ListParse:
    events: list[dict[str, Any]] = field(default_factory=list)
    items: int = 0
    unmapped: int = 0  # a path missing from the item, or an unreadable timestamp
    outside: int = 0  # dropped: outside the app's area
    truncated: int = 0  # past MAX_ITEMS
    first_error: str | None = None


def _number(v: Any) -> float | int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return v
    if isinstance(v, str):
        try:
            f = float(v)
        except ValueError:
            return None
        return f if math.isfinite(f) else None
    return None


def _coerce(value: Any, f: InputField) -> tuple[bool, Any]:
    """(fits, value) for a mapped value against its declared field type, lossless only."""
    if f.type == "number":
        n = _number(value)
        return (n is not None), n
    if f.type == "integer":
        n = _number(value)
        if n is not None and float(n).is_integer():
            return True, int(n)
        return False, value
    if f.type == "string":
        if isinstance(value, str):
            return True, value
        if isinstance(value, int | float) and not isinstance(value, bool):
            return True, str(value)
        return False, value
    return isinstance(value, bool), value


def _inside(bbox: BBox, lat: Any, lon: Any) -> bool | None:
    la, lo = _number(lat), _number(lon)
    if la is None or lo is None:
        return None  # not a coordinate: left to event validation to reject
    return bbox.min_lat <= la <= bbox.max_lat and bbox.min_lon <= lo <= bbox.max_lon


def _reason(e: Exception) -> str:
    if isinstance(e, item_mapping.PathNotFound):
        return str(e)  # names the config's path only
    return f"unreadable value ({type(e).__name__})"  # never the item's own value


def parse(
    raw: Any,
    config: dict[str, Any],
    now: float,
    bbox: BBox | None = None,
    declared: list[InputField] | None = None,
) -> ListParse:
    """Every item at `items_path` mapped to an event body. Raises ValueError for a bad config or
    a missing `items_path`; one bad item never drops the rest."""
    m = check_config(config)
    try:
        found = item_mapping.items(raw, m.items_path)
    except item_mapping.PathNotFound as e:
        raise ValueError(f"items_path: {e}") from None
    out = ListParse(items=len(found), truncated=max(0, len(found) - MAX_ITEMS))
    types = {f.name: f for f in declared or []}
    stamp = datetime.fromtimestamp(now, tz=UTC)
    for item in found[:MAX_ITEMS]:
        try:
            event = item_mapping.map_item(item, m, stamp)
        except Exception as e:  # noqa: BLE001 - one bad item must not drop the rest
            out.unmapped += 1
            out.first_error = out.first_error or _reason(e)
            continue
        if bbox is not None and _inside(bbox, event["lat"], event["lon"]) is False:
            out.outside += 1
            continue
        if event.get("user_id") is not None and not isinstance(event["user_id"], str):
            event["user_id"] = str(event["user_id"]) if _number(event["user_id"]) is not None else None
        for name in m.fields:
            f = types.get(name)
            if f is None or name not in event:
                continue
            fits, value = _coerce(event[name], f)
            if fits:
                event[name] = value
            elif not f.required:
                del event[name]
        out.events.append(event)
    return out
