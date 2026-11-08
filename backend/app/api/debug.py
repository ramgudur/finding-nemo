"""Demo-only debug API (`DEMO_MODE=1`): ground-truth error chart and simulation clock controls.

Rule 9: this module is the ONLY reader of simulator ground truth, and nothing here feeds the
engine or the agents. Ground truth is read from files; the engine side only contributes its
own outputs (top hotspot and operator point estimates per tick, recorded by a tick listener).
Without `install(...)` (i.e. DEMO_MODE off) the routes are not mounted, so every path is a 404.

Ground-truth files are JSONL, newest per app wins: `<truth_dir>/<app_id>-*.jsonl`, with lines
`{"type": "source", "ts": ISO-8601, "lat", "lon"}` giving the true source position over time.
"""

from __future__ import annotations

import json
import math
import threading
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import authority_key_header, get_db
from app.core import clock as clock_mod
from app.core.clock import SimClock
from app.core.db import Db
from app.core.models import UtcDatetime
from app.engine.tick import TickEngine, TickResult

DEFAULT_TRUTH_DIR = Path(__file__).resolve().parents[3] / "sim" / "ground_truth"
HISTORY_TICKS = 3600  # ~2 h at the 2 s tick
MAX_SPEED = 20.0
M_PER_DEG = 111_320.0

router = APIRouter(prefix="/v1/debug", tags=["debug"])


# --- contracts (demo-only; mirrored in frontend/src/api/types.ts) ---------------------


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClockState(_Out):
    now: UtcDatetime
    now_s: float
    paused: bool
    speed: float | None = Field(description="Sim seconds per wall second; None when paused or on wall time.")
    controllable: bool = Field(description="True once a SimClock is installed (POST .../clock).")


class ClockControl(_Out):
    paused: bool | None = None
    speed: float | None = Field(default=None, gt=0, le=MAX_SPEED)


class ErrorPoint(_Out):
    t: UtcDatetime
    hotspot_error_m: float | None = Field(description="Top hotspot to the true source; None without a hotspot.")
    point_error_m: float | None = Field(
        description="Best operator point estimate to the true source; None without one."
    )


class ChangeMarker(_Out):
    t: UtcDatetime
    id: str
    kind: str
    target: str
    actor: str
    reason: str


class ErrorSeries(_Out):
    app_id: str
    truth_file: str | None
    series: list[ErrorPoint]
    changes: list[ChangeMarker]


# --- per-tick recorder -----------------------------------------------------------------


@dataclass
class _Sample:
    t: float
    hotspot: tuple[float, float] | None
    points: list[tuple[float, float]]


@dataclass
class DebugState:
    truth_dir: Path
    speed: float = 1.0  # speed to resume at after a pause
    history: dict[str, deque[_Sample]] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, r: TickResult) -> None:
        top = max(r.hotspots, key=_hotspot_score, default=None)
        points = [
            (float(p["lat"]), float(p["lon"]))
            for ex in r.extras.values()
            if isinstance(p := ex.get("point"), dict) and "lat" in p and "lon" in p
        ]
        sample = _Sample(r.t, (top.lat, top.lon) if top is not None else None, points)
        with self.lock:
            self.history.setdefault(r.app_id, deque(maxlen=HISTORY_TICKS)).append(sample)

    def samples(self, app_id: str, since_s: float | None) -> list[_Sample]:
        with self.lock:
            return [s for s in self.history.get(app_id, ()) if since_s is None or s.t >= since_s]


def _hotspot_score(h: Any) -> float:
    return float(getattr(h, "score", None) or getattr(h, "peak", 0.0))


def install(app: FastAPI, engine: TickEngine, truth_dir: Path | None = None) -> DebugState:
    """Mount the debug routes and start recording ticks. Call only when DEMO_MODE is on; without it the
    routes don't exist (404)."""
    state = DebugState(truth_dir=truth_dir or DEFAULT_TRUTH_DIR)
    app.state.debug = state
    app.include_router(router)
    engine.listeners.append(state.record)
    return state


def _state(request: Request) -> DebugState:
    state = getattr(request.app.state, "debug", None)
    if state is None:
        raise HTTPException(status_code=404, detail="not found")
    return state


def _require_authority(db: Db, app_id: str, key: str | None) -> None:
    if db.get_app(app_id) is None:
        raise HTTPException(status_code=404, detail="app not found")
    if key is None or not db.check_authority_key(app_id, key):
        raise HTTPException(status_code=401, detail="authority key required")


# --- ground truth ----------------------------------------------------------------------


def _newest_truth(truth_dir: Path, app_id: str) -> Path | None:
    files = sorted(truth_dir.glob(f"{app_id}-*.jsonl"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def _read_source_track(path: Path) -> list[tuple[float, float, float]]:
    track = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("type") == "source":
            track.append((clock_mod.to_seconds(row["ts"]), float(row["lat"]), float(row["lon"])))
    return sorted(track)


def _source_at(track: list[tuple[float, float, float]], t: float) -> tuple[float, float] | None:
    if not track or t < track[0][0] or t > track[-1][0]:
        return None
    lo, hi = 0, len(track) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if track[mid][0] <= t:
            lo = mid
        else:
            hi = mid
    (t0, a0, o0), (t1, a1, o1) = track[lo], track[hi]
    f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
    return a0 + f * (a1 - a0), o0 + f * (o1 - o0)


def _dist_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    dy = (a[0] - b[0]) * M_PER_DEG
    dx = (a[1] - b[1]) * M_PER_DEG * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dx, dy)


def error_series(samples: Iterable[_Sample], track: list[tuple[float, float, float]]) -> list[ErrorPoint]:
    out = []
    for s in samples:
        truth = _source_at(track, s.t)
        if truth is None:
            continue
        hs = _dist_m(s.hotspot, truth) if s.hotspot else None
        pt = min((_dist_m(p, truth) for p in s.points), default=None)
        out.append(
            ErrorPoint(
                t=datetime.fromtimestamp(s.t).astimezone(),
                hotspot_error_m=None if hs is None else round(hs, 2),
                point_error_m=None if pt is None else round(pt, 2),
            )
        )
    return out


# --- routes ----------------------------------------------------------------------------


def _clock_state() -> ClockState:
    c = clock_mod.get_clock()
    now = c.now()
    sim = isinstance(c, SimClock)
    speed = c.speed if sim else 1.0
    return ClockState(
        now=datetime.fromtimestamp(now).astimezone(),
        now_s=now,
        paused=sim and speed is None,
        speed=speed,
        controllable=sim,
    )


@router.get("/clock", response_model=ClockState)
def get_clock_state(_: DebugState = Depends(_state)) -> ClockState:
    """The process clock. The simulator follows this (`--follow-clock`), so pausing or speeding it up
    here moves the sim's event timestamps and the engine together."""
    return _clock_state()


@router.post("/apps/{app}/clock", response_model=ClockState)
def control_clock(
    app: str,
    body: ClockControl,
    state: DebugState = Depends(_state),
    db: Db = Depends(get_db),
    authority_key: str | None = Depends(authority_key_header),
) -> ClockState:
    _require_authority(db, app, authority_key)
    c = clock_mod.get_clock()
    if not isinstance(c, SimClock):  # first control: take over from wall time without a jump
        c = SimClock(start=c.now(), speed=1.0)
        clock_mod.set_clock(c)
    if body.speed is not None:
        state.speed = body.speed
    paused = (c.speed is None) if body.paused is None else body.paused
    c.set_speed(None if paused else state.speed)
    return _clock_state()


@router.get("/apps/{app}/error", response_model=ErrorSeries)
def get_error(
    app: str,
    since_s: float | None = Query(default=None, description="Only ticks at or after this POSIX time."),
    state: DebugState = Depends(_state),
    db: Db = Depends(get_db),
    authority_key: str | None = Depends(authority_key_header),
) -> ErrorSeries:
    _require_authority(db, app, authority_key)
    truth = _newest_truth(state.truth_dir, app)
    track = _read_source_track(truth) if truth else []
    changes = [
        ChangeMarker(t=c.t, id=c.id, kind=c.kind, target=c.target, actor=c.actor, reason=c.reason)
        for c in db.list_changes(app, since=since_s)
        if c.kind in ("weight", "trust")
    ]
    return ErrorSeries(
        app_id=app,
        truth_file=truth.name if truth else None,
        series=error_series(state.samples(app, since_s), track),
        changes=changes,
    )
