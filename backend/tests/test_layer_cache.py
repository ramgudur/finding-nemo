import numpy as np
import pytest

from app.engine.layer_cache import LayerCache

SHAPE = (4, 5)
IDS = ("crowd", "flow")


def _stack(v):
    return np.full((len(IDS), *SHAPE), float(v))


def test_window_is_oldest_first_and_wraps():
    c = LayerCache(capacity=3, layer_ids=IDS, shape=SHAPE)
    assert len(c) == 0 and c.latest() is None
    for k in range(5):
        c.push(2.0 * k, _stack(k))
    w = c.window()
    assert len(c) == 3
    np.testing.assert_array_equal(w.t, [4.0, 6.0, 8.0])
    assert w.layers.shape == (3, 2, *SHAPE)
    np.testing.assert_array_equal(w.layers[:, 0, 0, 0], [2, 3, 4])
    assert c.latest().t[0] == 8.0


def test_window_time_range_and_overrides():
    c = LayerCache(capacity=10, layer_ids=IDS, shape=SHAPE)
    ov = np.zeros(SHAPE)
    ov[1, 1] = 1.0
    for k in range(6):
        c.push(float(k), _stack(k), override=ov if k == 3 else None)
    w = c.window(2.0, 4.0)
    np.testing.assert_array_equal(w.t, [2.0, 3.0, 4.0])
    assert w.overrides.shape == (3, *SHAPE)
    assert w.overrides[1, 1, 1] == 1.0 and w.overrides[0].sum() == 0 and w.overrides[2].sum() == 0
    assert w.layer_ids == IDS


def test_window_returns_copies():
    c = LayerCache(capacity=2, layer_ids=IDS, shape=SHAPE)
    c.push(0.0, _stack(1))
    w = c.window()
    w.layers[:] = 99
    assert c.window().layers.max() == 1.0


def test_rejects_bad_input():
    c = LayerCache(capacity=2, layer_ids=IDS, shape=SHAPE)
    with pytest.raises(ValueError):
        c.push(0.0, np.zeros((3, *SHAPE)))
    with pytest.raises(ValueError):
        c.push(0.0, _stack(0), override=np.zeros((2, 2)))
    c.push(1.0, _stack(0))
    with pytest.raises(ValueError):
        c.push(1.0, _stack(0))  # not increasing
    with pytest.raises(ValueError):
        LayerCache(capacity=2, layer_ids=("a", "a"), shape=SHAPE)


def test_reset_clears_history_for_new_layer_set():
    c = LayerCache(capacity=4, layer_ids=IDS, shape=SHAPE)
    c.push(0.0, _stack(1))
    c.reset(("crowd", "flow", "news"), SHAPE)
    assert len(c) == 0
    c.push(1.0, np.ones((3, *SHAPE)))
    assert c.window().layers.shape == (1, 3, *SHAPE)
