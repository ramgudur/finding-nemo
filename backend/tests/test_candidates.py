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


def test_registered_generators_cover_apps_with_and_without_tracks():
    assert set(registry.GENERATORS) == {"dwell_clusters", "flow_endpoints", "event_clusters", "persistent_hotspots"}
    assert {g.reads_op for g in registry.GENERATORS.values()} == {
        "dwell",
        "flow_source",
        "event_points",
        registry.HOTSPOTS,
    }
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


# -- apps without tracks: hotspots and documents (neutral synthetic scenarios) -------------------


def _hotspot(x, y, radius=30.0, layers=("a.density",), score=0.9):
    from app.core.models import Hotspot

    lat, lon = _ll(x, y)
    return Hotspot(
        id="h",
        lat=lat,
        lon=lon,
        radius_m=radius,
        score=score,
        mean=score * 0.7,
        area_m2=3.14 * radius**2,
        n_cells=5,
        persisted_ticks=0,
        contributing_layers=list(layers),
        visibility="authority",
    )


def test_persistent_hotspots_from_the_ticks_hotspots():
    drafts, errors = registry.generate({}, {}, GRID, hotspots=[_hotspot(100, 100), _hotspot(400, 400, radius=2.0)])
    assert errors == [] and [d.source for d in drafts] == ["persistent_hotspots"] * 2
    a, b = drafts
    assert a.contributing_layers == ["a.density"] and a.stats["peak_score"] == 0.9
    assert b.radius_m == GRID.cell_m / 2  # floored at half a cell
    assert registry.generate({}, {}, GRID, hotspots=[])[0] == []


def test_hotspot_on_an_operator_candidate_is_not_offered_twice():
    lat, lon = _ll(250, 250)
    flow = {"point": {"lat": lat, "lon": lon}, "kind": "source", "n_tracks": 10, "uncertainty_m": 5.0}
    drafts, _ = registry.generate(
        {"t.flow_source": flow},
        {"t.flow_source": "flow_source"},
        GRID,
        hotspots=[_hotspot(260, 255), _hotspot(50, 450)],
    )
    assert sorted(d.source for d in drafts) == ["flow_endpoints", "persistent_hotspots"]
    hs = next(d for d in drafts if d.source == "persistent_hotspots")
    assert GRID.dist_m({"lat": hs.lat, "lon": hs.lon}, (50.0, 450.0)) < 1.0


def test_hotspot_candidates_keep_their_id_when_contributors_change():
    tr = registry.CandidateTracker()
    (d1,), _ = registry.generate({}, {}, GRID, hotspots=[_hotspot(200, 200, layers=("a.density",))])
    (d2,), _ = registry.generate({}, {}, GRID, hotspots=[_hotspot(205, 200, layers=("a.density", "b.field"))])
    (c1,) = tr.update([d1], GRID)
    (c2,) = tr.update([d2], GRID)
    assert c2.id == c1.id and c2.persisted_ticks == 1


def test_event_clusters_group_nearby_documents():
    def doc(x, y, w):
        lat, lon = _ll(x, y)
        return {"lat": lat, "lon": lon, "w": w}

    ex = {"docs": [doc(100, 100, 0.9), doc(120, 110, 0.5), doc(400, 400, 0.4), doc(300, 300, 0.0)]}
    drafts, errors = registry.generate({"news.event_points": ex}, {"news.event_points": "event_points"}, GRID)
    assert errors == [] and len(drafts) == 2  # zero-weight doc dropped; two places
    big = max(drafts, key=lambda d: d.stats["n_docs"])
    assert big.source == "event_clusters" and big.contributing_layers == ["news.event_points"]
    assert big.stats["n_docs"] == 2 and big.stats["weight"] == pytest.approx(1.4)
    assert GRID.dist_m({"lat": big.lat, "lon": big.lon}, (100.0, 100.0)) < 20.0
    assert registry.generate({"n.e": {"docs": []}}, {"n.e": "event_points"}, GRID)[0] == []


def _no_track_spec() -> AppSpec:
    lat, lon = GRID.to_latlon(np.array([0.0, 500.0]), np.array([0.0, 500.0]))
    return AppSpec.model_validate(
        {
            "app_id": "no-tracks",
            "intent": "where reports concentrate",
            "status": "active",
            "grid": {
                "cell_m": 25.0,
                "bbox": {"min_lat": lat[0], "min_lon": lon[0], "max_lat": lat[1], "max_lon": lon[1]},
            },
            "inputs": [
                {
                    "id": "reports",
                    "kind": "push",
                    "shape": "point",
                    "tier": "flexible",
                    "meaning": "one report at a location",
                    "operators": [{"op": "density", "weight": 1.0}],
                },
                {
                    "id": "docs",
                    "kind": "push",
                    "shape": "document",
                    "tier": "stiff",
                    "meaning": "a located document",
                    "fields": [{"name": "doc_id", "type": "string"}, {"name": "confidence", "type": "number"}],
                    "operators": [{"op": "event_points", "weight": 0.5}],
                },
            ],
        }
    )


def test_tick_gives_candidates_to_an_app_without_tracks():
    """Point reports and documents only: before #160 this app got no candidates at all."""
    rt = AppRuntime(_no_track_spec())
    src = InMemoryEventSource()
    rng = np.random.default_rng(3)
    t0 = 1_000_000.0
    reports = []
    for k in range(40):  # reports concentrated around (150, 350)
        lat, lon = _ll(*(np.array([150.0, 350.0]) + rng.normal(0, 15, 2)))
        reports.append({"user_id": f"r{k}", "t": t0 + k, "lat": lat, "lon": lon})
    src.add("no-tracks", "reports", reports)
    lat, lon = _ll(420.0, 80.0)
    src.add(
        "no-tracks",
        "docs",
        [{"user_id": "feed", "t": t0 + 10, "lat": lat, "lon": lon, "doc_id": "d1", "confidence": 0.8}],
    )
    r1 = tick_app(rt, src, now=t0 + 60, clock=SimClock(t0 + 60))
    r2 = tick_app(rt, src, now=t0 + 62, clock=SimClock(t0 + 62))
    assert not r1.errors
    sources = sorted(c.source for c in r1.candidates)
    assert "persistent_hotspots" in sources and "event_clusters" in sources
    hot1 = next(c for c in r1.candidates if c.source == "persistent_hotspots")
    hot2 = next(c for c in r2.candidates if c.source == "persistent_hotspots")
    assert hot2.id == hot1.id and hot2.persisted_ticks == hot1.persisted_ticks + 1
    assert GRID.dist_m({"lat": hot1.lat, "lon": hot1.lon}, (150.0, 350.0)) < 40.0
    doc = next(c for c in r1.candidates if c.source == "event_clusters")
    assert doc.contributing_layers == ["docs.event_points"]
