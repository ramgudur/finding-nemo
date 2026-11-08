"""Operator registry. The Compiler sees it through `list_operators`; the tick runs operators through `run`.

An AppSpec operator entry is `{op, weight, noise_floor?, ...params}`. `op` and `weight` belong to
the combine step, so `parse_params` drops them and validates the rest against the params model.

`horizon_s(name, params)` says how far back an operator reads events (None = all history, e.g. an
override with no ttl stays active until cleared), so the tick fetches enough history and no more.

`role` tells the tick where a layer goes: "layer" layers are normalized and weighted; "override"
layers are 0/1 masks fed to combine's override step (applied after the weighted sum, never weighted).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .base import ClockLike, Events, GridLike, OperatorParams, OperatorResult
from .density import DensityParams, density
from .dwell import DwellParams, dwell
from .event_points import EventPointsParams, event_points
from .field import FieldParams, field
from .flow_source import FlowSourceParams, flow_source
from .override import OverrideParams, override

_SPEC_ONLY_KEYS = {"op", "weight", "layer_id"}


@dataclass(frozen=True)
class OperatorInfo:
    name: str
    fn: Callable[..., OperatorResult]
    params: type[OperatorParams]
    shapes: tuple[str, ...]  # input shapes it accepts (AppSpec `shape`)
    description: str
    role: Literal["layer", "override"] = "layer"
    horizon: Callable[[Any], float | None] = lambda p: None  # seconds of history needed; None = all


OPERATORS: dict[str, OperatorInfo] = {
    info.name: info
    for info in (
        OperatorInfo(
            "flow_source",
            flow_source,
            FlowSourceParams,
            ("track",),
            "Net outflow of moving entities per cell over a short window, plus a back-projected "
            "point where movement lines meet (source = entities leaving, sink = converging); needs tracks.",
            horizon=lambda p: p.window_s,
        ),
        OperatorInfo(
            "density",
            density,
            DensityParams,
            ("point", "track"),
            "Smoothed density of point events with half-life decay, trust weights and per-user duplicate removal.",
            horizon=lambda p: p.half_life_s * p.max_age_half_lives,
        ),
        OperatorInfo(
            "dwell",
            dwell,
            DwellParams,
            ("track",),
            "Stop detection: entities staying within R meters for at least T seconds, as stationary clusters.",
            horizon=lambda p: p.window_s,
        ),
        OperatorInfo(
            "field",
            field,
            FieldParams,
            ("field",),
            "Point or gridded readings interpolated onto the grid; with a vector field (magnitude + direction) "
            "it can carry another layer along it (advection-diffusion).",
            horizon=lambda p: p.max_age_s,
        ),
        OperatorInfo(
            "event_points",
            event_points,
            EventPointsParams,
            ("document",),
            "Geocoded documents as confidence x source reliability, decaying over time.",
            horizon=lambda p: p.half_life_s * p.max_age_half_lives,
        ),
        OperatorInfo(
            "override",
            override,
            OverrideParams,
            ("fixed_point",),
            "Authority fixed points: score forced to 1.0 within a radius until cleared; applied after weighting.",
            role="override",
            horizon=lambda p: p.ttl_s,  # None: active until cleared, so all history
        ),
    )
}


def get(name: str) -> OperatorInfo:
    try:
        return OPERATORS[name]
    except KeyError:
        raise KeyError(f"unknown operator {name!r}; registered: {sorted(OPERATORS)}") from None


def parse_params(name: str, raw: Mapping[str, Any] | None = None) -> OperatorParams:
    data = {k: v for k, v in (raw or {}).items() if k not in _SPEC_ONLY_KEYS}
    return get(name).params.model_validate(data)


def run(
    name: str,
    events: Events,
    params: Mapping[str, Any] | OperatorParams | None,
    trust: Mapping[str, float] | None,
    clock: ClockLike,
    grid: GridLike,
) -> OperatorResult:
    info = get(name)
    p = params if isinstance(params, info.params) else parse_params(name, params)  # type: ignore[arg-type]
    return info.fn(events, p, trust, clock, grid)


def horizon_s(name: str, params: Mapping[str, Any] | OperatorParams | None = None) -> float | None:
    """Seconds of event history the operator reads with these params; None = all history."""
    info = get(name)
    p = params if isinstance(params, info.params) else parse_params(name, params)  # type: ignore[arg-type]
    return info.horizon(p)


def list_operators() -> list[dict[str, Any]]:
    """Compact catalog for the Compiler: name, one-line description, accepted shapes, params schema."""
    return [
        {
            "name": i.name,
            "description": i.description,
            "shapes": list(i.shapes),
            "role": i.role,
            "params": i.params.model_json_schema(),
        }
        for i in OPERATORS.values()
    ]
