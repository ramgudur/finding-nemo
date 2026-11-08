"""The engine tick: every ~2 s, per active app, pure numpy, no LLM.

    events (lookback window) -> operators (trust inside) -> combine (noise-floor norm,
    weighted sum, clamp) -> authority overrides -> score grid + hotspots -> layer_cache

Rules this module keeps (CLAUDE.md):
- Time comes only from ``core.clock`` (rule: the engine reads time only from the clock).
- No LLM calls and nothing that waits on agents. Agents read ``latest()`` snapshots and
  change weights only through ``set_weights`` (called by ``tools/guards.py`` after checks).
- App-agnostic: everything app-specific comes from the ``AppSpec``.
- One bad operator never kills the tick: it contributes a zero layer and an error string.

Layers whose operator has ``role == "override"`` in the registry don't enter the weighted sum;
their layer (clipped to [0, 1]) is max-ed into the override mask, which ``combine`` applies
after the weighted sum.

Shift along a vector field: when an operator's extras carry ``shift = {layer, args}`` plus
``vector_u`` / ``vector_v`` (the ``field`` operator), the named raw layer is replaced by
``field.advect(layer, vector_u, vector_v, cell_m, **args)`` after all operators ran and before
normalizing.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from app.core import clock as clock_mod
from app.core.clock import Clock
from app.core.models import AppSpec, Candidate, Hotspot, Visibility
from app.engine.candidates import registry as candidates
from app.engine.combine import combine
from app.engine.grid import Grid
from app.engine.layer_cache import DTYPE as CACHE_DTYPE
from app.engine.layer_cache import LayerCache
from app.engine.operators import field as field_op
from app.engine.operators import registry
from app.engine.operators.base import Events

log = logging.getLogger(__name__)

TICK_S = 2.0
LOOKBACK_S = 900.0  # default read window for operators that don't declare a horizon
ALL_HISTORY = -1e300  # 'since' for layers that need every event (finite, so any store can compare it)
CACHE_TICKS = 300  # 10 min of 2 s ticks: the backtest replay window
# Memory budget for one live app's layer cache. On a big grid with many layers, 300 full frames
# would not fit (500 x 500 cells x 5 layers is ~1.7 GB), so the live engine keeps fewer frames,
# spaced further apart, over the same 10-minute span. Backtest replays don't use this budget.
CACHE_BUDGET_BYTES = 384 * 2**20
MIN_CACHE_TICKS = 2
HOTSPOT_MAX = 20
HOTSPOT_MATCH_CELLS = 3.0  # a hotspot within this many cells of last tick's keeps its id (scales with cell_m)
CONTRIBUTOR_SHARE = 0.10  # share of a hotspot's score mass to be listed in contributing_layers
VISIBILITY_SHARE = 0.01  # any layer above this share decides visibility: one private one makes it authority


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
        self._data: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def add(self, app_id: str, input_id: str, records: Sequence[Mapping[str, Any]]) -> None:
        """``records``: ``{user_id?, t (POSIX s) or ts, lat, lon, ...declared fields}``."""
        rows = self._data.setdefault((app_id, input_id), [])
        for r in records:
            row = {k: v for k, v in r.items() if k != "ts"}
            row["t"] = float(r["t"]) if "t" in r else clock_mod.to_seconds(r["ts"])
            rows.append(row)

    def events(self, app_id: str, input_id: str, since: float, until: float) -> Events:
        rows = [r for r in self._data.get((app_id, input_id), ()) if since < r["t"] <= until]
        if not rows:
            return empty_events()
        return Events.from_records(rows)


TrustProvider = Callable[[str], Mapping[str, float]]


# -- tick outputs -----------------------------------------------------------------


@dataclass(frozen=True)
class LayerInfo:
    layer_id: str
    input_id: str
    op: str
    noise_floor: float
    is_override: bool
    # How much event history the operator needs: seconds, math.inf for all of it, or None for
    # the tick's default lookback. Comes from the operator, never from a domain assumption.
    horizon_s: float | None = None


def operator_horizon(op: str, params: Mapping[str, Any]) -> float | None:
    """History an operator needs: ``registry.horizon_s`` (None there = all history, e.g. an override
    with no ttl, which stays active until cleared: rule 6). Unregistered ops use the tick default."""
    if op not in registry.OPERATORS:
        return None
    h = registry.horizon_s(op, params)
    return math.inf if h is None else float(h)


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
    candidates: list[Candidate] = field(default_factory=list)  # code-made POI candidates (never labels)
    elapsed_ms: float = 0.0


# -- per-app runtime --------------------------------------------------------------


class AppRuntime:
    """Everything the tick keeps for one app: grid, layer plan, weights, cache, last result."""

    def __init__(self, spec: AppSpec, cache_ticks: int = CACHE_TICKS, cache_budget_bytes: int | None = None) -> None:
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
                is_override = o.op in registry.OPERATORS and registry.get(o.op).role == "override"
                if nf is None and o.op in registry.OPERATORS:
                    nf = registry.parse_params(o.op, params).noise_floor
                self.layers.append(
                    LayerInfo(o.layer_id, inp.id, o.op, float(nf or 1.0), is_override, operator_horizon(o.op, params))
                )
                self.op_params[o.layer_id] = params
                self.weights[o.layer_id] = float(o.weight)
        self.weighted_ids = tuple(li.layer_id for li in self.layers if not li.is_override)
        self.noise_floors = {li.layer_id: li.noise_floor for li in self.layers}
        self.cache_min_gap_s, capacity = cache_plan(
            len(self.weighted_ids), self.grid.shape, cache_ticks, cache_budget_bytes
        )
        self.cache = LayerCache(capacity, self.weighted_ids, self.grid.shape)
        self.last: TickResult | None = None
        self._next_hotspot = 0
        self.candidate_tracker = candidates.CandidateTracker()

    def input_lookback(self, input_id: str, default_s: float) -> float:
        """Read window for one input: the longest horizon among its layers (math.inf = all history)."""
        hs = [li.horizon_s for li in self.layers if li.input_id == input_id]
        return max((default_s if h is None else h) for h in hs) if hs else default_s

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


def cache_frame_bytes(n_layers: int, shape: tuple[int, int]) -> int:
    """Bytes one cached tick takes: the normalized stack, the override mask and its time."""
    return (n_layers + 1) * int(shape[0]) * int(shape[1]) * np.dtype(CACHE_DTYPE).itemsize + 8


def cache_plan(n_layers: int, shape: tuple[int, int], cache_ticks: int, budget_bytes: int | None) -> tuple[float, int]:
    """``(min_gap_s, capacity)`` for a layer cache. Without a budget, or when ``cache_ticks`` frames
    fit in it: every tick is kept (gap 0). Otherwise fewer frames, spaced so they still span
    ``cache_ticks * TICK_S`` seconds."""
    if budget_bytes is None:
        return 0.0, cache_ticks
    fit = max(MIN_CACHE_TICKS, budget_bytes // cache_frame_bytes(n_layers, shape))
    if fit >= cache_ticks:
        return 0.0, cache_ticks
    return cache_ticks * TICK_S / fit, int(fit)


def hotspot_visibility(spec: AppSpec, contributing: Sequence[str]) -> Visibility:
    """Public only if some layers contribute and every one of them is a public output layer;
    otherwise authority (the safe default: override-only or unknown provenance stays private)."""
    public = {o.id for o in spec.outputs.layers if o.visibility == "public"}
    return "public" if contributing and all(c in public for c in contributing) else "authority"


def _connected_components(mask: np.ndarray) -> list[np.ndarray]:
    """8-connected components of a boolean [H, W] mask, as arrays of flat indices (ascending).

    Components come in row-major order of their first cell. Vectorized union-find (hook each
    edge's larger root onto the smaller, then pointer-jump), so a grid-sized component at
    250,000 cells costs a few numpy passes instead of a Python loop per cell.
    """
    mask = np.asarray(mask, dtype=bool)
    h, w = mask.shape
    cells = np.flatnonzero(mask)
    if not len(cells):
        return []
    idx = np.arange(h * w, dtype=np.intp).reshape(h, w)
    pairs = []
    for a, b in (
        (np.s_[:, :-1], np.s_[:, 1:]),  # right
        (np.s_[:-1, :], np.s_[1:, :]),  # down
        (np.s_[:-1, :-1], np.s_[1:, 1:]),  # down-right
        (np.s_[:-1, 1:], np.s_[1:, :-1]),  # down-left
    ):
        both = mask[a] & mask[b]
        pairs.append((idx[a][both], idx[b][both]))
    ea = np.concatenate([p[0] for p in pairs])
    eb = np.concatenate([p[1] for p in pairs])
    parent = np.arange(h * w, dtype=np.intp)
    while len(ea):
        ra, rb = parent[ea], parent[eb]
        lo, hi = np.minimum(ra, rb), np.maximum(ra, rb)
        live = lo != hi
        if not live.any():
            break
        np.minimum.at(parent, hi[live], lo[live])
        while True:  # pointer jumping: every node points straight at its root
            nxt = parent[parent]
            if np.array_equal(nxt, parent):
                break
            parent = nxt
        ea, eb = ea[live], eb[live]
    roots = parent[cells]  # each component's root is its smallest flat index
    order = np.argsort(roots, kind="stable")
    roots, cells = roots[order], cells[order]
    starts = np.flatnonzero(np.r_[True, roots[1:] != roots[:-1]])
    return [np.asarray(c, dtype=np.intp) for c in np.split(cells, starts[1:])]


def find_hotspots(
    rt: AppRuntime,
    score: np.ndarray,
    normalized: np.ndarray,
    weights: np.ndarray,
    *,
    override_layers: Mapping[str, np.ndarray] | None = None,
    threshold: float | None = None,
    max_hotspots: int = HOTSPOT_MAX,
) -> list[Hotspot]:
    """Connected areas with score >= the spec's threshold.

    ``contributing_layers`` (for display): weighted layers supplying >= 10% of the hotspot's score
    mass, plus every override layer active in it.

    Visibility uses a stricter cut (rule 8, fail safe): every weighted layer supplying more than
    ``VISIBILITY_SHARE`` of the score mass, plus every active override, must be a public output
    layer. So a private layer can't shape a public hotspot even when it's too small to be listed
    as a contributor, and an override-driven hotspot stays authority-only."""
    grid = rt.grid
    if threshold is None:
        threshold = rt.spec.outputs.hotspot_threshold
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
        shaping: tuple[str, ...] = ()  # every layer that decides visibility (stricter than `contributing`)
        mass = float(vals.sum())
        if contrib_all is not None and mass > 0:
            share = contrib_all[:, cells].sum(axis=1)
            order = np.argsort(-share)
            contributing = tuple(rt.weighted_ids[i] for i in order if share[i] / mass >= CONTRIBUTOR_SHARE)
            shaping = tuple(rt.weighted_ids[i] for i in order if share[i] / mass > VISIBILITY_SHARE)
        for lid, layer in (override_layers or {}).items():
            if float(layer.ravel()[cells].max()) > 0:
                contributing += (lid,)
                shaping += (lid,)
        # keep the id of the nearest unused hotspot from last tick
        hid, persisted = None, 0
        best = HOTSPOT_MATCH_CELLS * grid.cell_m
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
        area = len(cells) * grid.cell_m**2
        out.append(
            Hotspot(
                id=hid,
                lat=lat,
                lon=lon,
                radius_m=math.sqrt(area / math.pi),
                score=float(vals.max()),
                mean=float(vals.mean()),
                area_m2=area,
                n_cells=len(cells),
                persisted_ticks=persisted,
                contributing_layers=list(contributing),
                visibility=hotspot_visibility(rt.spec, shaping),
            )
        )
    return out


def _apply_shifts(
    rt: AppRuntime, raw: dict[str, np.ndarray], extras: Mapping[str, Mapping[str, Any]], errors: list[str]
) -> None:
    """Carry target layers along a vector field, in place, for every operator result that asks for it."""
    for src_id, ex in extras.items():
        shift = ex.get("shift")
        if not shift:
            continue
        target = shift.get("layer")
        if target not in raw:
            errors.append(f"{src_id}: shift target {target!r} is not a weighted layer of this app; not shifted")
            continue
        try:
            shifted = field_op.advect(
                raw[target], ex["vector_u"], ex["vector_v"], rt.grid.cell_m, **shift.get("args", {})
            )
            shifted = np.asarray(shifted, dtype=float)
            if shifted.shape != rt.grid.shape:
                raise ValueError(f"shifted shape {shifted.shape} != grid {rt.grid.shape}")
            raw[target] = shifted
        except Exception as e:  # noqa: BLE001 - a bad shift leaves the layer unshifted
            errors.append(f"{src_id}: shift of {target} failed: {type(e).__name__}: {e}")


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
    override_layers: dict[str, np.ndarray] = {}
    events_by_input: dict[str, Events] = {}

    for li in rt.layers:
        ev = events_by_input.get(li.input_id)
        if ev is None:
            try:
                back = rt.input_lookback(li.input_id, lookback_s)
                since = now - back if math.isfinite(back) else ALL_HISTORY
                ev = source.events(rt.spec.app_id, li.input_id, since, now)
            except Exception as e:  # noqa: BLE001 - the tick must survive a bad store read
                errors.append(f"{li.input_id}: event read failed: {e}")
                ev = empty_events()
            events_by_input[li.input_id] = ev
        # One store read per input covers its longest horizon; each operator sees only its own.
        own = lookback_s if li.horizon_s is None else li.horizon_s
        if math.isfinite(own) and len(ev):
            ev = ev.select(ev.t > now - own)
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
            ov_layer = np.clip(np.nan_to_num(layer), 0.0, 1.0)
            override_layers[li.layer_id] = ov_layer
            override = np.maximum(override, ov_layer)
        else:
            raw[li.layer_id] = layer

    _apply_shifts(rt, raw, extras, errors)

    ids = rt.weighted_ids
    w = np.array([rt.weights[i] for i in ids], dtype=float)
    if ids:
        res = combine([raw[i] for i in ids], [rt.noise_floors[i] for i in ids], w, override)
        score, normalized, ov = res.score, res.normalized, res.override
    else:
        score, normalized, ov = override.copy(), np.zeros((0, *grid.shape)), override

    last = rt.cache.latest()
    # A paused clock doesn't duplicate ticks. Under a memory budget, frames are spaced
    # cache_min_gap_s apart (half a tick of slack for loop jitter).
    if last is None or (now > float(last.t[0]) and now - float(last.t[0]) >= rt.cache_min_gap_s - TICK_S / 2):
        rt.cache.push(now, normalized, ov)

    hotspots = find_hotspots(rt, score, normalized, w, override_layers=override_layers)
    drafts, cand_errors = candidates.generate(extras, {li.layer_id: li.op for li in rt.layers}, grid, hotspots=hotspots)
    errors.extend(cand_errors)
    cands = rt.candidate_tracker.update(drafts, grid)
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
        candidates=cands,
    )
    rt.last = result
    if result.elapsed_ms > TICK_S * 1000.0:
        log.warning(
            "tick for app %s took %.0f ms (over the %.0f s tick); grid %s, %d layers",
            rt.spec.app_id,
            result.elapsed_ms,
            TICK_S,
            grid.shape,
            len(rt.layers),
        )
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
        rt = AppRuntime(spec, cache_budget_bytes=CACHE_BUDGET_BYTES)
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
