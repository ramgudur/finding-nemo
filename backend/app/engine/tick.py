"""The engine tick: every ~2 s, per active app, pure numpy, no LLM.

    events (lookback window) -> operators (trust inside) -> combine (noise-floor norm,
    weighted sum, clamp) -> authority overrides -> score grid + hotspots -> layer_cache

Rules this module keeps (CLAUDE.md):
- Time comes only from ``core.clock`` (rule: the engine reads time only from the clock).
- No LLM calls and nothing that waits on agents. Agents read ``latest()`` snapshots and
  change weights only through ``set_weights`` (called by ``tools/guards.py`` after checks).
- App-agnostic: everything app-specific comes from the ``AppSpec``.
- One bad operator never kills the tick: it contributes a zero layer and an error string.

Layers from the ``override`` operator don't enter the weighted sum; their layer (clipped to
[0, 1]) is max-ed into the override mask, which ``combine`` applies after the weighted sum.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from app.core import clock as clock_mod
from app.core.clock import Clock
from app.core.models import AppSpec
from app.engine.combine import combine
from app.engine.grid import Grid
from app.engine.layer_cache import LayerCache
from app.engine.operators import registry
from app.engine.operators.base import Events

log = logging.getLogger(__name__)

TICK_S = 2.0
LOOKBACK_S = 900.0  # events older than this are not passed to operators (they've decayed)
CACHE_TICKS = 300  # 10 min of 2 s ticks: the backtest replay window
HOTSPOT_THRESHOLD = 0.5
HOTSPOT_MAX = 20
HOTSPOT_MATCH_M = 30.0  # a hotspot within this distance of last tick's keeps its id


# -- event source -----------------------------------------------------------------


class EventSource(Protocol):
    """Where the tick reads events. The ingest/event store implements this."""

    def events(self, app_id: str, input_id: str, since: float, until: float) -> Events: ...


def empty_events() -> Events:
    return Events(
        user_id=np.array([], dtype=object),
        t=np.array([], dtype=float),
        lat=np.array([], dtype=float),
        lon=np.array([], dtype=float),
    )


class InMemoryEventSource:
    """Simple per-(app, input) append-only store. Good for tests and the sim; the DB-backed
    store can replace it as long as it implements ``EventSource``."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], list[tuple[str, float, float, float]]] = {}

    def add(self, app_id: str, input_id: str, records: Sequence[Mapping[str, Any]]) -> None:
        """``records``: ``{user_id?, t (POSIX s) or ts, lat, lon}``."""
        rows = self._data.setdefault((app_id, input_id), [])
        for r in records:
            t = r["t"] if "t" in r else clock_mod.to_seconds(r["ts"])
            rows.append((str(r.get("user_id") or ""), float(t), float(r["lat"]), float(r["lon"])))

    def events(self, app_id: str, input_id: str, since: float, until: float) -> Events:
        rows = [r for r in self._data.get((app_id, input_id), ()) if since < r[1] <= until]
        if not rows:
            return empty_events()
        u, t, la, lo = zip(*rows, strict=True)
        return Events(
            user_id=np.array(u, dtype=object),
            t=np.array(t, dtype=float),
            lat=np.array(la, dtype=float),
            lon=np.array(lo, dtype=float),
        )


TrustProvider = Callable[[str], Mapping[str, float]]


# -- tick outputs -----------------------------------------------------------------


@dataclass(frozen=True)
class Hotspot:
    """Connected area with score >= threshold. Internal until a Hotspot contract lands in models.py."""

    id: str
    lat: float
    lon: float
    peak: float
    mean: float
    area_m2: float
    n_cells: int
    persisted_ticks: int
    contributing_layers: tuple[str, ...]


@dataclass(frozen=True)
class LayerInfo:
    layer_id: str
    input_id: str
    op: str
    noise_floor: float
    is_override: bool


@dataclass
class TickResult:
    app_id: str
    t: float
    score: np.ndarray  # [H, W] in [0, 1]
    layer_ids: tuple[str, ...]  # weighted layers, in stack order
    normalized: np.ndarray  # [L, H, W]
    override: np.ndarray  # [H, W]
    weights: dict[str, float]
    hotspots: list[Hotspot]
    extras: dict[str, dict[str, Any]]  # layer_id -> operator extras (point estimates, kind, ...)
    errors: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0


# -- per-app runtime --------------------------------------------------------------


class AppRuntime:
    """Everything the tick keeps for one app: grid, layer plan, weights, cache, last result."""

    def __init__(self, spec: AppSpec, cache_ticks: int = CACHE_TICKS) -> None:
        if spec.status != "active":
            raise ValueError(f"app {spec.app_id!r} is {spec.status!r}; only active (dev-confirmed) specs tick")
        self.spec = spec
        self.grid = Grid.from_spec(spec.grid)
        self.layers: list[LayerInfo] = []
        self.op_params: dict[str, dict[str, Any]] = {}
        self.weights: dict[str, float] = {}
        for inp in spec.inputs:
            for o in inp.operators:
                params = o.params()
                nf = o.noise_floor
                is_override = o.op == "override"
                if nf is None and o.op in registry.OPERATORS:
                    nf = registry.parse_params(o.op, params).noise_floor
                self.layers.append(LayerInfo(o.layer_id, inp.id, o.op, float(nf or 1.0), is_override))
                self.op_params[o.layer_id] = params
                self.weights[o.layer_id] = float(o.weight)
        self.weighted_ids = tuple(li.layer_id for li in self.layers if not li.is_override)
        self.noise_floors = {li.layer_id: li.noise_floor for li in self.layers}
        self.cache = LayerCache(cache_ticks, self.weighted_ids, self.grid.shape)
        self.last: TickResult | None = None
        self._next_hotspot = 0

    def set_weights(self, weights: Mapping[str, float]) -> None:
        """Replace weights for known layers. Validation (tiers, backtests) happens in tools/guards.py."""
        unknown = set(weights) - set(self.weights)
        if unknown:
            raise KeyError(f"unknown layers: {sorted(unknown)}")
        for k, v in weights.items():
            if not np.isfinite(v) or v < 0:
                raise ValueError(f"weight for {k} must be finite and >= 0")
        self.weights.update({k: float(v) for k, v in weights.items()})

    def new_hotspot_id(self) -> str:
        self._next_hotspot += 1
        return f"h{self._next_hotspot}"


def _connected_components(mask: np.ndarray) -> list[np.ndarray]:
    """8-connected components of a boolean [H, W] mask, as arrays of flat indices."""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    comps: list[np.ndarray] = []
    for r0, c0 in zip(*np.nonzero(mask), strict=True):
        if seen[r0, c0]:
            continue
        stack = [(int(r0), int(c0))]
        seen[r0, c0] = True
        cells = []
        while stack:
            r, c = stack.pop()
            cells.append(r * w + c)
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < h and 0 <= cc < w and mask[rr, cc] and not seen[rr, cc]:
                        seen[rr, cc] = True
                        stack.append((rr, cc))
        comps.append(np.array(cells, dtype=np.intp))
    return comps


def find_hotspots(
    rt: AppRuntime,
    score: np.ndarray,
    normalized: np.ndarray,
    weights: np.ndarray,
    *,
    threshold: float = HOTSPOT_THRESHOLD,
    max_hotspots: int = HOTSPOT_MAX,
) -> list[Hotspot]:
    grid = rt.grid
    comps = _connected_components(score >= threshold)
    flat = score.ravel()
    # biggest peak first; ties by size
    comps.sort(key=lambda c: (-float(flat[c].max()), -len(c)))
    comps = comps[:max_hotspots]
    prev = list(rt.last.hotspots) if rt.last else []
    used: set[str] = set()
    out: list[Hotspot] = []
    w_cols = grid.shape[1]
    contrib_all = normalized.reshape(normalized.shape[0], -1) * weights[:, None] if len(weights) else None
    for cells in comps:
        vals = flat[cells]
        rows, cols = cells // w_cols, cells % w_cols
        cx, cy = grid.cell_center_xy(rows, cols)
        wsum = float(vals.sum())
        x, y = float((cx * vals).sum() / wsum), float((cy * vals).sum() / wsum)
        lat, lon = (float(v) for v in grid.to_latlon(x, y))
        contributing: tuple[str, ...] = ()
        if contrib_all is not None:
            share = contrib_all[:, cells].sum(axis=1)
            total = float(share.sum())
            if total > 0:
                order = np.argsort(-share)
                contributing = tuple(rt.weighted_ids[i] for i in order if share[i] / total >= 0.1)
        # keep the id of the nearest unused hotspot from last tick
        hid, persisted = None, 0
        best = HOTSPOT_MATCH_M
        for p in prev:
            if p.id in used:
                continue
            px, py = grid.to_xy(p.lat, p.lon)
            d = float(np.hypot(float(px) - x, float(py) - y))
            if d <= best:
                best, hid, persisted = d, p.id, p.persisted_ticks + 1
        if hid is None:
            hid = rt.new_hotspot_id()
        used.add(hid)
        out.append(
            Hotspot(
                id=hid,
                lat=lat,
                lon=lon,
                peak=float(vals.max()),
                mean=float(vals.mean()),
                area_m2=len(cells) * grid.cell_m**2,
                n_cells=len(cells),
                persisted_ticks=persisted,
                contributing_layers=contributing,
            )
        )
    return out


def tick_app(
    rt: AppRuntime,
    source: EventSource,
    *,
    now: float,
    trust: Mapping[str, float] | None = None,
    clock: Clock | None = None,
    lookback_s: float = LOOKBACK_S,
) -> TickResult:
    """One tick for one app. Pure apart from updating ``rt`` (cache, last result, hotspot ids)."""
    started = time.perf_counter()
    clk = clock or clock_mod.get_clock()
    grid = rt.grid
    errors: list[str] = []
    raw: dict[str, np.ndarray] = {}
    extras: dict[str, dict[str, Any]] = {}
    override = np.zeros(grid.shape)
    events_by_input: dict[str, Events] = {}

    for li in rt.layers:
        ev = events_by_input.get(li.input_id)
        if ev is None:
            try:
                ev = source.events(rt.spec.app_id, li.input_id, now - lookback_s, now)
            except Exception as e:  # noqa: BLE001 - the tick must survive a bad store read
                errors.append(f"{li.input_id}: event read failed: {e}")
                ev = empty_events()
            events_by_input[li.input_id] = ev
        layer = np.zeros(grid.shape)
        if li.op not in registry.OPERATORS:
            errors.append(f"{li.layer_id}: operator {li.op!r} not registered; layer is zero")
        elif len(ev):
            try:
                res = registry.run(li.op, ev, rt.op_params[li.layer_id], trust, clk, grid)
                layer = np.asarray(res.layer, dtype=float)
                if layer.shape != grid.shape:
                    raise ValueError(f"layer shape {layer.shape} != grid {grid.shape}")
                extras[li.layer_id] = res.extras
            except Exception as e:  # noqa: BLE001 - one operator must not kill the tick
                errors.append(f"{li.layer_id}: {type(e).__name__}: {e}")
                layer = np.zeros(grid.shape)
        if li.is_override:
            override = np.maximum(override, np.clip(np.nan_to_num(layer), 0.0, 1.0))
        else:
            raw[li.layer_id] = layer

    ids = rt.weighted_ids
    w = np.array([rt.weights[i] for i in ids], dtype=float)
    if ids:
        res = combine([raw[i] for i in ids], [rt.noise_floors[i] for i in ids], w, override)
        score, normalized, ov = res.score, res.normalized, res.override
    else:
        score, normalized, ov = override.copy(), np.zeros((0, *grid.shape)), override

    last = rt.cache.latest()
    if last is None or now > float(last.t[0]):  # a paused clock doesn't duplicate ticks
        rt.cache.push(now, normalized, ov)

    hotspots = find_hotspots(rt, score, normalized, w)
    result = TickResult(
        app_id=rt.spec.app_id,
        t=now,
        score=score,
        layer_ids=ids,
        normalized=normalized,
        override=ov,
        weights=dict(rt.weights),
        hotspots=hotspots,
        extras=extras,
        errors=errors,
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
    )
    rt.last = result
    return result


# -- the loop ----------------------------------------------------------------------


TickListener = Callable[[TickResult], None]


class TickEngine:
    """Runs ``tick_app`` for every registered app every ``interval_s`` seconds.

    The numpy work runs in a worker thread so the asyncio loop (API, WebSocket) stays
    responsive. Listeners (WS broadcast, Critic triggers) get each ``TickResult``; a
    listener that raises is logged and skipped, never fatal.
    """

    def __init__(
        self,
        source: EventSource,
        *,
        trust: TrustProvider | None = None,
        clock: Clock | None = None,
        interval_s: float = TICK_S,
        lookback_s: float = LOOKBACK_S,
    ) -> None:
        self.source = source
        self.trust = trust
        self._clock = clock
        self.interval_s = interval_s
        self.lookback_s = lookback_s
        self.apps: dict[str, AppRuntime] = {}
        self.listeners: list[TickListener] = []
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    @property
    def clock(self) -> Clock:
        return self._clock or clock_mod.get_clock()

    def add_app(self, spec: AppSpec) -> AppRuntime:
        rt = AppRuntime(spec)
        self.apps[spec.app_id] = rt
        return rt

    def remove_app(self, app_id: str) -> None:
        self.apps.pop(app_id, None)

    def latest(self, app_id: str) -> TickResult | None:
        rt = self.apps.get(app_id)
        return rt.last if rt else None

    def set_weights(self, app_id: str, weights: Mapping[str, float]) -> None:
        self.apps[app_id].set_weights(weights)

    def tick_once(self) -> list[TickResult]:
        now = self.clock.now()
        results = []
        for app_id, rt in list(self.apps.items()):
            try:
                trust = self.trust(app_id) if self.trust else None
                r = tick_app(rt, self.source, now=now, trust=trust, clock=self.clock, lookback_s=self.lookback_s)
            except Exception:  # noqa: BLE001 - one app must not stop the others
                log.exception("tick failed for app %s", app_id)
                continue
            results.append(r)
            for fn in list(self.listeners):
                try:
                    fn(r)
                except Exception:  # noqa: BLE001
                    log.exception("tick listener failed for app %s", app_id)
        return results

    async def run(self) -> None:
        self._stop.clear()
        while not self._stop.is_set():
            started = time.monotonic()
            await asyncio.to_thread(self.tick_once)
            delay = max(0.0, self.interval_s - (time.monotonic() - started))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=delay)
            except TimeoutError:
                pass

    def start(self) -> asyncio.Task[None]:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self.run())
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None
