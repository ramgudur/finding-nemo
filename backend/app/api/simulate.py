"""`POST /v1/apps/{app}/simulate` (#268): generate test events for one push input (authority key).

A dev tool for the console: it lets the dev see the map, the Critic and the spoof checks react
without a real device in the field. Generated events go through the SAME validation and storage
as `POST /v1/apps/{app}/inputs/{input}/events` (`api/ingest._check_position`, `_check_fields`,
`Db.insert_event`), so nothing reaches the engine that a real push couldn't send. The engine and
agents see them only as ordinary events (rule 1: this route writes no weights, trust or POIs).

Modes:
  - ``normal``: points scattered uniformly within ``radius_m`` of the centre, one distinct
    ``user_id`` ``sim-<n>`` each, timestamps spread over the last few tens of seconds.
  - ``spoof``: brand-new ``user_id``s ``spoof-<n>`` all at exactly the centre and within a few
    seconds, the pattern `engine/trust.py`'s duplicate-coordinate and new-ID-burst checks look for.
  - ``line``: ``clusters`` clusters evenly along ``from_point`` -> ``to_point`` (a little jitter),
    ``count`` reporters each, user ids ``line-<k>-<n>`` (cluster k, reporter n) so trust can differ
    per cluster. Each reporter keeps one home point in its cluster (within ``radius_m``) and every
    wave reports near it, so nothing moves implausibly. The first wave is posted in the request;
    a background asyncio job (outside the tick, rule 2) posts one wave about every ``WAVE_S``
    seconds until ``duration_s`` has passed on the Clock, so the clusters persist through decay.
    At most one line job per app: a new line request replaces it, ``DELETE`` stops it.

Ids for normal/spoof keep counting per app across requests (`sim-1`, `sim-2`, ...), so every
generated user is new; line ids are stable (the same reporters keep reporting). Required input
fields get neutral defaults (string "simulated", number 0, integer 0, boolean false). Events
outside the app's area or failing the field checks are counted as rejected, not stored.

Tracing (rule 11): each request writes one TraceEntry, and a line job writes one more when it
ends. `AgentName` has no dev value, so it follows the connector-poll convention
(`connectors/engine.py`): ``agent="scout"`` (the area that brings data in) with
``action="simulate"`` and a `why` that says the events are simulated and were asked for by the
dev. The trace is authority-only (rule 8); the response carries counts only. Nothing here is
simulator ground truth (rule 9): the events are ordinary inputs, labelled as such.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import threading
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.api.deps import authority_key_header, get_db
from app.api.ingest import OUTSIDE_AREA, _check_fields, _check_position
from app.api.trace import _authority
from app.core import clock as clock_mod
from app.core.db import Db
from app.core.models import (
    AppSpec,
    Event,
    FieldType,
    InputField,
    InputSpec,
    SimulateRequest,
    SimulateResult,
    TraceEntry,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/apps", tags=["simulate"])

PREFIX = {"normal": "sim-", "spoof": "spoof-", "line": "line-"}
NORMAL_SPREAD_S = 30.0  # normal events: timestamps spread over the last 30 s
SPOOF_SPREAD_S = 3.0  # spoof events: all within a few seconds
WAVE_S = 10.0  # line mode: one wave about this often
LINE_JITTER_FRAC = 0.25  # line mode: a cluster centre sits at most this share of radius_m off the line
HOME_JITTER_M = 5.0  # line mode: a reporter's wave-to-wave jitter around its home (at most 5% of radius_m)
_M_PER_DEG_LAT = 111_320.0

# The line job's wait between waves. Module-level so tests can swap in one that advances a SimClock.
_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep

_DEFAULTS: dict[FieldType, Any] = {"string": "simulated", "number": 0.0, "integer": 0, "boolean": False}


def get_sim_rng(request: Request) -> np.random.Generator:
    """RNG for generated positions and times. Unseeded (OS entropy) in production; tests override
    this dependency with a seeded generator."""
    return np.random.default_rng()


class _Counters:
    """Next `<n>` per (app, prefix). Started lazily from the highest id already stored, so ids stay
    new across requests and restarts."""

    def __init__(self) -> None:
        self._next: dict[tuple[str, str], int] = {}
        self._lock = threading.Lock()

    def take(self, db: Db, app_id: str, prefix: str, n: int) -> int:
        with self._lock:
            key = (app_id, prefix)
            if key not in self._next:
                pat = re.compile(rf"^{re.escape(prefix)}(\d+)$")
                hi = 0
                for e in db.list_events(app_id):
                    m = pat.match(e.user_id or "")
                    if m:
                        hi = max(hi, int(m.group(1)))
                self._next[key] = hi + 1
            start = self._next[key]
            self._next[key] = start + n
            return start


def _counters(request: Request) -> _Counters:
    state = request.app.state
    c = getattr(state, "sim_counters", None)
    if c is None:
        c = state.sim_counters = _Counters()
    return c


def _field_defaults(fields: list[InputField]) -> dict[str, Any]:
    return {f.name: _DEFAULTS[f.type] for f in fields if f.required}


def _offset(lat: float, lon: float, dx_m: np.ndarray, dy_m: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(lat, lon) moved by local east/north meters."""
    return lat + dy_m / _M_PER_DEG_LAT, lon + dx_m / (_M_PER_DEG_LAT * max(math.cos(math.radians(lat)), 1e-6))


def _disc(n: int, radius_m: float, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """n (dx, dy) offsets uniform over a disc of `radius_m`."""
    r = radius_m * np.sqrt(rng.random(n))
    a = rng.random(n) * 2 * math.pi
    return r * np.cos(a), r * np.sin(a)


def _positions(body: SimulateRequest, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    assert body.lat is not None and body.lon is not None  # noqa: S101 - the model requires them here
    if body.mode == "spoof":
        return np.full(body.count, body.lat), np.full(body.count, body.lon)
    dx, dy = _disc(body.count, body.radius_m, rng)
    return _offset(body.lat, body.lon, dx, dy)


def _inside(spec: AppSpec, lat: float, lon: float) -> bool:
    return _check_position(spec, Event(lat=lat, lon=lon, ts=datetime.now(UTC))) is None


def _store(db: Db, spec: AppSpec, inp: InputSpec, raw: dict[str, Any]) -> bool:
    """One generated body through the /events checks; True if stored."""
    try:
        event = Event.model_validate(raw)
    except Exception:  # noqa: BLE001 - e.g. a point pushed past a pole; counted, not stored
        return False
    if _check_position(spec, event) or _check_fields(inp.fields, event.model_extra or {}):
        return False
    db.insert_event(spec.app_id, inp.id, event, received_at=clock_mod.now())
    return True


def _trace(db: Db, app_id: str, t: float, saw: str, result: str, why: str) -> None:
    db.add_trace(
        TraceEntry(
            t=datetime.fromtimestamp(t, UTC),
            app_id=app_id,
            agent="scout",
            saw=saw,
            result=result,
            action="simulate",
            why=why,
        )
    )


# --- line mode ---------------------------------------------------------------------------


@dataclass
class LinePlan:
    """Where line mode's reporters stand: `homes[k][n]` is reporter n of cluster k, as (lat, lon)."""

    centres: list[tuple[float, float]]
    homes: list[list[tuple[float, float]]]
    jitter_m: float


def line_plan(body: SimulateRequest, rng: np.random.Generator) -> LinePlan:
    """Clusters evenly along the line (end to end) with a little jitter, each with `count` home
    points uniform within `radius_m` of its centre."""
    assert body.from_point is not None and body.to_point is not None  # noqa: S101 - required in line mode
    a, b = body.from_point, body.to_point
    centres: list[tuple[float, float]] = []
    homes: list[list[tuple[float, float]]] = []
    for i in range(body.clusters):
        f = i / (body.clusters - 1)
        jx, jy = _disc(1, LINE_JITTER_FRAC * body.radius_m, rng)
        clat, clon = _offset(a.lat + f * (b.lat - a.lat), a.lon + f * (b.lon - a.lon), jx, jy)
        c = (float(clat[0]), float(clon[0]))
        centres.append(c)
        dx, dy = _disc(body.count, body.radius_m, rng)
        hl, ho = _offset(c[0], c[1], dx, dy)
        homes.append([(float(x), float(y)) for x, y in zip(hl, ho, strict=True)])
    return LinePlan(centres=centres, homes=homes, jitter_m=min(HOME_JITTER_M, 0.05 * body.radius_m))


def line_user_id(k: int, n: int) -> str:
    """Reporter n (1-based) of cluster k (1-based)."""
    return f"{PREFIX['line']}{k}-{n}"


def line_wave(
    db: Db, spec: AppSpec, inp: InputSpec, plan: LinePlan, rng: np.random.Generator, now: float
) -> tuple[int, int]:
    """One wave: every reporter reports once near its home, timestamps spread over the last
    `WAVE_S` seconds. Returns (accepted, rejected)."""
    extras = _field_defaults(inp.fields)
    accepted = rejected = 0
    for k, members in enumerate(plan.homes, start=1):
        n = len(members)
        offsets = rng.random(n) * WAVE_S
        dx, dy = _disc(n, plan.jitter_m, rng)
        for j, (hlat, hlon) in enumerate(members):
            lat, lon = _offset(hlat, hlon, dx[j : j + 1], dy[j : j + 1])
            ts = datetime.fromtimestamp(now, UTC) - timedelta(seconds=float(offsets[j]))
            raw = {"user_id": line_user_id(k, j + 1), "lat": float(lat[0]), "lon": float(lon[0]), "ts": ts, **extras}
            if _store(db, spec, inp, raw):
                accepted += 1
            else:
                rejected += 1
    return accepted, rejected


@dataclass
class LineJob:
    job_id: str
    app_id: str
    task: asyncio.Task[None] | None = None
    end_reason: str = "finished"
    waves: int = 1


class LineJobs:
    """At most one line job per app, in process memory (a restart simply ends them)."""

    def __init__(self) -> None:
        self._jobs: dict[str, LineJob] = {}

    def get(self, app_id: str) -> LineJob | None:
        job = self._jobs.get(app_id)
        return job if job is not None and job.task is not None and not job.task.done() else None

    def stop(self, app_id: str, reason: str) -> bool:
        job = self.get(app_id)
        self._jobs.pop(app_id, None)
        if job is None or job.task is None:
            return False
        job.end_reason = reason
        job.task.cancel()
        return True

    def start(self, job: LineJob, run: Callable[[LineJob], Awaitable[None]]) -> None:
        self.stop(job.app_id, "replaced by a new line simulation")
        job.task = asyncio.get_running_loop().create_task(run(job))  # type: ignore[arg-type]
        self._jobs[job.app_id] = job


def get_line_jobs(request: Request) -> LineJobs:
    state = request.app.state
    jobs = getattr(state, "sim_line_jobs", None)
    if jobs is None:
        jobs = state.sim_line_jobs = LineJobs()
    return jobs


async def _run_line_job(
    db: Db, input_id: str, plan: LinePlan, rng: np.random.Generator, duration_s: float, job: LineJob
) -> None:
    """Post a wave about every `WAVE_S` s until `duration_s` of Clock time has passed since the
    first one. Stops early if the app or input stops being an active push input."""
    start = clock_mod.now()
    accepted = rejected = 0
    try:
        while True:
            await _sleep(WAVE_S)
            now = clock_mod.now()
            if now - start >= duration_s:
                break
            spec = db.get_app(job.app_id)
            inp = next((i for i in spec.inputs if i.id == input_id), None) if spec is not None else None
            if spec is None or spec.status != "active" or inp is None or inp.kind != "push":
                job.end_reason = "the app or input is no longer active"
                break
            a, r = await asyncio.to_thread(line_wave, db, spec, inp, plan, rng, now)
            job.waves, accepted, rejected = job.waves + 1, accepted + a, rejected + r
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - a failing wave ends the job, never the server
        log.exception("line simulation for %s failed", job.app_id)
        job.end_reason = "a wave failed"
    finally:
        try:
            _trace(
                db,
                job.app_id,
                clock_mod.now(),
                saw=f"line simulation {job.job_id} on input {input_id!r} ended ({job.end_reason})",
                result=f"{job.waves} wave(s); after the first: {accepted} accepted, {rejected} rejected",
                why="SIMULATED test data requested by the dev, not real reports",
            )
        except Exception:  # noqa: BLE001 - bookkeeping must never raise out of a cancelled task
            log.exception("tracing the end of line simulation %s failed", job.job_id)


async def _simulate_line(
    db: Db, spec: AppSpec, inp: InputSpec, body: SimulateRequest, rng: np.random.Generator, jobs: LineJobs
) -> SimulateResult:
    assert body.from_point is not None and body.to_point is not None  # noqa: S101 - required in line mode
    for name, pt in (("from_point", body.from_point), ("to_point", body.to_point)):
        if not _inside(spec, pt.lat, pt.lon):
            raise HTTPException(status_code=422, detail=f"{name}: {OUTSIDE_AREA}")
    plan = line_plan(body, rng)
    now = clock_mod.now()
    accepted, rejected = await asyncio.to_thread(line_wave, db, spec, inp, plan, rng, now)
    job = LineJob(job_id=str(uuid.uuid4()), app_id=spec.app_id) if body.duration_s > 0 else None
    _trace(
        db,
        spec.app_id,
        now,
        saw=(
            f"dev asked for a line simulation on input {inp.id!r}: {body.clusters} clusters x {body.count} "
            f"reporter(s), a wave every ~{WAVE_S:g} s for {body.duration_s} s"
        ),
        result=f"first wave: {accepted} accepted, {rejected} rejected (outside the area or failed field checks)",
        why=f"SIMULATED test data requested by the dev, not real reports; user_ids "
        f"{line_user_id(1, 1)}..{line_user_id(body.clusters, body.count)}"
        + (f"; job {job.job_id}" if job is not None else ""),
    )
    if job is None:
        jobs.stop(spec.app_id, "replaced by a one-wave line simulation")
        return SimulateResult(accepted=accepted, rejected=rejected, user_id_prefix=PREFIX["line"])

    async def run(j: LineJob) -> None:
        await _run_line_job(db, inp.id, plan, rng, float(body.duration_s), j)

    jobs.start(job, run)
    return SimulateResult(
        accepted=accepted,
        rejected=rejected,
        user_id_prefix=PREFIX["line"],
        job_id=job.job_id,
        duration_s=body.duration_s,
    )


# --- normal / spoof ----------------------------------------------------------------------


def _simulate_burst(
    db: Db, spec: AppSpec, inp: InputSpec, body: SimulateRequest, rng: np.random.Generator, counters: _Counters
) -> SimulateResult:
    app = spec.app_id
    prefix = PREFIX[body.mode]
    start = counters.take(db, app, prefix, body.count)
    now = clock_mod.now()
    spread = SPOOF_SPREAD_S if body.mode == "spoof" else NORMAL_SPREAD_S
    offsets = np.sort(rng.random(body.count) * spread)[::-1]  # oldest first
    lats, lons = _positions(body, rng)
    extras = _field_defaults(inp.fields)

    accepted = rejected = 0
    for i in range(body.count):
        ts = datetime.fromtimestamp(now, UTC) - timedelta(seconds=float(offsets[i]))
        raw = {"user_id": f"{prefix}{start + i}", "lat": float(lats[i]), "lon": float(lons[i]), "ts": ts, **extras}
        if _store(db, spec, inp, raw):
            accepted += 1
        else:
            rejected += 1

    _trace(
        db,
        app,
        now,
        saw=f"dev asked for {body.count} simulated {body.mode} event(s) on input {inp.id!r}",
        result=f"{accepted} accepted, {rejected} rejected (outside the area or failed field checks)",
        why=f"SIMULATED test data requested by the dev, not real reports; user_ids "
        f"{prefix}{start}..{prefix}{start + body.count - 1}",
    )
    return SimulateResult(accepted=accepted, rejected=rejected, user_id_prefix=prefix)


# --- routes ------------------------------------------------------------------------------


def _active_push_input(db: Db, app: str, input_id: str) -> tuple[AppSpec, InputSpec]:
    spec = db.get_app(app)
    if spec is None or spec.status != "active":
        raise HTTPException(status_code=409, detail="app is not active")
    inp = next((i for i in spec.inputs if i.id == input_id), None)
    if inp is None:
        raise HTTPException(status_code=404, detail="input not found")
    if inp.kind != "push":
        raise HTTPException(status_code=422, detail="only push inputs can be simulated")
    return spec, inp


@router.post("/{app}/simulate", status_code=202, response_model=SimulateResult, response_model_exclude_none=True)
async def simulate(
    app: str,
    body: SimulateRequest,
    request: Request,
    db: Db = Depends(get_db),
    rng: np.random.Generator = Depends(get_sim_rng),
    jobs: LineJobs = Depends(get_line_jobs),
    authority_key: str | None = Depends(authority_key_header),
) -> SimulateResult:
    _authority(db, app, authority_key)  # 404 unknown app, 401 missing/wrong key
    spec, inp = _active_push_input(db, app, body.input_id)
    if body.mode == "line":
        return await _simulate_line(db, spec, inp, body, rng, jobs)
    assert body.lat is not None and body.lon is not None  # noqa: S101 - the model requires them here
    if not _inside(spec, body.lat, body.lon):
        raise HTTPException(status_code=422, detail=f"centre: {OUTSIDE_AREA}")
    return await asyncio.to_thread(_simulate_burst, db, spec, inp, body, rng, _counters(request))


@router.delete("/{app}/simulate", status_code=204)
async def stop_simulation(
    app: str,
    db: Db = Depends(get_db),
    jobs: LineJobs = Depends(get_line_jobs),
    authority_key: str | None = Depends(authority_key_header),
) -> Response:
    """Stop this app's running line job, if any (204 either way)."""
    _authority(db, app, authority_key)  # 404 unknown app, 401 missing/wrong key
    jobs.stop(app, "stopped by the dev")
    return Response(status_code=204)
