import numpy as np
import pytest
from synthetic import LocalGrid

from app.core.clock import SimClock
from app.core.models import AppSpec, Candidate
from app.engine.candidates import registry
from app.engine.candidates.base import CandidateDraft
from app.engine.candidates.flow_endpoints import SIGMA_TO_95
from app.engine.tick import AppRuntime, InMemoryEventSource, tick_app

GRID = LocalGrid()


def _ll(x, y):
    lat, lon = GRID.to_latlon(np.array([x]), np.array([y]))
    return float(lat[0]), float(lon[0])


def test_only_the_two_generators_are_registered():
    assert set(registry.GENERATORS) == {"dwell_clusters", "flow_endpoints"}
    assert {g.reads_op for g in registry.GENERATORS.values()} == {"dwell", "flow_source"}
    for g in registry.GENERATORS.values():
        assert g.description and "\n" not in g.description


def test_dwell_clusters_skips_singletons_and_keeps_stats():
    lat, lon = _ll(100, 100)
    ex = {
        "clusters": [
            {"lat": lat, "lon": lon, "radius_m": 18.0, "n_users": 6, "mean_dwell_s": 240.0, "weight": 5.5},
            {"lat": lat, "lon": lon, "radius_m": 5.0, "n_users": 1, "mean_dwell_s": 300.0, "weight": 1.0},
        ]
    }
    drafts, errors = registry.generate({"crowd.dwell": ex}, {"crowd.dwell": "dwell"}, GRID)
    assert errors == [] and len(drafts) == 1
    d = drafts[0]
    assert d.source == "dwell_clusters" and d.contributing_layers == ["crowd.dwell"]
    assert d.stats == {"n_entities": 6.0, "mean_dwell_s": 240.0, "weight": 5.5}
    assert d.radius_m == 18.0


def test_flow_endpoints_radius_from_uncertainty():
    lat, lon = _ll(250, 250)
    ex = {"point": {"lat": lat, "lon": lon}, "kind": "source", "n_tracks": 40, "uncertainty_m": 8.0, "spread_m": 30.0}
    (d,), _ = registry.generate({"t.flow_source": ex}, {"t.flow_source": "flow_source"}, GRID)
    assert d.radius_m == pytest.approx(SIGMA_TO_95 * 8.0)
    assert d.stats["is_source"] == 1.0 and d.stats["uncertainty_m"] == 8.0
    # no uncertainty: spread; tiny values: floored at half a cell
    ex2 = {**ex, "uncertainty_m": None, "kind": "sink"}
    (d2,), _ = registry.generate({"t.flow_source": ex2}, {"t.flow_source": "flow_source"}, GRID)
    assert d2.radius_m == 30.0 and d2.stats["is_source"] == 0.0
    ex3 = {**ex, "uncertainty_m": 0.1}
    (d3,), _ = registry.generate({"t.flow_source": ex3}, {"t.flow_source": "flow_source"}, GRID)
    assert d3.radius_m == GRID.cell_m / 2
    # no point or too few tracks: nothing
    assert registry.generate({"t.f": {"point": None}}, {"t.f": "flow_source"}, GRID)[0] == []
    assert registry.generate({"t.f": {**ex, "n_tracks": 2}}, {"t.f": "flow_source"}, GRID)[0] == []


def test_other_operators_produce_no_candidates_and_errors_dont_stop_the_rest():
    lat, lon = _ll(250, 250)
    good = {"point": {"lat": lat, "lon": lon}, "kind": "source", "n_tracks": 10, "uncertainty_m": 5.0}
    drafts, errors = registry.generate(
        {"a.density": {"n_events": 3}, "b.dwell": {"clusters": [{"n_users": 3}]}, "c.flow_source": good},
        {"a.density": "density", "b.dwell": "dwell", "c.flow_source": "flow_source"},
        GRID,
    )
    assert [d.source for d in drafts] == ["flow_endpoints"]
    assert len(errors) == 1 and "dwell_clusters" in errors[0]


def test_tracker_keeps_ids_and_counts_persistence():
    tr = registry.CandidateTracker()

    def draft(x, y, src="dwell_clusters", layer="crowd.dwell"):
        lat, lon = _ll(x, y)
        return CandidateDraft(lat, lon, 20.0, src, {}, [layer])

    first = tr.update([draft(100, 100), draft(400, 400)], GRID)
    assert [c.persisted_ticks for c in first] == [0, 0] and len({c.id for c in first}) == 2
    second = tr.update([draft(110, 105), draft(400, 400)], GRID)  # small move: same ids
    assert [c.id for c in second] == [c.id for c in first]
    assert [c.persisted_ticks for c in second] == [1, 1]
    third = tr.update([draft(300, 100), draft(400, 400, src="flow_endpoints", layer="t.flow_source")], GRID)
    assert all(c.id not in {x.id for x in first} for c in third)  # moved far / other generator: new
    assert all(c.persisted_ticks == 0 for c in third)
    assert all(isinstance(c, Candidate) for c in third)


def _spec() -> AppSpec:
    lat, lon = GRID.to_latlon(np.array([0.0, 500.0]), np.array([0.0, 500.0]))
    return AppSpec.model_validate(
        {
            "app_id": "cand-app",
            "intent": "candidates through the tick",
            "status": "active",
            "grid": {
                "cell_m": 25.0,
                "bbox": {"min_lat": lat[0], "min_lon": lon[0], "max_lat": lat[1], "max_lon": lon[1]},
            },
            "inputs": [
                {
                    "id": "tracks",
                    "kind": "push",
                    "shape": "track",
                    "tier": "flexible",
                    "meaning": "position pings",
                    "operators": [{"op": "dwell", "weight": 1.0}, {"op": "density", "weight": 1.0}],
                }
            ],
        }
    )


def test_tick_emits_persistent_dwell_candidates():
    rt = AppRuntime(_spec())
    src = InMemoryEventSource()
    rng = np.random.default_rng(0)
    t0 = 1_000_000.0
    rows = []
    for k in range(6):  # six entities staying put near (250, 250) for 5 minutes
        spot = np.array([250.0, 250.0]) + rng.normal(0, 4, 2)
        for t in range(0, 301, 10):
            lat, lon = _ll(*(spot + rng.normal(0, 5, 2)))
            rows.append({"user_id": f"e{k}", "t": t0 + t, "lat": lat, "lon": lon})
    src.add("cand-app", "tracks", rows)
    r1 = tick_app(rt, src, now=t0 + 300, clock=SimClock(t0 + 300))
    r2 = tick_app(rt, src, now=t0 + 302, clock=SimClock(t0 + 302))
    assert [c.source for c in r1.candidates] == ["dwell_clusters"]
    c1, c2 = r1.candidates[0], r2.candidates[0]
    assert c2.id == c1.id and c2.persisted_ticks == c1.persisted_ticks + 1
    assert c1.contributing_layers == ["tracks.dwell"] and c1.stats["n_entities"] == 6
    assert GRID.dist_m({"lat": c1.lat, "lon": c1.lon}, (250.0, 250.0)) < 15.0
    assert not r1.errors
