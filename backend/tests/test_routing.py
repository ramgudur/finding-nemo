from datetime import UTC, datetime

import numpy as np
import pytest

from app.core.models import POI, AppSpec, DestinationPoint
from app.engine import routing
from app.engine.grid import Grid
from app.tools import message_guard as mg

# Synthetic ~500 m box, 10 m cells: 50 x 50. Nothing app-specific.
BBOX = {"min_lat": 45.0, "min_lon": 10.0, "max_lat": 45.0045, "max_lon": 10.00635}
NOW = datetime(2026, 11, 8, tzinfo=UTC)


def make_spec(**routing_kw) -> AppSpec:
    return AppSpec.model_validate(
        {
            "app_id": "route-app",
            "intent": "test",
            "status": "active",
            "grid": {"cell_m": 10, "bbox": BBOX},
            "outputs": {
                "poi_labels": {
                    "label_set": [
                        {"label": "exit", "destination": True},
                        {"label": "hidden group", "sensitive": True},
                    ]
                }
            },
            "routing": {"enabled": True, "alpha": 4.0, "impassable_score": 0.8, **routing_kw},
        }
    )


@pytest.fixture
def grid() -> Grid:
    return Grid.from_spec(make_spec().grid)


def pt(grid: Grid, x: float, y: float) -> tuple[float, float]:
    lat, lon = grid.to_latlon(x, y)
    return float(lat), float(lon)


def dest(grid: Grid, x: float, y: float, label: str = "exit") -> DestinationPoint:
    lat, lon = pt(grid, x, y)
    return DestinationPoint(lat=lat, lon=lon, label=label)


def poi(grid: Grid, x: float, y: float, label: str, status: str = "active") -> POI:
    lat, lon = pt(grid, x, y)
    return POI(
        id=f"p-{label}-{x}",
        candidate_id="c",
        lat=lat,
        lon=lon,
        radius_m=10,
        label=label,
        confidence=0.9,
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


def plan(grid, score, start, dests, spec=None):
    spec = spec or make_spec()
    return routing.plan_route(
        grid,
        score,
        start,
        dests,
        alpha=spec.routing.alpha,
        impassable_score=spec.routing.impassable_score,
        destination_max_score=routing.destination_threshold(spec),
    )


def test_straight_route_on_empty_grid(grid):
    score = grid.zeros()
    out = plan(grid, score, pt(grid, 55, 55), [dest(grid, 455, 55)])
    assert out.reason == "ok"
    r = out.route
    assert r.distance_m == pytest.approx(400, abs=15)
    assert all(rr == 5 for rr, _ in r.cells)  # stays on its row
    assert r.points[0] == pt(grid, 55, 55) and r.points[-1] == pt(grid, 455, 55)


def test_route_goes_around_a_blocked_wall(grid):
    score = grid.zeros()
    score[0:40, 25] = 0.9  # wall with a gap at the top (rows 40..49)
    out = plan(grid, score, pt(grid, 55, 55), [dest(grid, 455, 55)])
    assert out.route is not None
    assert all(score[c] < 0.8 for c in out.route.cells[1:])
    assert max(r for r, _ in out.route.cells) >= 40  # used the gap
    assert out.route.distance_m > 400


def test_no_diagonal_squeeze_between_blocked_cells(grid):
    score = grid.zeros()
    score[:, :] = 0.9
    score[5, 5] = score[6, 6] = 0.0  # only diagonal contact
    score[5, 4] = 0.0  # start
    out = plan(grid, score, pt(grid, 45, 55), [dest(grid, 65, 65)])
    assert out.route is None and "blocked" in out.reason


def test_alpha_prefers_low_score_detour(grid):
    score = grid.zeros()
    score[3:8, 20:30] = 0.6  # passable but costly band on the direct line
    out = plan(grid, score, pt(grid, 55, 55), [dest(grid, 455, 55)])
    assert all(score[c] < 0.6 for c in out.route.cells)


def test_lowest_cost_destination_wins_and_unsafe_ones_are_skipped(grid):
    score = grid.zeros()
    score[45:50, 45:50] = 0.6  # >= hotspot threshold 0.5: not a usable destination
    near_hot = dest(grid, 475, 475, "exit")
    far = dest(grid, 55, 455, "exit")
    near = dest(grid, 155, 55, "exit")
    out = plan(grid, score, pt(grid, 55, 55), [near_hot, far, near])
    assert out.route.destination == near
    assert any("destination area scores" in s for s in out.skipped)


def test_unreachable_and_outside(grid):
    score = grid.zeros()
    score[:, 25] = 0.95
    out = plan(grid, score, pt(grid, 55, 55), [dest(grid, 455, 55)])
    assert out.route is None and "reachable" in out.reason
    assert plan(grid, score, (0.0, 0.0), [dest(grid, 455, 55)]).reason == "start is outside the grid"
    assert plan(grid, grid.zeros(), pt(grid, 55, 55), []).reason == "no usable destination"


def test_collect_destinations_uses_active_destination_pois_and_spec_points(grid):
    spec = make_spec(destination_points=[{"lat": 45.001, "lon": 10.001, "label": "gate"}])
    pois = [poi(grid, 100, 100, "exit"), poi(grid, 200, 200, "exit", "retired"), poi(grid, 300, 300, "hidden group")]
    labels = [d.label for d in routing.collect_destinations(spec, pois)]
    assert labels == ["exit", "gate"]


def test_text_is_neutral_and_coordinate_free(grid):
    score = grid.zeros()
    score[20:25, 0:5] = 0.9  # danger to the north of the start
    out = plan(grid, score, pt(grid, 25, 55), [dest(grid, 455, 55)])
    avoid = routing.avoid_direction(grid, score, pt(grid, 25, 55), 0.5)
    text = routing.instruction_text(out.route, grid, avoid)
    assert text.startswith("Head east") and "to exit" in text and "Avoid the area to the north" in text
    assert mg._COORD_RE.search(text) is None
    assert len(text) <= mg.MAX_CHARS


# -- message guard ---------------------------------------------------------------


def run(grid, score, start_xy, pois, spec=None):
    spec = spec or make_spec()
    lat, lon = pt(grid, *start_xy)
    return mg.safe_instruction(spec=spec, grid=grid, score=score, lat=lat, lon=lon, pois=pois, now=NOW)


def test_safe_instruction_passes_all_checks(grid):
    ins = run(grid, grid.zeros(), (55, 55), [poi(grid, 455, 55, "exit")])
    assert ins.guard.passed and ins.destination.label == "exit"
    assert {c.name for c in ins.guard.checks} == {
        "destination_exists",
        "destination_safe",
        "route_passable",
        "no_coordinates",
        "no_sensitive_labels",
        "length",
    }
    assert ins.route[0] == pt(grid, 55, 55) and ins.distance_m > 0


def test_fallback_when_nothing_reachable(grid):
    score = grid.zeros()
    score[:, 25] = 0.95
    ins = run(grid, score, (55, 55), [poi(grid, 455, 55, "exit")], make_spec(fallback_text="Stay put."))
    assert ins.text == "Stay put." and ins.destination is None and ins.route == []
    assert not ins.guard.passed and ins.guard.checks[0].name == "route_found"


def test_instructions_never_contain_coordinates_or_sensitive_labels(grid):
    """CLAUDE.md required test."""
    spec = make_spec()
    other_users = [pt(grid, 100 + 37 * i, 200 + 11 * i) for i in range(5)]
    rng = np.random.default_rng(0)
    routed = 0
    for _ in range(25):
        score = np.clip(rng.random(grid.shape) * 0.7, 0, 1)
        start = (float(rng.uniform(20, 480)), float(rng.uniform(20, 480)))
        pois = [poi(grid, 455, 455, "exit"), poi(grid, 45, 455, "exit"), poi(grid, 250, 250, "hidden group")]
        ins = run(grid, score, start, pois, spec)
        assert "hidden group" not in ins.text.lower()
        for lat, lon in other_users:
            assert f"{lat:.4f}" not in ins.text and f"{lon:.4f}" not in ins.text
        assert mg._COORD_RE.search(ins.text) is None
        assert len(ins.text) <= 360
        routed += ins.destination is not None
    assert routed >= 10  # mostly real routed texts, not just the fallback


def test_guard_rejects_bad_texts_and_routes(grid):
    spec = make_spec()
    score = grid.zeros()
    d = dest(grid, 455, 55)
    out = plan(grid, score, pt(grid, 55, 55), [d])

    def check(text, destination=d, points=out.route.points, s=score):
        return mg.check_instruction(
            text=text, destination=destination, route_points=points, spec=spec, grid=grid, score=s, destinations=[d]
        )

    failed = lambda res: {c.name for c in res.checks if not c.passed}  # noqa: E731
    assert check("Head east to exit.").passed
    assert failed(check("Meet at 45.00123, 10.00456.")) == {"no_coordinates"}
    assert failed(check("Join the Hidden Group by the exit.")) == {"no_sensitive_labels"}
    assert failed(check("x" * 361)) == {"length"}
    assert "destination_exists" in failed(check("Go.", destination=dest(grid, 255, 255, "exit")))
    hot = score.copy()
    hot[5, 30] = 0.95  # the grid changed under the route
    assert failed(check("Go.", s=hot)) == {"route_passable"}
    unsafe = score.copy()
    unsafe[5, 45] = 0.6
    assert "destination_safe" in failed(check("Go.", s=unsafe))
    gappy = [out.route.points[0], out.route.points[-1]]
    assert failed(check("Go.", points=gappy)) == {"route_passable"}
    # the guard's own report never repeats a sensitive label
    res = check("Join the Hidden Group.")
    assert all("hidden" not in c.detail.lower() for c in res.checks)


def test_trace_entry_has_every_check_but_no_text_or_position(grid):
    ins = run(grid, grid.zeros(), (55, 55), [poi(grid, 455, 55, "exit")])
    te = mg.trace_entry("route-app", ins, NOW)
    assert te.agent == "message_guard" and te.result == "passed"
    for c in ins.guard.checks:
        assert c.name in te.tested
    assert ins.text not in te.model_dump_json()
    assert mg._COORD_RE.search(te.model_dump_json()) is None


# -- escape from a blocked area (#245) -------------------------------------------


def _after_escape(route, region):
    """Route cells after the leading run inside the start's blocked region."""
    cells = route.cells
    i = 0
    while i < len(cells) and region[cells[i]]:
        i += 1
    return cells[i:]


def test_start_inside_a_blocked_area_gets_a_route_out(grid):
    score = grid.zeros()
    score[20:30, 20:30] = 0.95  # the caller stands in the middle of it
    start = pt(grid, 255, 255)
    out = plan(grid, score, start, [dest(grid, 455, 255)])
    assert out.reason == "ok", out.reason
    r = out.route
    region = routing.escape_region(score >= 0.8, r.cells[0])
    assert region[r.cells[0]] and r.escape_cells >= 2
    assert all(score[c] < 0.8 for c in _after_escape(r, region))  # once out, never in a blocked cell
    text = routing.instruction_text(r, grid, None)
    assert text.startswith("Leave this area now: head east") and "to exit" in text
    assert mg._COORD_RE.search(text) is None and len(text) <= mg.MAX_CHARS

    ins = run(grid, score, (255, 255), [poi(grid, 455, 255, "exit")])
    assert ins.guard.passed, ins.guard.checks
    assert ins.text.startswith("Leave this area now") and ins.destination.label == "exit"


def test_escape_never_crosses_a_separate_blocked_area(grid):
    score = grid.zeros()
    score[20:30, 20:30] = 0.95  # where the caller is
    score[0:45, 35] = 0.95  # a separate wall between them and the destination, gap at rows 45..49
    out = plan(grid, score, pt(grid, 255, 255), [dest(grid, 455, 255)])
    assert out.route is not None
    region = routing.escape_region(score >= 0.8, out.route.cells[0])
    assert not region[:, 35].any()  # the wall is not part of the caller's region
    rest = _after_escape(out.route, region)
    assert all(score[c] < 0.8 for c in rest)
    assert max(r for r, _ in rest) >= 45  # went round through the gap
    # with no gap the wall can't be crossed, even by someone escaping
    score[:, 35] = 0.95
    out = plan(grid, score, pt(grid, 255, 255), [dest(grid, 455, 255)])
    assert out.route is None and "reachable" in out.reason


def test_escape_routes_never_re_enter_the_start_region(grid):
    rng = np.random.default_rng(7)
    routed = 0
    for _ in range(20):
        score = np.clip(rng.random(grid.shape) * 0.6, 0, 1)
        r0, c0 = (int(v) for v in rng.integers(10, 40, size=2))
        hh, ww = (int(v) for v in rng.integers(3, 12, size=2))
        score[r0 - hh // 2 : r0 + hh, c0 - ww // 2 : c0 + ww] = 0.9
        score[r0 + hh - 2 : r0 + hh, c0 - ww // 2 : c0 + 1] = 0.3  # a notch, so cutting back in could pay
        start = pt(grid, c0 * 10 + 5, r0 * 10 + 5)
        out = plan(grid, score, start, [dest(grid, 455, 455), dest(grid, 45, 455)])
        if out.route is None:
            continue
        routed += 1
        region = routing.escape_region(score >= 0.8, out.route.cells[0])
        assert all(not region[c] and score[c] < 0.8 for c in _after_escape(out.route, region))
        res = mg._route_check(grid, score, out.route.points, 0.8)
        assert res.passed, res.detail
    assert routed >= 10


def test_start_outside_any_blocked_area_is_unchanged(grid):
    score = grid.zeros()
    score[20:30, 20:30] = 0.95
    out = plan(grid, score, pt(grid, 55, 255), [dest(grid, 455, 255)])
    assert out.route.escape_cells == 0
    assert all(score[c] < 0.8 for c in out.route.cells)
    assert routing.instruction_text(out.route, grid, None).startswith("Head ")
    assert not routing.escape_region(score >= 0.8, out.route.cells[0]).any()


def test_guard_allows_only_the_escape_from_the_start_region(grid):
    score = grid.zeros()
    score[20:30, 20:30] = 0.95
    out = plan(grid, score, pt(grid, 255, 255), [dest(grid, 455, 255)])
    assert mg._route_check(grid, score, out.route.points, 0.8).passed
    # a different blocked area appears on the route after the escape: rejected
    later = score.copy()
    r, c = _after_escape(out.route, routing.escape_region(score >= 0.8, out.route.cells[0]))[3]
    later[r, c] = 0.95
    assert not routing.escape_region(later >= 0.8, out.route.cells[0])[r, c]  # a separate blocked cell
    assert not mg._route_check(grid, later, out.route.points, 0.8).passed
    # leaving the start region and coming back into it: rejected
    inside, outside = pt(grid, 255, 255), pt(grid, 305, 255)
    back = [inside, pt(grid, 265, 255), pt(grid, 275, 255), pt(grid, 285, 255), pt(grid, 295, 255), outside,
            pt(grid, 295, 255)]  # fmt: skip
    res = mg._route_check(grid, score, back, 0.8)
    assert not res.passed and "impassable" in res.detail
    # starting outside, crossing the region is rejected as before
    across = [pt(grid, 195, 255)] + [pt(grid, x, 255) for x in range(205, 315, 10)]
    assert not mg._route_check(grid, score, across, 0.8).passed
