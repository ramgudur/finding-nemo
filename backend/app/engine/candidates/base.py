"""Shared types for candidate generators."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.engine.operators.base import GridLike


@dataclass
class CandidateDraft:
    """A candidate before the tracker gives it an id and persisted_ticks."""

    lat: float
    lon: float
    radius_m: float
    source: str  # generator name
    stats: dict[str, float] = field(default_factory=dict)
    contributing_layers: list[str] = field(default_factory=list)


Generator = Callable[[str, Mapping[str, Any], GridLike], list[CandidateDraft]]
