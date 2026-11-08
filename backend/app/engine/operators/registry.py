"""Operator registry. The Compiler sees it through `list_operators`; the tick runs operators through `run`.

An AppSpec operator entry is `{op, weight, noise_floor?, ...params}`. `op` and `weight` belong to
the combine step, so `parse_params` drops them and validates the rest against the params model.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .base import ClockLike, Events, GridLike, OperatorParams, OperatorResult
from .density import DensityParams, density
from .flow_source import FlowSourceParams, flow_source

_SPEC_ONLY_KEYS = {"op", "weight"}


@dataclass(frozen=True)
class OperatorInfo:
    name: str
    fn: Callable[..., OperatorResult]
    params: type[OperatorParams]
    shapes: tuple[str, ...]  # input shapes it accepts (AppSpec `shape`)
    description: str


OPERATORS: dict[str, OperatorInfo] = {
    info.name: info
    for info in (
        OperatorInfo(
            "flow_source",
            flow_source,
            FlowSourceParams,
            ("track",),
            "Net outflow of moving people per cell over a short window, plus a back-projected "
            "point where movement lines meet (source = people leaving, sink = converging); needs tracks.",
        ),
        OperatorInfo(
            "density",
            density,
            DensityParams,
            ("point", "track"),
            "Smoothed density of point events with half-life decay, trust weights and per-user duplicate removal.",
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


def list_operators() -> list[dict[str, Any]]:
    """Compact catalog for the Compiler: name, one-line description, accepted shapes, params schema."""
    return [
        {
            "name": i.name,
            "description": i.description,
            "shapes": list(i.shapes),
            "params": i.params.model_json_schema(),
        }
        for i in OPERATORS.values()
    ]
