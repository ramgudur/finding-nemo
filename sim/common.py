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


class ServerClock:
    """Reads the platform's clock from `GET /v1/debug/clock` (DEMO_MODE only) and extrapolates between polls,
    so the sim posts events when the *server's* time reaches them: pausing or speeding up the server clock
    (console SimControls) pauses or speeds up the sim with it."""

    def __init__(self, api: str, poll_s: float = 0.5) -> None:
        self.url = f"{api.rstrip('/').replace('://localhost', '://127.0.0.1')}/v1/debug/clock"
        self.poll_s = poll_s
        self._fetched_at = -1e9
        self._now_s = 0.0
        self._speed: float | None = None

    def _fetch(self) -> None:
        with urllib.request.urlopen(self.url, timeout=5) as r:
            body = json.loads(r.read())
        self._now_s, self._speed, self._fetched_at = float(body["now_s"]), body.get("speed"), time.monotonic()

    def now(self) -> float:
        if time.monotonic() - self._fetched_at >= self.poll_s:
            self._fetch()
        return self._now_s + (time.monotonic() - self._fetched_at) * (self._speed or 0.0)


def replay_following(
    posts: Iterable[Post], t0: float, clock: ServerClock, poster: Poster, log_every_s: float = 10.0
) -> None:
    """Send posts when the server clock passes t0 + sim_t (ts stays t0 + sim_t, matching the engine)."""
    next_log = log_every_s
    for p in sorted(posts):
        while clock.now() < t0 + p.sim_t:
            time.sleep(0.05)
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


# -- adapting to an app the dev built in the Builder -----------------------------------------


def parse_input_map(text: str) -> dict[str, str]:
    """`"panic=sos,room_button=rooms"` -> {"panic": "sos", "room_button": "rooms"} (sim id -> app input id)."""
    out = {}
    for part in filter(None, (p.strip() for p in (text or "").split(","))):
        sim_id, _, app_id = part.partition("=")
        if not sim_id or not app_id:
            raise ValueError(f"--input-map entry {part!r} must look like sim_id=app_input_id")
        out[sim_id.strip()] = app_id.strip()
    return out


def fetch_spec(api: str, app: str, authority_key: str) -> dict[str, Any]:
    """The app's spec (authority-only): lets the sim check what the dev actually built."""
    base = api.rstrip("/").replace("://localhost", "://127.0.0.1")
    req = urllib.request.Request(f"{base}/v1/apps/{app}", headers={"X-Authority-Key": authority_key})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def adapt_posts(
    posts: list[Post], input_map: dict[str, str], spec: dict[str, Any] | None = None
) -> tuple[list[Post], list[str]]:
    """Point the sim's posts at the app's input ids. With the app's spec: report inputs that are missing
    or not push, and drop body fields the dev didn't declare (ingest would reject them). Returns
    (posts, problems); any problem means the run would fail, so the caller should stop and show them."""
    out = [Post(p.sim_t, input_map.get(p.input_id, p.input_id), dict(p.body)) for p in posts]
    if spec is None:
        return out, []
    inputs = {i["id"]: i for i in spec.get("inputs", [])}
    problems, used = [], sorted({p.input_id for p in out})
    for iid in used:
        inp = inputs.get(iid)
        if inp is None:
            problems.append(f"the app has no input {iid!r} (add it in the Builder, or use --input-map)")
        elif inp.get("kind") != "push":
            problems.append(f"input {iid!r} is {inp.get('kind')!r}; the sim posts events, so it must be push")
    if problems:
        return out, problems
    base = {"user_id", "lat", "lon", "ts"}
    for p in out:
        declared = {f["name"] for f in inputs[p.input_id].get("fields", [])}
        p.body = {k: v for k, v in p.body.items() if k in base or k in declared}
    for iid in used:
        missing = [f["name"] for f in inputs[iid].get("fields", []) if f.get("required", True)]
        missing = [m for m in missing if not any(m in p.body for p in out if p.input_id == iid)]
        if missing:
            problems.append(f"input {iid!r} requires fields the sim doesn't send: {missing} (make them optional)")
    return out, problems
