"""RSS/Atom parsing: pure, no network. `engine.py` fetches the text; this turns it into
raw event dicts. Understands the GeoRSS `<georss:point>lat lon</georss:point>` extension
(what `sim/campus_alert.py`'s scripted feed emits) for a location per item; an item with
none is dropped rather than guessed at.
"""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree

_GEORSS = "{http://www.georss.org/georss}"


def _text(item: ElementTree.Element, tag: str) -> str | None:
    el = item.find(tag)
    return el.text.strip() if el is not None and el.text else None


def _parse_point(raw: str) -> tuple[float, float] | None:
    parts = raw.split()
    if len(parts) != 2:
        return None
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    return lat, lon


def _parse_date(raw: str | None, now: float) -> datetime:
    if raw is None:
        return datetime.fromtimestamp(now, tz=UTC)
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return datetime.fromtimestamp(now, tz=UTC)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_items(xml_text: str, now: float, include_unlocated: bool = False) -> list[dict[str, Any]]:
    """Each item becomes `{doc_id, title, description, ts, lat, lon}`. An item with no
    `<georss:point>` (or an unparseable one) is skipped -- never guess a location -- unless
    `include_unlocated`, which keeps it with lat/lon None for a locator to place (or drop). `now`
    (from `core/clock.py`, via the caller) is only the fallback for a missing/unparseable
    `pubDate` -- this module reads no clock itself, so it stays pure and sim runs line up."""
    root = ElementTree.fromstring(xml_text)  # noqa: S314 - a connector's own feed, not untrusted user input
    items: list[dict[str, Any]] = []
    for item in root.iter("item"):
        point_el = item.find(f"{_GEORSS}point")
        point = _parse_point(point_el.text) if point_el is not None and point_el.text else None
        if point is None and not include_unlocated:
            continue
        lat, lon = point if point is not None else (None, None)
        guid = _text(item, "guid") or _text(item, "link") or _text(item, "title") or ""
        items.append(
            {
                "doc_id": guid,
                "title": _text(item, "title") or "",
                "description": _text(item, "description") or "",
                "ts": _parse_date(_text(item, "pubDate"), now),
                "lat": lat,
                "lon": lon,
            }
        )
    return items
