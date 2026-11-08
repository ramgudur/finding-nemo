"""Item mapping shared by the webhook receiver (`api/webhooks.py`, push) and the `json_list`
connector (`connectors/json_list.py`, pull): one JSON item -> one `/events`-shaped body.

Data only (rule 10): every path is a dotted field name with optional list indices (e.g.
"geometry.coordinates.1"), read from one item at a time, never evaluated. Nothing here names a
domain (rule 3).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.models import TsFormat, WebhookMapping


class PathNotFound(Exception):
    def __init__(self, path: str) -> None:
        super().__init__(f"path {path!r} not found")
        self.path = path


def dig(data: Any, path: str) -> Any:
    """Dotted path with optional list indices, e.g. "geometry.coordinates.1"."""
    node = data
    for part in path.split(".") if path else []:
        if isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError) as e:
                raise PathNotFound(path) from e
        elif isinstance(node, dict):
            if part not in node:
                raise PathNotFound(path)
            node = node[part]
        else:
            raise PathNotFound(path)
    return node


def items(body: Any, items_path: str) -> list[Any]:
    """The items at `items_path`: a list as is, one object as a one-item list."""
    node = dig(body, items_path)
    return node if isinstance(node, list) else [node]


def parse_ts(raw: Any, ts_format: TsFormat) -> datetime:
    if ts_format == "epoch_s":
        return datetime.fromtimestamp(float(raw), tz=UTC)
    if ts_format == "epoch_ms":
        return datetime.fromtimestamp(float(raw) / 1000.0, tz=UTC)
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))


def map_item(item: Any, mapping: WebhookMapping, now: datetime) -> dict[str, Any]:
    """One item -> an `/events`-shaped body, or raises with a reason this item is rejected."""
    out: dict[str, Any] = {"lat": dig(item, mapping.lat), "lon": dig(item, mapping.lon)}
    out["ts"] = parse_ts(dig(item, mapping.ts), mapping.ts_format) if mapping.ts else now
    if mapping.user_id:
        out["user_id"] = dig(item, mapping.user_id)
    for name, path in mapping.fields.items():
        out[name] = dig(item, path)
    return out
