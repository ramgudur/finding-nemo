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
    assert h.contributing_layers == ["crowd.density"]


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
    assert len(res.hotspots) == 1 and res.hotspots[0].contributing_layers == ["marker.override"]
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
        ids.append(max(res.hotspots, key=lambda h: h.score).id)
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


def test_hotspot_visibility_follows_contributing_layers():
    """Public only if every contributing layer is a public output; otherwise authority."""
    from app.core.models import Hotspot

    def run(outputs, with_marker=False):
        crowd = spec().inputs[0].model_dump()
        inputs = [crowd]
        if with_marker:
            inputs.append(
                {
                    "id": "marker",
                    "kind": "push",
                    "shape": "fixed_point",
                    "tier": "locked",
                    "meaning": "an authority point",
                    "operators": [{"op": "override", "weight": 0, "radius_m": 30}],
                }
            )
        rt = AppRuntime(spec(inputs=inputs, outputs={"layers": outputs}))
        src = InMemoryEventSource()
        src.add("test-app", "crowd", cluster(rt.grid, 200, 300, 30, T0))
        if with_marker:
            lat, lon = rt.grid.to_latlon(400.0, 100.0)
            src.add("test-app", "marker", [{"t": T0 - 1, "lat": float(lat), "lon": float(lon)}])
        return tick_app(rt, src, now=T0, clock=SimClock(T0)).hotspots

    public = [{"id": "crowd.density", "visibility": "public"}]
    hs = run(public)
    assert all(isinstance(h, Hotspot) for h in hs)
    assert [h.visibility for h in hs] == ["public"]
    assert [h.visibility for h in run([])] == ["authority"]  # layer not exposed publicly
    assert [h.visibility for h in run([{"id": "crowd.density", "visibility": "authority"}])] == ["authority"]
    # an override hotspot lists its override layer (and not crowd's smoothing tail): stays authority
    by_layers = {tuple(h.contributing_layers): h.visibility for h in run(public, with_marker=True)}
    assert by_layers == {("crowd.density",): "public", ("marker.override",): "authority"}


def test_small_private_share_keeps_hotspot_authority():
    """#88 (rule 8): a private layer too small to be listed as a contributor (< 10%) still makes
    the hotspot authority-only once it supplies more than 1% of the score mass."""
    from app.engine.tick import find_hotspots

    crowd = spec().inputs[0].model_dump()
    private = {**crowd, "id": "private", "operators": [{**crowd["operators"][0], "layer_id": ""}]}
    outputs = {"layers": [{"id": "crowd.density", "visibility": "public"}]}  # private.density stays authority
    rt = AppRuntime(spec(inputs=[crowd, private], outputs=outputs))
    h, w = rt.grid.shape
    blob = np.zeros((h, w))
    blob[h // 2 - 2 : h // 2 + 2, w // 2 - 2 : w // 2 + 2] = 1.0
    ids = list(rt.weighted_ids)
    assert ids == ["crowd.density", "private.density"]

    def hotspots(private_share: float):
        normalized = np.stack([blob, blob])
        weights = np.array([1.0 - private_share, private_share])
        score = np.clip(np.tensordot(weights, normalized, axes=1), 0, 1)
        return find_hotspots(rt, score, normalized, weights)

    (h9,) = hotspots(0.09)  # 9% private: not listed, but still decides visibility
    assert h9.contributing_layers == ["crowd.density"]
    assert h9.visibility == "authority"
    (h0,) = hotspots(0.0)  # all public
    assert h0.visibility == "public"
    (tiny,) = hotspots(0.005)  # below the 1% cut: smoothing-level noise doesn't block public
    assert tiny.visibility == "public"


def test_hotspot_model_fields():
    rt = AppRuntime(spec())
    src = InMemoryEventSource()
    src.add("test-app", "crowd", cluster(rt.grid, 200, 300, 30, T0))
    h = tick_app(rt, src, now=T0, clock=SimClock(T0)).hotspots[0]
    assert h.radius_m == pytest.approx(np.sqrt(h.area_m2 / np.pi))
    assert h.area_m2 == h.n_cells * rt.grid.cell_m**2
    assert 0.5 <= h.score <= 1.0 and h.mean <= h.score


# -- #197: max grid (250,000 cells): hotspot labelling and cache memory ---------------------------


def _flood_fill_components(mask):
    """Reference: plain 8-connected flood fill, components in row-major order of their first cell."""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    comps = []
    for r0, c0 in zip(*np.nonzero(mask), strict=True):
        if seen[r0, c0]:
            continue
        stack, cells = [(r0, c0)], []
        seen[r0, c0] = True
        while stack:
            r, c = stack.pop()
            cells.append(r * w + c)
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < h and 0 <= cc < w and mask[rr, cc] and not seen[rr, cc]:
                        seen[rr, cc] = True
                        stack.append((rr, cc))
        comps.append(sorted(int(x) for x in cells))
    return comps


def test_connected_components_match_a_flood_fill():
    from app.engine.tick import _connected_components

    rng = np.random.default_rng(3)
    for _ in range(200):
        m = rng.random((int(rng.integers(1, 30)), int(rng.integers(1, 30)))) < rng.uniform(0.05, 0.9)
        assert [c.tolist() for c in _connected_components(m)] == _flood_fill_components(m)
    snake = np.zeros((41, 41), dtype=bool)  # one long winding component: the slowest case to merge
    snake[::2, :] = True
    snake[1::4, -1] = True
    snake[3::4, 0] = True
    assert [c.tolist() for c in _connected_components(snake)] == _flood_fill_components(snake)
    assert _connected_components(np.zeros((5, 5), dtype=bool)) == []


def _max_grid_spec(n_inputs: int) -> AppSpec:
    from app.core.models import MAX_GRID_CELLS

    side_m = 10 * int(np.sqrt(MAX_GRID_CELLS)) - 1  # just under 500 x 500 cells of 10 m
    lat1 = 45.0 + side_m / 111_194.9
    lon1 = 10.0 + side_m / (111_194.9 * np.cos(np.radians((45.0 + lat1) / 2)))
    base = spec().inputs[0].model_dump(exclude_none=True)
    ops = [{k: v for k, v in o.items() if k != "layer_id"} for o in base["operators"]]
    inputs = [{**base, "id": f"in{i}", "operators": ops} for i in range(n_inputs)]
    s = spec(grid={"cell_m": 10, "bbox": {"min_lat": 45.0, "min_lon": 10.0, "max_lat": lat1, "max_lon": lon1}})
    return spec(grid=s.grid.model_dump(), inputs=inputs)


def test_live_cache_stays_under_its_memory_budget_at_the_max_grid():
    from app.core.models import MAX_GRID_CELLS
    from app.engine.tick import CACHE_BUDGET_BYTES, CACHE_TICKS, TICK_S

    eng = TickEngine(InMemoryEventSource(), clock=SimClock(T0))
    rt = eng.add_app(_max_grid_spec(6))
    h, w = rt.grid.shape
    assert 0.9 * MAX_GRID_CELLS <= h * w <= MAX_GRID_CELLS
    cache_bytes = rt.cache._layers.nbytes + rt.cache._overrides.nbytes + rt.cache._t.nbytes
    assert cache_bytes <= CACHE_BUDGET_BYTES
    # fewer frames, but they still span the full replay window
    assert rt.cache.capacity < CACHE_TICKS
    assert rt.cache.capacity * rt.cache_min_gap_s == pytest.approx(CACHE_TICKS * TICK_S)


def test_small_apps_and_backtest_replays_keep_every_tick():
    from app.engine.tick import CACHE_TICKS

    eng = TickEngine(InMemoryEventSource(), clock=SimClock(T0))
    rt = eng.add_app(spec())
    assert (rt.cache.capacity, rt.cache_min_gap_s) == (CACHE_TICKS, 0.0)
    # no budget (e.g. a backtest replay): every tick, whatever the grid
    big = AppRuntime(_max_grid_spec(1), cache_ticks=7)
    assert (big.cache.capacity, big.cache_min_gap_s) == (7, 0.0)


def test_budgeted_cache_spaces_frames_over_the_window():
    from app.engine.tick import TICK_S, cache_frame_bytes

    s = spec()
    probe = AppRuntime(s)
    frame = cache_frame_bytes(len(probe.weighted_ids), probe.grid.shape)
    rt = AppRuntime(s, cache_ticks=30, cache_budget_bytes=10 * frame)  # 10 frames over 60 s
    assert rt.cache.capacity == 10 and rt.cache_min_gap_s == pytest.approx(6.0)
    src = InMemoryEventSource()
    for k in range(60):  # 120 s of 2 s ticks
        now = T0 + TICK_S * k
        tick_app(rt, src, now=now, clock=SimClock(now))
    t = rt.cache.window().t
    assert len(t) == 10
    assert np.all(np.diff(t) >= 6.0 - TICK_S / 2)
    assert t[-1] - t[0] >= 50.0  # still spans most of the window


def test_slow_tick_is_logged(monkeypatch, caplog):
    from app.engine import tick as tick_mod

    rt = AppRuntime(spec())
    monkeypatch.setattr(tick_mod, "TICK_S", 0.0)
    with caplog.at_level("WARNING", logger="app.engine.tick"):
        tick_app(rt, InMemoryEventSource(), now=T0, clock=SimClock(T0))
    assert any("over the" in r.getMessage() and "test-app" in r.getMessage() for r in caplog.records)
