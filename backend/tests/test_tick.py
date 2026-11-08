import asyncio

import numpy as np
import pytest

from app.core.clock import SimClock
from app.core.models import AppSpec
from app.engine.grid import Grid
from app.engine.operators import registry
from app.engine.operators.base import OperatorParams, OperatorResult
from app.engine.tick import AppRuntime, InMemoryEventSource, TickEngine, tick_app

# Synthetic ~500 m box; nothing app-specific.
BBOX = {"min_lat": 45.0, "min_lon": 10.0, "max_lat": 45.0045, "max_lon": 10.00635}
T0 = 1_000_000.0


def spec(**overrides) -> AppSpec:
    data = {
        "app_id": "test-app",
        "intent": "test",
        "status": "active",
        "grid": {"cell_m": 10, "bbox": BBOX},
        "inputs": [
            {
                "id": "crowd",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "a report",
                "operators": [{"op": "density", "weight": 1.0, "half_life_s": 60, "sigma_m": 15}],
            }
        ],
    }
    data.update(overrides)
    return AppSpec.model_validate(data)


def cluster(grid: Grid, x: float, y: float, n: int, t: float, seed: int = 0, spread: float = 8.0):
    rng = np.random.default_rng(seed)
    lat, lon = grid.to_latlon(x + rng.normal(0, spread, n), y + rng.normal(0, spread, n))
    return [
        {"user_id": f"u{seed}_{i}", "t": t - i * 0.1, "lat": la, "lon": lo}
        for i, (la, lo) in enumerate(zip(lat, lon, strict=True))
    ]


def test_only_active_specs_tick():
    with pytest.raises(ValueError, match="only active"):
        AppRuntime(spec(status="pending_confirmation"))


def test_density_cluster_becomes_hotspot_near_truth():
    rt = AppRuntime(spec())
    src = InMemoryEventSource()
    src.add("test-app", "crowd", cluster(rt.grid, 200, 300, 30, T0))
    res = tick_app(rt, src, now=T0, clock=SimClock(T0))
    assert res.errors == []
    assert res.score.shape == rt.grid.shape
    assert 0.0 <= res.score.min() and res.score.max() <= 1.0
    assert res.layer_ids == ("crowd.density",)
    assert len(res.hotspots) == 1
    h = res.hotspots[0]
    x, y = rt.grid.to_xy(h.lat, h.lon)
    assert np.hypot(float(x) - 200, float(y) - 300) < 15
    assert h.contributing_layers == ("crowd.density",)


def test_tick_ignores_future_and_stale_events():
    rt = AppRuntime(spec())
    src = InMemoryEventSource()
    src.add("test-app", "crowd", cluster(rt.grid, 200, 300, 30, T0 + 50))  # after "now"
    src.add("test-app", "crowd", cluster(rt.grid, 100, 100, 30, T0 - 5000, seed=1))  # beyond lookback
    res = tick_app(rt, src, now=T0, clock=SimClock(T0))
    assert res.score.max() == 0.0 and res.hotspots == []


def test_hotspot_id_persists_and_cache_fills():
    rt = AppRuntime(spec())
    src = InMemoryEventSource()
    src.add("test-app", "crowd", cluster(rt.grid, 200, 300, 30, T0))
    ids = []
    for k in range(3):
        src.add("test-app", "crowd", cluster(rt.grid, 200 + 3 * k, 300, 10, T0 + 2 * k, seed=10 + k))
        res = tick_app(rt, src, now=T0 + 2 * k, clock=SimClock(T0 + 2 * k))
        ids.append((res.hotspots[0].id, res.hotspots[0].persisted_ticks))
    assert ids == [("h1", 0), ("h1", 1), ("h1", 2)]
    assert len(rt.cache) == 3
    # a paused clock doesn't push a duplicate tick
    tick_app(rt, src, now=T0 + 4, clock=SimClock(T0 + 4))
    assert len(rt.cache) == 3


def test_bad_or_missing_operators_dont_kill_the_tick(monkeypatch):
    crowd = spec().inputs[0].model_dump()
    crowd["operators"].append({"op": "event_points", "weight": 0.5})
    rt = AppRuntime(spec(inputs=[crowd]))
    src = InMemoryEventSource()
    src.add("test-app", "crowd", cluster(rt.grid, 200, 300, 30, T0))

    def boom(*a, **k):
        raise RuntimeError("bad data")

    info = registry.OPERATORS["density"]
    monkeypatch.setitem(registry.OPERATORS, "density", info.__class__(**{**info.__dict__, "fn": boom}))
    res = tick_app(rt, src, now=T0, clock=SimClock(T0))
    assert any("event_points" in e and "not registered" in e for e in res.errors)
    assert any("crowd.density" in e and "bad data" in e for e in res.errors)
    assert res.score.max() == 0.0
    assert not np.isnan(res.score).any()


class _OverrideParams(OperatorParams):
    noise_floor: float = 1.0
    radius_m: float = 30.0


def _fake_override(events, params, trust, clock, grid):
    layer = np.zeros(grid.shape)
    for la, lo in zip(events.lat, events.lon, strict=True):
        layer[Grid.cells_within(grid, la, lo, params.radius_m)] = 1.0
    return OperatorResult(layer=layer, extras={"kind": "override"})


def test_override_wins_after_weighted_sum(monkeypatch):
    monkeypatch.setitem(
        registry.OPERATORS,
        "override",
        registry.OperatorInfo("override", _fake_override, _OverrideParams, ("fixed_point",), "test override"),
    )
    crowd = spec().inputs[0].model_dump()
    marker = {
        "id": "marker",
        "kind": "push",
        "shape": "fixed_point",
        "tier": "locked",
        "meaning": "an authority marker",
        "operators": [{"op": "override", "weight": 0, "radius_m": 30}],
    }
    rt = AppRuntime(spec(inputs=[crowd, marker]))
    assert rt.weighted_ids == ("crowd.density",)  # override layers stay out of the weighted sum
    src = InMemoryEventSource()
    lat, lon = rt.grid.to_latlon(400.0, 100.0)
    src.add("test-app", "marker", [{"t": T0 - 1, "lat": float(lat), "lon": float(lon)}])
    res = tick_app(rt, src, now=T0, clock=SimClock(T0))
    r, c, _ = rt.grid.cell_of(lat, lon)
    assert res.score[int(r), int(c)] == 1.0
    assert res.override.max() == 1.0
    assert len(res.hotspots) == 1 and res.hotspots[0].contributing_layers == ()
    assert rt.cache.latest().overrides.max() == 1.0


def test_set_weights_changes_score_and_validates():
    rt = AppRuntime(spec())
    src = InMemoryEventSource()
    src.add("test-app", "crowd", cluster(rt.grid, 200, 300, 30, T0))
    full = tick_app(rt, src, now=T0, clock=SimClock(T0)).score.max()
    rt.set_weights({"crowd.density": 0.25})
    quarter = tick_app(rt, src, now=T0 + 2, clock=SimClock(T0 + 2)).score.max()
    assert quarter < full
    with pytest.raises(KeyError):
        rt.set_weights({"nope": 1.0})
    with pytest.raises(ValueError):
        rt.set_weights({"crowd.density": -1.0})


def test_engine_loop_reads_clock_and_notifies_listeners():
    clock = SimClock(T0)
    src = InMemoryEventSource()
    eng = TickEngine(src, clock=clock, interval_s=0.01, trust=lambda app_id: {"u0_0": 0.0})
    rt = eng.add_app(spec())
    src.add("test-app", "crowd", cluster(rt.grid, 200, 300, 30, T0))
    seen = []

    def listener(r):
        seen.append(r.t)
        clock.advance(2.0)

    def bad_listener(r):
        raise RuntimeError("listener bug")

    eng.listeners += [bad_listener, listener]

    async def main():
        eng.start()
        while len(seen) < 3:
            await asyncio.sleep(0.01)
        await eng.stop()

    asyncio.run(asyncio.wait_for(main(), timeout=10))
    assert seen[:3] == [T0, T0 + 2, T0 + 4]
    assert eng.latest("test-app").hotspots
