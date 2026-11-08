import asyncio

import numpy as np
import pytest

from app.core.clock import SimClock
from app.core.models import AppSpec
from app.engine.grid import Grid
from app.engine.operators import registry
from app.engine.tick import LOOKBACK_S, AppRuntime, InMemoryEventSource, TickEngine, tick_app

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
    crowd["operators"].append({"op": "telepathy", "weight": 0.5})
    rt = AppRuntime(spec(inputs=[crowd]))
    src = InMemoryEventSource()
    src.add("test-app", "crowd", cluster(rt.grid, 200, 300, 30, T0))

    def boom(*a, **k):
        raise RuntimeError("bad data")

    info = registry.OPERATORS["density"]
    monkeypatch.setitem(registry.OPERATORS, "density", info.__class__(**{**info.__dict__, "fn": boom}))
    res = tick_app(rt, src, now=T0, clock=SimClock(T0))
    assert any("telepathy" in e and "not registered" in e for e in res.errors)
    assert any("crowd.density" in e and "bad data" in e for e in res.errors)
    assert res.score.max() == 0.0
    assert not np.isnan(res.score).any()


def test_override_wins_after_weighted_sum_and_clears():
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
    # the declared 'cleared' field reaches the operator through the event source
    src.add("test-app", "marker", [{"t": T0 + 1, "lat": float(lat), "lon": float(lon), "cleared": True}])
    res = tick_app(rt, src, now=T0 + 2, clock=SimClock(T0 + 2))
    assert res.override.max() == 0.0 and res.score.max() == 0.0


def _vector_spec(shift_layer: str) -> AppSpec:
    crowd = spec().inputs[0].model_dump()
    vec = {
        "id": "vec",
        "kind": "pull",
        "shape": "field",
        "tier": "stiff",
        "meaning": "vector readings",
        "operators": [
            {
                "op": "field",
                "weight": 0.0,
                "vector_speed_field": "speed",
                "vector_dir_field": "dir",
                "shift_layer": shift_layer,
                "shift_horizon_s": 20,
            }
        ],
    }
    return spec(inputs=[crowd, vec])


def test_field_shift_carries_target_layer_along_vector_field():
    rt = AppRuntime(_vector_spec("crowd.density"))
    still = AppRuntime(spec())
    src = InMemoryEventSource()
    src.add("test-app", "crowd", cluster(rt.grid, 200, 250, 30, T0))
    # 5 m/s from the west: blows east, so the crowd layer moves ~100 m east over 20 s
    lat, lon = rt.grid.to_latlon(250.0, 250.0)
    src.add(
        "test-app", "vec", [{"t": T0 - 1, "lat": float(lat), "lon": float(lon), "value": 1, "speed": 5, "dir": 270}]
    )
    res = tick_app(rt, src, now=T0, clock=SimClock(T0))
    base = tick_app(still, src, now=T0, clock=SimClock(T0))
    assert res.errors == []
    x_shift, _ = rt.grid.to_xy(res.hotspots[0].lat, res.hotspots[0].lon)
    x_base, _ = rt.grid.to_xy(base.hotspots[0].lat, base.hotspots[0].lon)
    assert 80 < float(x_shift - x_base) < 120


def test_field_shift_to_unknown_layer_is_an_error_not_a_crash():
    rt = AppRuntime(_vector_spec("nope.density"))
    src = InMemoryEventSource()
    lat, lon = rt.grid.to_latlon(250.0, 250.0)
    src.add(
        "test-app", "vec", [{"t": T0 - 1, "lat": float(lat), "lon": float(lon), "value": 1, "speed": 5, "dir": 270}]
    )
    res = tick_app(rt, src, now=T0, clock=SimClock(T0))
    assert any("shift target 'nope.density'" in e for e in res.errors)


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


def _marker_spec(**op_params):
    marker = {
        "id": "marker",
        "kind": "push",
        "shape": "fixed_point",
        "tier": "locked",
        "meaning": "an authority point",
        "operators": [{"op": "override", "weight": 0, "radius_m": 30, **op_params}],
    }
    return spec(inputs=[marker])


def test_override_outlives_default_lookback_until_cleared():
    """Authority overrides win until cleared, however old (rule 6); no fixed read window drops them."""
    rt = AppRuntime(_marker_spec())
    src = InMemoryEventSource()
    lat, lon = rt.grid.to_latlon(200.0, 200.0)
    src.add("test-app", "marker", [{"t": T0, "lat": float(lat), "lon": float(lon)}])
    for dt in (60, LOOKBACK_S + 1, 6 * 3600):
        assert tick_app(rt, src, now=T0 + dt, clock=SimClock(T0 + dt)).override.max() == 1.0, dt
    src.add("test-app", "marker", [{"t": T0 + 7 * 3600, "lat": float(lat), "lon": float(lon), "cleared": True}])
    now = T0 + 7 * 3600 + 1
    assert tick_app(rt, src, now=now, clock=SimClock(now)).override.max() == 0.0


def test_override_ttl_from_spec_bounds_its_history():
    rt = AppRuntime(_marker_spec(ttl_s=120))
    assert rt.input_lookback("marker", LOOKBACK_S) == 120
    src = InMemoryEventSource()
    lat, lon = rt.grid.to_latlon(200.0, 200.0)
    src.add("test-app", "marker", [{"t": T0, "lat": float(lat), "lon": float(lon)}])
    assert tick_app(rt, src, now=T0 + 60, clock=SimClock(T0 + 60)).override.max() == 1.0
    assert tick_app(rt, src, now=T0 + 121, clock=SimClock(T0 + 121)).override.max() == 0.0


def test_declared_operator_horizon_is_used():
    rt = AppRuntime(spec())  # density: half_life_s = 60, max_age_half_lives default 8
    assert rt.input_lookback("crowd", LOOKBACK_S) == registry.horizon_s("density", rt.op_params["crowd.density"])
    assert rt.input_lookback("crowd", LOOKBACK_S) == 480


def test_hotspot_matching_scales_with_cell_size():
    big = spec(grid={"cell_m": 50, "bbox": {"min_lat": 45.0, "min_lon": 10.0, "max_lat": 45.045, "max_lon": 10.0635}})
    rt = AppRuntime(big)
    src = InMemoryEventSource()
    ids = []
    for k in range(3):  # the cluster moves 100 m per tick: 2 cells here, far beyond a fixed 30 m
        t = T0 + 2 * k
        src.add("test-app", "crowd", cluster(rt.grid, 2000 + 100 * k, 2000, 40, t, seed=k, spread=20))
        res = tick_app(rt, src, now=t, clock=SimClock(t))
        ids.append(max(res.hotspots, key=lambda h: h.peak).id)
    assert ids == ["h1", "h1", "h1"]


def test_hotspot_threshold_comes_from_the_spec():
    def peak_hotspots(threshold):
        s = spec()
        s = s.model_copy(update={"outputs": s.outputs.model_copy(update={"hotspot_threshold": threshold})})
        rt = AppRuntime(s)
        src = InMemoryEventSource()
        src.add("test-app", "crowd", cluster(rt.grid, 200, 300, 30, T0))
        res = tick_app(rt, src, now=T0, clock=SimClock(T0))
        return res, sum(h.n_cells for h in res.hotspots)

    res, cells_default = peak_hotspots(0.5)
    _, cells_low = peak_hotspots(0.1)
    _, cells_high = peak_hotspots(1.0)
    assert cells_low > cells_default > cells_high
    assert int((res.score >= 0.5).sum()) == cells_default


def test_each_operator_gets_only_its_own_history(monkeypatch):
    """An input mixing an override (all history) with a regular operator: the regular one still
    sees only its default lookback, not everything the override needs."""
    seen: dict[str, float] = {}
    info = registry.OPERATORS["density"]

    def spy(events, params, trust, clock, grid):
        seen["oldest"] = float(events.t.min()) if len(events) else float("nan")
        return info.fn(events, params, trust, clock, grid)

    monkeypatch.setitem(registry.OPERATORS, "density", info.__class__(**{**info.__dict__, "fn": spy}))
    mixed = {
        "id": "marker",
        "kind": "push",
        "shape": "fixed_point",
        "tier": "locked",
        "meaning": "an authority point",
        "operators": [
            {"op": "override", "weight": 0, "radius_m": 30},
            {"op": "density", "weight": 0.5, "half_life_s": 60, "sigma_m": 15},
        ],
    }
    rt = AppRuntime(spec(inputs=[mixed]))
    src = InMemoryEventSource()
    lat, lon = rt.grid.to_latlon(200.0, 200.0)
    now = T0 + 3 * 3600
    src.add("test-app", "marker", [{"t": T0, "lat": float(lat), "lon": float(lon)}])
    src.add("test-app", "marker", [{"t": now - 10, "lat": float(lat), "lon": float(lon)}])
    res = tick_app(rt, src, now=now, clock=SimClock(now))
    assert res.override.max() == 1.0  # the override still sees the 3-hour-old point
    assert seen["oldest"] >= now - LOOKBACK_S  # density does not
