"""Ring buffer of per-tick normalized layer stacks [L, H, W] and override masks [H, W].

Backtests replay weights over this history (`window` returns [T, L, H, W]) instead of
re-running operators. Stored as float32 to keep a 10-minute window at 2 s ticks small.
If the layer set or grid changes, call `reset`: old ticks no longer line up with the new layers.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

DTYPE = np.float32


@dataclass
class CacheWindow:
    t: np.ndarray  # [T] tick times, ascending
    layers: np.ndarray  # [T, L, H, W]
    overrides: np.ndarray  # [T, H, W]
    layer_ids: tuple[str, ...]


class LayerCache:
    def __init__(self, capacity: int, layer_ids: Sequence[str], shape: tuple[int, int]):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self.reset(layer_ids, shape)

    def reset(self, layer_ids: Sequence[str], shape: tuple[int, int]) -> None:
        if len(set(layer_ids)) != len(layer_ids):
            raise ValueError("duplicate layer id")
        self.layer_ids = tuple(layer_ids)
        self.shape = (int(shape[0]), int(shape[1]))
        n_layers = len(self.layer_ids)
        self._t = np.full(self.capacity, np.nan)
        self._layers = np.zeros((self.capacity, n_layers, *self.shape), dtype=DTYPE)
        self._overrides = np.zeros((self.capacity, *self.shape), dtype=DTYPE)
        self._next = 0  # slot the next push writes
        self._size = 0

    def __len__(self) -> int:
        return self._size

    def push(self, t: float, normalized: np.ndarray, override: np.ndarray | None = None) -> None:
        if normalized.shape != (len(self.layer_ids), *self.shape):
            raise ValueError(f"stack shape {normalized.shape} != {(len(self.layer_ids), *self.shape)}")
        if self._size and t <= self._t[(self._next - 1) % self.capacity]:
            raise ValueError("ticks must be pushed in increasing time order")
        i = self._next
        self._t[i] = t
        self._layers[i] = normalized
        if override is None:
            self._overrides[i] = 0.0
        elif override.shape != self.shape:
            raise ValueError(f"override shape {override.shape} != {self.shape}")
        else:
            self._overrides[i] = override
        self._next = (i + 1) % self.capacity
        self._size = min(self._size + 1, self.capacity)

    def _order(self) -> np.ndarray:
        """Slot indices, oldest first."""
        start = (self._next - self._size) % self.capacity
        return (start + np.arange(self._size)) % self.capacity

    def window(self, t0: float = -np.inf, t1: float = np.inf) -> CacheWindow:
        """Ticks with t0 <= t <= t1, oldest first. Returns copies."""
        idx = self._order()
        idx = idx[(self._t[idx] >= t0) & (self._t[idx] <= t1)]
        return CacheWindow(
            t=self._t[idx].copy(),
            layers=self._layers[idx],
            overrides=self._overrides[idx],
            layer_ids=self.layer_ids,
        )

    def latest(self) -> CacheWindow | None:
        if not self._size:
            return None
        i = np.array([(self._next - 1) % self.capacity])
        return CacheWindow(
            t=self._t[i].copy(), layers=self._layers[i], overrides=self._overrides[i], layer_ids=self.layer_ids
        )
