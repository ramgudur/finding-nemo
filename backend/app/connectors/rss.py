"""RSS/Atom parsing: pure, no network. `engine.py` fetches the text; this turns it into
raw event dicts. Reads RSS 2.0 / RSS 1.0 `<item>` and Atom `<entry>` elements alike, and
understands the GeoRSS `<georss:point>lat lon</georss:point>` extension for a location per
item; an item with none is dropped rather than guessed at (unless a locator is to place it).
"""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree

_GEORSS = "{http://www.georss.org/georss}"
_ITEM_TAGS = ("item", "entry")  # RSS <item>, Atom <entry>


def _local(tag: Any) -> str:
    """The tag without its namespace: `{http://www.w3.org/2005/Atom}entry` -> `entry`."""
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child(item: ElementTree.Element, *names: str) -> ElementTree.Element | None:
    """The first direct child whose local name is one of `names` (in `names` order)."""
    for name in names:
        for el in item:
            if _local(el.tag) == name:
                return el
    return None


def _text(item: ElementTree.Element, *names: str) -> str | None:
    for name in names:
        el = _child(item, name)
        if el is not None:
            text = "".join(el.itertext()).strip()
            if text:
                return text
    return None


def _link(item: ElementTree.Element) -> str | None:
    """RSS `<link>text</link>`, or Atom `<link href=".."/>` (rel="alternate" or no rel first)."""
    links = [el for el in item if _local(el.tag) == "link"]
    for el in sorted(links, key=lambda el: el.get("rel", "alternate") != "alternate"):
        href = el.get("href") or (el.text or "").strip()
        if href:
            return href
    return None


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
    """RFC 822 (RSS `pubDate`) or ISO 8601 (Atom `published`/`updated`, `dc:date`); `now` if neither."""
    if raw is None:
        return datetime.fromtimestamp(now, tz=UTC)
    dt: datetime | None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except ValueError:
            return datetime.fromtimestamp(now, tz=UTC)
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def parse_items(xml_text: str, now: float, include_unlocated: bool = False) -> list[dict[str, Any]]:
    """Each item/entry becomes `{doc_id, title, description, ts, lat, lon}`. An item with no
    `<georss:point>` (or an unparseable one) is skipped -- never guess a location -- unless
    `include_unlocated`, which keeps it with lat/lon None for a locator to place (or drop). `now`
    (from `core/clock.py`, via the caller) is only the fallback for a missing/unparseable date --
    this module reads no clock itself, so it stays pure and sim runs line up."""
    root = ElementTree.fromstring(xml_text)  # noqa: S314 - a connector's own feed, not untrusted user input
    items: list[dict[str, Any]] = []
    for item in root.iter():
        if _local(item.tag) not in _ITEM_TAGS:
            continue
        point_el = item.find(f"{_GEORSS}point")
        point = _parse_point(point_el.text) if point_el is not None and point_el.text else None
        if point is None and not include_unlocated:
            continue
        lat, lon = point if point is not None else (None, None)
        title = _text(item, "title") or ""
        guid = _text(item, "guid", "id") or _link(item) or title
        items.append(
            {
                "doc_id": guid,
                "title": title,
                "description": _text(item, "description", "summary", "content") or "",
                "ts": _parse_date(_text(item, "pubDate", "published", "updated", "date"), now),
                "lat": lat,
                "lon": lon,
            }
        )
    return items
