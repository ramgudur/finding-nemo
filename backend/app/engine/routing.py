"""Grid routing for end-user instructions (CLAUDE.md "End-user instructions").

- Graph: grid cells, 8 neighbours. Step cost = ``step_m * (1 + alpha * score(cell entered))``.
- Cells with ``score >= impassable_score`` are blocked, and a diagonal step may not cut past a
  blocked corner cell.
- Escape (#245): a caller standing inside a blocked area must still be able to leave it. The
  blocked cells connected to the start cell (8-neighbour; ``escape_region``) are passable, at their
  high step cost, but only while the route is still inside that region: once it steps out it can
  never re-enter it, and every other blocked region stays blocked. With the start outside any
  blocked cell the region is empty and routing is unchanged.
- Start: the caller's own posted position. Destinations: active POIs whose label is a
  ``destination`` label, plus ``routing.destination_points``. A destination is usable only if its
  cell is inside the grid and below both the impassable score and the hotspot threshold.
- One Dijkstra search from the start; the first destination cell settled is the lowest-cost
  reachable one. Nothing reachable -> no route (the caller sends ``routing.fallback_text``).

Text is built by code from the route only: direction, distance, destination label, the direction
of the highest-score area to avoid. It names nothing the grid doesn't know (no floors, rooms or
entity types) and never contains coordinates. Pure numpy + heapq; no clock, no I/O.
"""

from __future__ import annotations

import heapq
import math
from collections import deque
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import numpy as np

from app.core.models import POI, AppSpec, DestinationPoint
from app.engine.grid import Grid

SQRT2 = math.sqrt(2.0)
_NEIGHBOURS = [(-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0)] + [
    (dr, dc, SQRT2) for dr in (-1, 1) for dc in (-1, 1)
]
_COMPASS = ["north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"]
HEADING_LOOKAHEAD_CELLS = 4  # the first direction is read this far along the route
AVOID_RADIUS_CELLS = 20  # only areas this close to the caller are worth naming


@dataclass(frozen=True)
class Route:
    destination: DestinationPoint
    cells: list[tuple[int, int]]  # start cell .. destination cell
    points: list[tuple[float, float]]  # [lat, lon]: exact start, cell centres, exact destination
    distance_m: float
    cost: float
    escape_cells: int = 0  # route cells (start included) inside the blocked region the caller started in


@dataclass(frozen=True)
class RouteOutcome:
    route: Route | None
    reason: str  # why there's no route, or "ok"
    skipped: list[str] = field(default_factory=list)  # destinations left out, with reasons


def destination_threshold(spec: AppSpec) -> float:
    """A usable destination must score below this: never inside a hotspot or an impassable cell."""
    return min(spec.routing.impassable_score, spec.outputs.hotspot_threshold)


def escape_region(blocked: np.ndarray, start: tuple[int, int]) -> np.ndarray:
    """Boolean ``[H, W]`` mask of the blocked cells 8-connected to ``start`` (empty if it isn't blocked).

    This is the blocked area the caller stands in; a route may cross it only to get out.
    """
    blocked = np.asarray(blocked, dtype=bool)
    region = np.zeros(blocked.shape, dtype=bool)
    if not blocked[start]:
        return region
    h, w = blocked.shape
    region[start] = True
    queue = deque([start])
    while queue:
        r, c = queue.popleft()
        for dr, dc, _ in _NEIGHBOURS:
            rr, cc = r + dr, c + dc
            if 0 <= rr < h and 0 <= cc < w and blocked[rr, cc] and not region[rr, cc]:
                region[rr, cc] = True
                queue.append((rr, cc))
    return region


def collect_destinations(spec: AppSpec, pois: Iterable[POI]) -> list[DestinationPoint]:
    """Active POIs with a destination label, then the spec's fixed destination points."""
    dest_labels = {lbl.label for lbl in spec.outputs.poi_labels.label_set if lbl.destination}
    out = [
        DestinationPoint(lat=p.lat, lon=p.lon, label=p.label)
        for p in pois
        if p.status == "active" and p.label in dest_labels
    ]
    out.extend(spec.routing.destination_points)
    return out


def plan_route(
    grid: Grid,
    score: np.ndarray,
    start: tuple[float, float],
    destinations: Sequence[DestinationPoint],
    *,
    alpha: float,
    impassable_score: float,
    destination_max_score: float | None = None,
) -> RouteOutcome:
    """Lowest-cost route from ``start`` (lat, lon) to any usable destination."""
    score = np.nan_to_num(np.asarray(score, dtype=float), nan=1.0)
    if score.shape != grid.shape:
        raise ValueError(f"score shape {score.shape} != grid {grid.shape}")
    h, w = grid.shape
    blocked = score >= impassable_score
    dest_max = impassable_score if destination_max_score is None else destination_max_score

    sr, sc, inside = grid.cell_of(start[0], start[1])
    if not bool(inside):
        return RouteOutcome(None, "start is outside the grid")
    s = (int(sr), int(sc))

    targets: dict[tuple[int, int], list[DestinationPoint]] = {}
    skipped: list[str] = []
    for d in destinations:
        r, c, ok = grid.cell_of(d.lat, d.lon)
        if not bool(ok):
            skipped.append(f"{d.label}: outside the grid")
            continue
        cell = (int(r), int(c))
        if score[cell] >= dest_max:
            skipped.append(f"{d.label}: destination area scores {score[cell]:.2f} (>= {dest_max:.2f})")
            continue
        targets.setdefault(cell, []).append(d)
    if not targets:
        return RouteOutcome(None, "no usable destination", skipped)

    # Inside the start's blocked region its cells are passable; from anywhere else they're blocked.
    # A region cell can only be entered from another region cell, so a route that has left the
    # region never comes back in, and no other blocked cell is ever passable.
    region = escape_region(blocked, s)
    blocked_inside = blocked & ~region

    step = grid.cell_m
    dist = np.full((h, w), np.inf)
    prev = np.full((h, w, 2), -1, dtype=np.intp)
    dist[s] = 0.0
    heap: list[tuple[float, int, int]] = [(0.0, s[0], s[1])]
    while heap:
        d0, r, c = heapq.heappop(heap)
        if d0 > dist[r, c]:
            continue
        if (r, c) in targets:
            route = _build_route(grid, start, (r, c), targets[(r, c)][0], prev, d0, s, region)
            return RouteOutcome(route, "ok", skipped)
        blk = blocked_inside if region[r, c] else blocked
        for dr, dc, mult in _NEIGHBOURS:
            rr, cc = r + dr, c + dc
            if not (0 <= rr < h and 0 <= cc < w) or blk[rr, cc]:
                continue
            if dr and dc and (blk[r + dr, c] or blk[r, c + dc]):
                continue  # no squeezing diagonally past a blocked corner
            nd = d0 + step * mult * (1.0 + alpha * float(score[rr, cc]))
            if nd < dist[rr, cc]:
                dist[rr, cc] = nd
                prev[rr, cc] = (r, c)
                heapq.heappush(heap, (nd, rr, cc))
    return RouteOutcome(None, "no destination is reachable without crossing a blocked area", skipped)


def _build_route(
    grid: Grid,
    start: tuple[float, float],
    end: tuple[int, int],
    dest: DestinationPoint,
    prev: np.ndarray,
    cost: float,
    s: tuple[int, int],
    region: np.ndarray,
) -> Route:
    cells = [end]
    while cells[-1] != s:
        r, c = prev[cells[-1]]
        cells.append((int(r), int(c)))
    cells.reverse()
    mid = cells[1:-1]
    pts = [(float(start[0]), float(start[1]))]
    if mid:
        lat, lon = grid.cell_center([c[0] for c in mid], [c[1] for c in mid])
        pts += [(float(a), float(o)) for a, o in zip(np.atleast_1d(lat), np.atleast_1d(lon), strict=True)]
    pts.append((float(dest.lat), float(dest.lon)))
    x, y = grid.to_xy([p[0] for p in pts], [p[1] for p in pts])
    distance = float(np.hypot(np.diff(x), np.diff(y)).sum())
    escape = sum(1 for cell in cells if region[cell])
    return Route(destination=dest, cells=cells, points=pts, distance_m=distance, cost=float(cost), escape_cells=escape)


# -- text ------------------------------------------------------------------------


def compass(dx: float, dy: float) -> str:
    """8-point compass word for a local-meter vector (x east, y north)."""
    ang = (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0
    return _COMPASS[int((ang + 22.5) // 45) % 8]


def _round_distance(m: float) -> int:
    step = 10 if m < 200 else 50 if m < 1000 else 100
    return max(step, int(round(m / step) * step))


def avoid_direction(grid: Grid, score: np.ndarray, start: tuple[float, float], min_score: float) -> str | None:
    """Direction of the highest-score cell near the caller, if any reaches ``min_score``."""
    sr, sc, inside = grid.cell_of(start[0], start[1])
    if not bool(inside):
        return None
    r0, c0 = int(sr), int(sc)
    k = AVOID_RADIUS_CELLS
    win = score[max(0, r0 - k) : r0 + k + 1, max(0, c0 - k) : c0 + k + 1]
    if win.size == 0 or float(win.max()) < min_score:
        return None
    r, c = np.unravel_index(int(np.argmax(win)), win.shape)
    r, c = r + max(0, r0 - k), c + max(0, c0 - k)
    if (r, c) == (r0, c0):
        return None  # the caller is in it: direction is meaningless
    return compass(float(c - c0), float(r - r0))


def instruction_text(route: Route, grid: Grid, avoid: str | None, *, leave: bool = False) -> str:
    """Code-built instruction: direction, distance, destination label, area to avoid.

    ``leave`` (the caller stands in a high-score area), or a route that starts by escaping a blocked
    area, puts "Leave this area now" first, so the first thing read is to get out.
    """
    pts = route.points
    if route.distance_m < grid.cell_m:
        return f"You are at {route.destination.label}."
    x, y = grid.to_xy([p[0] for p in pts], [p[1] for p in pts])
    i = min(len(pts) - 1, HEADING_LOOKAHEAD_CELLS)
    heading = compass(float(x[i] - x[0]), float(y[i] - y[0]))
    dist = _round_distance(route.distance_m)
    if leave or route.escape_cells > 0:
        text = f"Leave this area now: head {heading} and continue about {dist} m to {route.destination.label}."
    else:
        text = f"Head {heading} and continue about {dist} m to {route.destination.label}."
    if avoid:
        text += f" Avoid the area to the {avoid}."
    return text
