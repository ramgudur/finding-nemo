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

import re
from typing import Any

from app.connectors.presets import PRESETS

FIELD_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")  # the same pattern as `InputField.name`
RESERVED = frozenset({"user_id", "lat", "lon", "ts"})  # set by the connector itself, never a field
MAX_CUSTOM_FIELDS = 32
MAX_PATH_CHARS = 200


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
