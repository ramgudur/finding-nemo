"""Generic JSON API connector: pure extraction, no network (`engine.py` fetches).

A connector's `config` plus its named `preset` (`connectors/presets.py`, data only --
rule 10) decide the request and how to read the response; nothing here is specific to
any one API.

A custom source (no preset) may say how to read its response with two data-only config keys,
the same path semantics as webhook mappings (dotted names with optional list indices, e.g.
`"data.items.0.value"`):

- `response_path`: where the reading lives in the body (`""`, the default, is the body itself).
- `fields`: a list of field names read from that node, or a `{field_name: path}` map with paths
  relative to that node.

Both are validated as data (`check_custom_config`), never evaluated.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from app.connectors.presets import PRESETS

FIELD_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")  # the same pattern as `InputField.name`
RESERVED = frozenset({"user_id", "lat", "lon", "ts"})  # set by the connector itself, never a field
MAX_CUSTOM_FIELDS = 32
MAX_PATH_CHARS = 200
MAX_AREA_POINTS = 100  # hard ceiling on a preset's `area_grid.max_points` (one request per poll)
_M_PER_DEG = 6_371_000.0 * math.pi / 180.0


def resolve_preset(preset: str | None, config: dict[str, Any]) -> dict[str, Any]:
    """The preset's own request/response shape; `config` only ever fills its placeholders,
    never changes the shape itself (that would make the preset code, not config)."""
    if preset is None:
        return {}
    if preset not in PRESETS:
        raise KeyError(f"unknown connector preset {preset!r}; registered: {sorted(PRESETS)}")
    return PRESETS[preset]


def render_query(query: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Substitutes `"{name}"` placeholders in a preset's query template from `config`."""
    out: dict[str, Any] = {}
    for k, v in query.items():
        if isinstance(v, str) and v.startswith("{") and v.endswith("}"):
            name = v[1:-1]
            if name not in config:
                raise KeyError(f"query placeholder {v!r} needs config[{name!r}]")
            out[k] = config[name]
        else:
            out[k] = v
    return out


class PathNotFound(KeyError):
    pass


def dig(data: Any, path: str) -> Any:
    """Dotted path with optional list indices, e.g. "geometry.coordinates.1" (as webhooks)."""
    node = data
    for part in path.split(".") if path else []:
        if isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError) as e:
                raise PathNotFound(path) from e
        elif isinstance(node, dict):
            if part not in node:
                raise PathNotFound(path)
            node = node[part]
        else:
            raise PathNotFound(path)
    return node


def _check_path(path: Any, what: str) -> str:
    if not isinstance(path, str) or len(path) > MAX_PATH_CHARS:
        raise ValueError(f"{what} must be a string path of at most {MAX_PATH_CHARS} characters")
    if path and any(part == "" for part in path.split(".")):
        raise ValueError(f"{what} {path!r} has an empty segment")
    return path


def check_custom_config(config: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """`(response_path, {field_name: path})` from a custom source's config, or ValueError.
    Data only (rule 10): names and paths are checked strings, never code."""
    response_path = _check_path(config.get("response_path", ""), "config.response_path")
    raw = config.get("fields", [])
    if isinstance(raw, list):
        if not all(isinstance(n, str) for n in raw):
            raise ValueError("config.fields must be a list of field names or a {name: path} map")
        mapping = {n: n for n in raw}
    elif isinstance(raw, dict):
        mapping = {}
        for name, path in raw.items():
            mapping[str(name)] = _check_path(path, f"config.fields[{name!r}]")
    else:
        raise ValueError("config.fields must be a list of field names or a {name: path} map")
    if len(mapping) > MAX_CUSTOM_FIELDS:
        raise ValueError(f"config.fields has more than {MAX_CUSTOM_FIELDS} fields")
    for name in mapping:
        if not FIELD_NAME.match(name):
            raise ValueError(f"config.fields: {name!r} is not a valid field name")
        if name in RESERVED:
            raise ValueError(f"config.fields: {name!r} is reserved (set by the connector itself)")
    return response_path, mapping


def field_names(preset: str | None, config: dict[str, Any]) -> list[str]:
    """The field names this source's events carry (a preset's own, or the custom config's)."""
    if preset is not None:
        return list(resolve_preset(preset, config).get("fields", []))
    return list(check_custom_config(config)[1])


def extract_fields(data: Any, spec: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """One event dict from a fetched JSON response: the declared fields read from
    `response_path` (a preset's, else the custom config's), plus `lat`/`lon` from `config`
    (a field-shaped pull input is one site's reading, not many located items). A field missing
    from the response is left out. `ts` isn't set here -- a response's own timestamp carries no
    timezone in e.g. Open-Meteo, and this stays provider-agnostic; the caller
    (`connectors/engine.py`) stamps the fetch time instead."""
    if spec:  # a preset fixes the shape; config never changes it
        response_path = spec.get("response_path", "")
        mapping = {n: n for n in spec.get("fields", [])}
    else:
        response_path, mapping = check_custom_config(config)
    node = dig(data, response_path)
    event: dict[str, Any] = {}
    for name, path in mapping.items():
        try:
            event[name] = dig(node, path)
        except PathNotFound:
            continue
    if "latitude" in config and "longitude" in config:
        event["lat"] = config["latitude"]
        event["lon"] = config["longitude"]
    return event


# -- area grid (a preset's optional `area_grid`, data only) ----------------------------


@dataclass(frozen=True)
class AreaPoint:
    row: int  # 0 = southmost
    col: int  # 0 = westmost
    lat: float
    lon: float

    @property
    def point_id(self) -> str:
        """A stable id per point, so the field operator keeps one latest reading per point."""
        return f"grid-{self.row}-{self.col}"


def area_grid(spec: dict[str, Any]) -> dict[str, Any] | None:
    """A preset's `area_grid` declaration, or None (a custom source never has one)."""
    grid = spec.get("area_grid")
    return grid if isinstance(grid, dict) else None


def area_points(
    min_lat: float, min_lon: float, max_lat: float, max_lon: float, grid: dict[str, Any]
) -> list[AreaPoint]:
    """Cell centres of an n x n split of the bbox, row-major from the south-west. n is the largest
    value with n^2 <= max_points (capped at MAX_AREA_POINTS) and spacing >= min_spacing_m along both
    axes; a small area gets n = 1, its centre."""
    max_points = max(1, min(int(grid.get("max_points", 1)), MAX_AREA_POINTS))
    min_spacing_m = max(float(grid.get("min_spacing_m", 0.0)), 0.0)
    height_m = max(max_lat - min_lat, 0.0) * _M_PER_DEG
    width_m = max(max_lon - min_lon, 0.0) * _M_PER_DEG * math.cos(math.radians((min_lat + max_lat) / 2))
    n = math.isqrt(max_points)
    if min_spacing_m > 0:
        n = min(n, int(min(height_m, width_m) // min_spacing_m))
    n = max(n, 1)
    dlat, dlon = (max_lat - min_lat) / n, (max_lon - min_lon) / n
    return [
        AreaPoint(r, c, round(min_lat + (r + 0.5) * dlat, 5), round(min_lon + (c + 0.5) * dlon, 5))
        for r in range(n)
        for c in range(n)
    ]


def area_config(config: dict[str, Any], grid: dict[str, Any], points: list[AreaPoint]) -> dict[str, Any]:
    """`config` with the grid's lat/lon placeholders filled by the points (comma-joined lists, as
    the preset declares the API accepts). One point stays a plain number."""
    lat_key, lon_key = str(grid.get("lat_key", "latitude")), str(grid.get("lon_key", "longitude"))
    join = str(grid.get("multi_param_join", ","))
    out = dict(config)
    if len(points) == 1:
        out[lat_key], out[lon_key] = points[0].lat, points[0].lon
    else:
        out[lat_key] = join.join(f"{p.lat:.5f}" for p in points)
        out[lon_key] = join.join(f"{p.lon:.5f}" for p in points)
    return out


def extract_area_events(data: Any, spec: dict[str, Any], points: list[AreaPoint]) -> list[dict[str, Any]]:
    """One event per point from a multi-point response: a list with one response per point, in
    order (a single point may come back as the bare object). Each event sits at its own point."""
    items = data if isinstance(data, list) else [data]
    if len(items) != len(points):
        raise ValueError(f"expected {len(points)} point response(s), got {len(items)}")
    events = []
    for item, p in zip(items, points, strict=True):
        event = extract_fields(item, spec, {})
        event.update(user_id=p.point_id, lat=p.lat, lon=p.lon)
        events.append(event)
    return events
