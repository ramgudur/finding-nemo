"""`local_file` connector (pull): one located document per line of a text file the dev keeps.

Config is data only (rule 10): `{filename}` is a bare name (`core/models.LOCAL_FILE_NAME`, checked
when the config is parsed), read from `<dir of SQLITE_PATH>/feeds/<filename>`, i.e.
`backend/data/feeds/` by default (gitignored). The file is read as text and never executed, and
the resolved path must still sit directly in that folder (no traversal, no symlink out of it).

File format, one item per line::

    # comment lines and blank lines are ignored
    <lat>, <lon> | <text>
    <lat>, <lon>, <confidence 0-1> | <text>

Each valid line becomes one document event: `lat`/`lon` from the line, `text`, `confidence`
(default 0.9), and `doc_id`, a stable hash of the normalized line, so the poller's
(user_id, doc_id) de-duplication writes a line once however often the file is read. `ts` is the
poll time (`core/clock.py`), stamped by the caller. Lines outside the app's area are dropped and
counted; malformed lines are skipped and counted. Nothing here names a domain (rule 3).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.models import BBox, InputField, local_file_name_error

DEFAULT_CONFIDENCE = 0.9
DEFAULT_SOURCE_ID = "local_file"
MAX_FILE_BYTES = 256 * 1024
MAX_LINES = 1000
MAX_TEXT_CHARS = 500
FEEDS_DIRNAME = "feeds"

# What every event of this connector carries besides user_id/lat/lon/ts (`connectors/fields.py`).
FIELDS = (
    InputField(name="text", type="string", required=False, description="The line's text (set by the connector)."),
    InputField(name="confidence", type="number", required=False, description="Confidence 0-1 (set by the connector)."),
    InputField(
        name="doc_id", type="string", required=False, description="Stable id of the line (set by the connector)."
    ),
)


def feeds_dir() -> Path:
    """`<dir of SQLITE_PATH>/feeds`. Read at call time, so tests can point it elsewhere."""
    return Path(get_settings().sqlite_path).resolve().parent / FEEDS_DIRNAME


def check_config(config: dict[str, Any]) -> str:
    """The configured file name, or ValueError with a reason."""
    name = config.get("filename")
    reason = local_file_name_error(name)
    if reason is not None:
        raise ValueError(reason)
    source = config.get("source_id", DEFAULT_SOURCE_ID)
    if not isinstance(source, str) or not source.strip() or len(source) > 64:
        raise ValueError("config.source_id must be a short non-empty string")
    return str(name)


def source_id(config: dict[str, Any]) -> str:
    return str(config.get("source_id") or DEFAULT_SOURCE_ID)


def read_text(config: dict[str, Any]) -> str:
    """The file's text. ValueError with a plain reason when it can't be read."""
    name = check_config(config)
    folder = feeds_dir()
    path = (folder / name).resolve()
    if path.parent != folder.resolve():  # a symlink pointing out of the folder, or anything odd
        raise ValueError("file must be inside data/feeds")
    if not path.is_file():
        raise ValueError(f"file not found in data/feeds: {name}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"file is larger than {MAX_FILE_BYTES // 1024} KB")
    return path.read_text(encoding="utf-8", errors="replace")


def doc_id(line: str) -> str:
    """Stable id of one line: the same text (whitespace normalized) always gives the same id."""
    norm = " ".join(line.split())
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]  # noqa: S324 - an id, not security


@dataclass
class FileParse:
    events: list[dict[str, Any]] = field(default_factory=list)
    lines: int = 0  # non-comment, non-blank lines seen
    outside: int = 0  # valid lines outside the app's area (dropped)
    malformed: int = 0  # lines that don't follow the format (skipped)

    def note(self) -> str:
        """Counts for the connector's `last_status` ("ok (<note>)")."""
        parts = [f"{len(self.events)} valid line(s)"]
        if self.outside:
            parts.append(f"{self.outside} outside the area")
        if self.malformed:
            parts.append(f"{self.malformed} malformed")
        return ", ".join(parts)


def _parse_line(line: str) -> tuple[float, float, float, str] | None:
    if "|" not in line:
        return None
    head, text = line.split("|", 1)
    text = " ".join(text.split())
    nums = [p.strip() for p in head.split(",")]
    if not text or len(nums) not in (2, 3):
        return None
    try:
        values = [float(n) for n in nums]
    except ValueError:
        return None
    lat, lon = values[0], values[1]
    conf = values[2] if len(values) == 3 else DEFAULT_CONFIDENCE
    if not all(v == v and abs(v) != float("inf") for v in values):  # NaN / inf
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0 <= conf <= 1):
        return None
    return lat, lon, conf, text[:MAX_TEXT_CHARS]


def parse(text: str, config: dict[str, Any], now: float, bbox: BBox | None = None) -> FileParse:
    """Event bodies from the file's text (at most `MAX_LINES` lines are read)."""
    out = FileParse()
    src = source_id(config)
    ts = datetime.fromtimestamp(now, tz=UTC)
    seen: set[str] = set()
    for raw in text.splitlines()[:MAX_LINES]:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out.lines += 1
        parsed = _parse_line(line)
        if parsed is None:
            out.malformed += 1
            continue
        lat, lon, conf, body = parsed
        if bbox is not None and not (bbox.min_lat <= lat <= bbox.max_lat and bbox.min_lon <= lon <= bbox.max_lon):
            out.outside += 1
            continue
        did = doc_id(line)
        if did in seen:  # the same line twice in one file counts once
            continue
        seen.add(did)
        out.events.append(
            {"user_id": src, "lat": lat, "lon": lon, "ts": ts, "text": body, "confidence": conf, "doc_id": did}
        )
    return out


def check(config: dict[str, Any], now: float, bbox: BBox | None = None) -> FileParse:
    """What `test_connector` needs: read and parse once, writing nothing. ValueError with a clear
    reason unless at least one valid line is inside the area."""
    parsed = parse(read_text(config), config, now, bbox)
    if not parsed.events:
        if parsed.outside:
            raise ValueError(f"no valid lines inside the app's area ({parsed.outside} outside)")
        raise ValueError(f"no valid lines ({parsed.malformed} malformed)" if parsed.malformed else "no valid lines")
    return parsed
