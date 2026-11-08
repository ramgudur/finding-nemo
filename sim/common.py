"""Shared simulator helpers: local-meter frame, event posting, ground-truth output.

Simulators are clients of the platform. They talk to it ONLY through the public ingest
API (and, for pull inputs, by serving a feed a connector polls). They never import backend code.
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
M_PER_DEG_LAT = 111_320.0


@dataclass(frozen=True)
class LocalFrame:
    """Equirectangular local meters from the spec bbox's south-west corner (x east, y north)."""

    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float

    @classmethod
    def from_spec(cls, spec: dict[str, Any]) -> LocalFrame:
        b = spec["grid"]["bbox"]
        return cls(b["min_lat"], b["min_lon"], b["max_lat"], b["max_lon"])

    @property
    def m_per_deg_lon(self) -> float:
        return M_PER_DEG_LAT * math.cos(math.radians((self.min_lat + self.max_lat) / 2))

    @property
    def size_m(self) -> tuple[float, float]:
        return self.to_xy(self.max_lat, self.max_lon)

    def to_latlon(self, x: float, y: float) -> tuple[float, float]:
        return self.min_lat + y / M_PER_DEG_LAT, self.min_lon + x / self.m_per_deg_lon

    def to_xy(self, lat: float, lon: float) -> tuple[float, float]:
        return (lon - self.min_lon) * self.m_per_deg_lon, (lat - self.min_lat) * M_PER_DEG_LAT


def load_spec(name: str) -> dict[str, Any]:
    return json.loads((REPO / "specs" / f"{name}.json").read_text(encoding="utf-8"))


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(order=True)
class Post:
    """One push event at simulated second `sim_t`. `body` has no `ts`; it is stamped as t0 + sim_t."""

    sim_t: float
    input_id: str = field(compare=False)
    body: dict[str, Any] = field(compare=False)

    def stamped(self, t0: float) -> dict[str, Any]:
        return {**self.body, "ts": iso(t0 + self.sim_t)}


class IngestError(RuntimeError):
    pass


class Poster:
    """POSTs events to `/v1/apps/{app}/inputs/{input}/events` with the app's ingest key."""

    def __init__(self, api: str, app: str, ingest_key: str, workers: int = 8, timeout_s: float = 5.0):
        # On Windows "localhost" tries ::1 first and stalls ~2 s per request against uvicorn's IPv4 bind.
        api = api.rstrip("/").replace("://localhost", "://127.0.0.1")
        self.base = f"{api}/v1/apps/{app}/inputs"
        self.key = ingest_key
        self.timeout_s = timeout_s
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.ok = 0
        self.failed = 0
        self.fatal: str | None = None
        self.consecutive_failures = 0

    def _send(self, input_id: str, body: dict[str, Any]) -> None:
        req = urllib.request.Request(
            f"{self.base}/{input_id}/events",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "X-Ingest-Key": self.key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s):
                pass
            self.ok += 1
            self.consecutive_failures = 0
        except urllib.error.HTTPError as e:
            self.failed += 1
            if e.code in (401, 403):
                self.fatal = f"ingest key rejected (HTTP {e.code})"
            elif e.code == 404:
                self.fatal = f"app or input not found (HTTP 404 for {input_id!r}); is the app active?"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            self.failed += 1
            self.consecutive_failures += 1
            if self.consecutive_failures >= 20:
                self.fatal = f"API unreachable: {e}"

    def submit(self, input_id: str, body: dict[str, Any]) -> None:
        if self.fatal:
            raise IngestError(self.fatal)
        self.pool.submit(self._send, input_id, body)

    def close(self, cancel: bool = False) -> None:
        self.pool.shutdown(wait=True, cancel_futures=cancel)


def replay(posts: Iterable[Post], t0: float, speed: float, poster: Poster, log_every_s: float = 10.0) -> None:
    """Send posts in sim-time order, pacing sim seconds at `speed`x wall time."""
    wall0 = time.monotonic()
    next_log = log_every_s
    for p in sorted(posts):
        delay = wall0 + p.sim_t / speed - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        poster.submit(p.input_id, p.stamped(t0))
        if p.sim_t >= next_log:
            print(f"  t={p.sim_t:5.0f}s  ok={poster.ok}  failed={poster.failed}", flush=True)
            next_log += log_every_s


def write_ground_truth(name: str, lines: Iterable[dict[str, Any]], out_dir: Path | None = None) -> Path:
    """Ground truth is read only by `api/debug.py` (DEMO_MODE=1). Never send it to the platform."""
    out_dir = out_dir or REPO / "sim" / "ground_truth"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
    return path
