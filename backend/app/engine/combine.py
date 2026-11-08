"""Combine operator layers into the score grid.

    norm(layer) = layer / max(p99(layer), noise_floor)      all-zero -> zeros, never NaN
    score       = clamp(Σ wᵢ · norm(layerᵢ), 0, 1)
    score       = max(score, override)                       authority overrides win, applied last

Trust is applied inside operators, so normalized layers don't depend on weights. The
normalized stack and override mask are what layer_cache stores for backtests.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

P99 = 99.0


def normalize(layer: np.ndarray, noise_floor: float) -> np.ndarray:
    """Scale by max(p99, noise_floor). Negative and non-finite cells count as 0."""
    if noise_floor <= 0:
        raise ValueError("noise_floor must be > 0")
    clean = np.nan_to_num(np.asarray(layer, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    clean = np.maximum(clean, 0.0)
    return clean / max(float(np.percentile(clean, P99)), noise_floor)


def normalize_stack(layers: Sequence[np.ndarray] | np.ndarray, noise_floors: Sequence[float]) -> np.ndarray:
    """[L, H, W] raw layers -> [L, H, W] normalized layers."""
    if len(layers) != len(noise_floors):
        raise ValueError("one noise_floor per layer")
    if len(layers) == 0:
        raise ValueError("no layers to normalize")
    return np.stack([normalize(lay, nf) for lay, nf in zip(layers, noise_floors, strict=True)])


def weighted_sum(norm_stack: np.ndarray, weights: Sequence[float] | np.ndarray) -> np.ndarray:
    """clamp(Σ wᵢ · normᵢ, 0, 1) over axis 0 of an [L, H, W] stack."""
    w = np.asarray(weights, dtype=float)
    if w.shape != (norm_stack.shape[0],):
        raise ValueError(f"expected {norm_stack.shape[0]} weights, got shape {w.shape}")
    return np.clip(np.einsum("l,lhw->hw", w, norm_stack), 0.0, 1.0)


def apply_overrides(score: np.ndarray, override: np.ndarray | None) -> np.ndarray:
    """Authority overrides: per-cell max(score, override). `override` is [H, W] in [0, 1]
    (1.0 inside an active override radius, 0 elsewhere)."""
    if override is None:
        return score
    return np.maximum(score, np.clip(override, 0.0, 1.0))


@dataclass
class CombineResult:
    score: np.ndarray  # [H, W] in [0, 1]
    normalized: np.ndarray  # [L, H, W]
    override: np.ndarray  # [H, W]; zeros when there is no override


def combine(
    layers: Sequence[np.ndarray] | np.ndarray,
    noise_floors: Sequence[float],
    weights: Sequence[float] | np.ndarray,
    override: np.ndarray | None = None,
) -> CombineResult:
    norm = normalize_stack(layers, noise_floors)
    ov = np.zeros(norm.shape[1:]) if override is None else np.clip(np.asarray(override, dtype=float), 0.0, 1.0)
    if ov.shape != norm.shape[1:]:
        raise ValueError(f"override shape {ov.shape} != layer shape {norm.shape[1:]}")
    score = apply_overrides(weighted_sum(norm, weights), ov)
    return CombineResult(score=score, normalized=norm, override=ov)
