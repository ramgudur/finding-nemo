"""Generic JSON API connector: pure extraction, no network (`engine.py` fetches).

A connector's `config` plus its named `preset` (`connectors/presets.py`, data only --
rule 10) decide the request and how to read the response; nothing here is specific to
any one API.
"""

from __future__ import annotations

from typing import Any

from app.connectors.presets import PRESETS


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


def _dig(data: Any, path: str) -> Any:
    for part in path.split(".") if path else []:
        data = data[part]
    return data


def extract_fields(data: dict[str, Any], spec: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """One event dict from a fetched JSON response: `spec`'s declared `fields` read from
    `response_path`, plus `lat`/`lon` from `config` (a field-shaped pull input is one
    site's reading, not many located items). `ts` isn't set here -- a response's own
    timestamp carries no timezone in e.g. Open-Meteo, and this stays provider-agnostic;
    the caller (`connectors/engine.py`) stamps the fetch time instead."""
    node = _dig(data, spec.get("response_path", ""))
    fields = {name: node[name] for name in spec.get("fields", []) if name in node}
    event: dict[str, Any] = {**fields}
    if "latitude" in config and "longitude" in config:
        event["lat"] = config["latitude"]
        event["lon"] = config["longitude"]
    return event
