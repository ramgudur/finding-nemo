"""Message guard: every end-user instruction passes these checks before it is sent (CLAUDE.md).

Checks (each one is reported, pass or fail, and traced):
1. ``destination_exists``    the destination is one of the app's current destinations.
2. ``destination_safe``      its cell scores below min(impassable_score, hotspot_threshold).
3. ``route_passable``        every step is inside the grid, adjacent to the last, and below
                             impassable_score on the *current* grid. One exception, the escape:
                             a caller inside a blocked area may cross the blocked cells connected
                             to their own start cell (``routing.escape_region``), only as the
                             first part of the route. Any other blocked cell, or coming back into
                             that region after leaving it, fails.
4. ``no_coordinates``        the text holds no coordinate-like numbers (so no one's position,
                             including other users', can leak through it).
5. ``no_sensitive_labels``   the text names no ``sensitive`` POI label.
6. ``length``                at most 360 characters.

``safe_instruction`` is the one call the instructions API needs: route (to a destination, or to
the nearest safe cell when no destination is within a reasonable distance) -> code-built text ->
guard. If anything fails, the caller gets ``routing.fallback_text`` with no destination and no
route. Rephrasing (``routing.rephrase``) is not built yet; when it is, a rephrased text must pass
the same checks or the code-built text is sent.

The guard only reads the caller's own posted position; it never looks anyone up (rule 8).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import datetime

import numpy as np

from app.core.models import POI, AppSpec, DestinationPoint, GuardCheck, GuardResult, Instruction, TraceEntry
from app.engine import routing
from app.engine.grid import Grid

MAX_CHARS = 360
# Decimal numbers with 4+ fraction digits, or two decimals separated by a comma: lat/lon-like.
_COORD_RE = re.compile(r"-?\d{1,3}\.\d{4,}|-?\d{1,3}\.\d+\s*,\s*-?\d{1,3}\.\d+")


def _check(name: str, passed: bool, detail: str = "") -> GuardCheck:
    return GuardCheck(name=name, passed=bool(passed), detail=detail)


def check_instruction(
    *,
    text: str,
    destination: DestinationPoint | None,
    route_points: Sequence[tuple[float, float]],
    spec: AppSpec,
    grid: Grid,
    score: np.ndarray,
    destinations: Sequence[DestinationPoint],
) -> GuardResult:
    """Run every check on an instruction; ``passed`` only if all pass."""
    checks: list[GuardCheck] = []
    score = np.nan_to_num(np.asarray(score, dtype=float), nan=1.0)
    imp = spec.routing.impassable_score

    if destination is None:
        checks.append(_check("destination_exists", False, "no destination"))
        checks.append(_check("destination_safe", False, "no destination"))
    else:
        known = any(
            d.label == destination.label and float(routing_dist(grid, d, destination)) <= grid.cell_m
            for d in destinations
        )
        checks.append(_check("destination_exists", known, "" if known else "not a current destination"))
        r, c, inside = grid.cell_of(destination.lat, destination.lon)
        limit = routing.destination_threshold(spec)
        safe = bool(inside) and float(score[int(r), int(c)]) < limit
        checks.append(
            _check(
                "destination_safe",
                safe,
                "outside the grid" if not inside else f"score {float(score[int(r), int(c)]):.2f} vs limit {limit:.2f}",
            )
        )

    checks.append(_route_check(grid, score, route_points, imp))

    coords = _COORD_RE.search(text or "")
    checks.append(_check("no_coordinates", coords is None, "" if coords is None else "coordinate-like number in text"))

    sensitive = [
        lbl.label
        for lbl in spec.outputs.poi_labels.label_set
        if lbl.sensitive and lbl.label.strip() and lbl.label.lower() in (text or "").lower()
    ]
    # Report only how many matched: the guard's own trace must not repeat a sensitive label.
    checks.append(
        _check("no_sensitive_labels", not sensitive, "" if not sensitive else f"{len(sensitive)} sensitive label(s)")
    )
    checks.append(_check("length", len(text or "") <= MAX_CHARS, f"{len(text or '')} chars"))
    return GuardResult(passed=all(c.passed for c in checks), checks=checks)


def routing_dist(grid: Grid, a: DestinationPoint, b: DestinationPoint) -> float:
    x, y = grid.to_xy([a.lat, b.lat], [a.lon, b.lon])
    return float(np.hypot(x[1] - x[0], y[1] - y[0]))


def _route_check(grid: Grid, score: np.ndarray, pts: Sequence[tuple[float, float]], imp: float) -> GuardCheck:
    if len(pts) < 2:
        return _check("route_passable", False, "no route")
    r, c, inside = grid.cell_of([p[0] for p in pts], [p[1] for p in pts])
    if not bool(np.all(inside)):
        return _check("route_passable", False, "route leaves the grid")
    cells = list(zip(r.tolist(), c.tolist(), strict=True))
    for (r0, c0), (r1, c1) in zip(cells, cells[1:], strict=False):
        if max(abs(r1 - r0), abs(c1 - c0)) > 1:
            return _check("route_passable", False, "route skips cells")
    blocked = score >= imp
    region = routing.escape_region(blocked, cells[0])
    escaping = True  # only the caller's own blocked region, and only as the route's first part
    hot = 0
    for cell in cells[1:]:
        if escaping and region[cell]:
            continue
        escaping = False
        if blocked[cell]:
            hot += 1
    if hot:
        return _check("route_passable", False, f"{hot} route cell(s) at or above impassable score {imp:.2f}")
    return _check("route_passable", True, "leaves the blocked area it starts in" if region.any() else "")


def safe_instruction(
    *,
    spec: AppSpec,
    grid: Grid,
    score: np.ndarray,
    lat: float,
    lon: float,
    pois: Iterable[POI],
    now: datetime,
) -> Instruction:
    """Route from the caller's own position, build the text, guard it; fall back on any failure.

    Modes (#266): ``route`` to a destination, ``escape`` when that route starts by leaving the blocked
    area the caller stands in, ``nearest_safe`` when the caller isn't clear of danger and no destination
    is reachable within a reasonable distance (``routing.too_far``), ``fallback`` otherwise.
    """
    destinations = routing.collect_destinations(spec, pois)
    rs = spec.routing
    dest_max = routing.destination_threshold(spec)
    hot = spec.outputs.hotspot_threshold
    outcome = routing.plan_route(
        grid,
        score,
        (lat, lon),
        destinations,
        alpha=rs.alpha,
        impassable_score=rs.impassable_score,
        destination_max_score=dest_max,
    )
    safe = routing.plan_nearest_safe(
        grid,
        score,
        (lat, lon),
        alpha=rs.alpha,
        impassable_score=rs.impassable_score,
        safe_max_score=dest_max,
        hot_score=hot,
    )
    route = outcome.route
    nearest = safe.route is not None and (route is None or routing.too_far(route, safe.route, grid.cell_m))
    fallback = Instruction(
        text=rs.fallback_text,
        destination=None,
        route=[],
        distance_m=0.0,
        computed_at=now,
        guard=GuardResult(
            passed=False,
            checks=[_check("route_found", False, f"{outcome.reason}; nearest safe area: {safe.reason}")],
        ),
        mode="fallback",
    )
    if nearest:
        assert safe.route is not None
        route = safe.route
        destinations = [*destinations, route.destination]  # code-made from the current grid, never a POI
    if route is None:
        return fallback
    score_arr = np.nan_to_num(np.asarray(score, dtype=float), nan=1.0)
    sr, sc = route.cells[0]
    leave = float(score_arr[sr, sc]) >= hot  # the caller is in a high-score area
    if nearest:
        text = routing.nearest_safe_text(route, grid, leave=leave)
        mode = "nearest_safe"
    else:
        avoid = routing.avoid_direction(grid, score_arr, (lat, lon), hot)
        text = routing.instruction_text(route, grid, avoid, leave=leave)
        mode = "escape" if route.escape_cells > 0 else "route"
        if safe.reason == "already safe" and route.distance_m > routing.far_limit_m(grid.cell_m):
            text = f"{routing.NOT_AT_RISK_TEXT} {text}"  # a long trip is never sent silently
    guard = check_instruction(
        text=text,
        destination=route.destination,
        route_points=route.points,
        spec=spec,
        grid=grid,
        score=score,
        destinations=destinations,
    )
    if not guard.passed:
        return fallback.model_copy(update={"guard": guard})
    return Instruction(
        text=text,
        destination=route.destination,
        route=route.points,
        distance_m=route.distance_m,
        computed_at=now,
        guard=guard,
        mode=mode,
        walk_minutes=routing.walk_minutes(route.distance_m),
    )


def trace_entry(app_id: str, instruction: Instruction, now: datetime) -> TraceEntry:
    """One TraceEntry with every guard check, for the authority trace. No position, no text."""
    checks = "; ".join(
        f"{c.name}={'ok' if c.passed else 'FAIL'}{f' ({c.detail})' if c.detail else ''}"
        for c in instruction.guard.checks
    )
    return TraceEntry(
        t=now,
        app_id=app_id,
        agent="message_guard",
        saw="instruction request",
        tested=checks,
        result="passed" if instruction.guard.passed else "failed",
        action=(f"sent code-built text ({instruction.mode})" if instruction.guard.passed else "sent fallback text"),
        why="every instruction is guarded before it is sent",
    )
