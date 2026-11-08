import numpy as np
import pytest

from app.engine.combine import apply_overrides, combine, normalize, normalize_stack, weighted_sum


def _gauss_blob(shape=(20, 20), at=(10, 10), peak=1.0, sigma=1.5):
    i, j = np.indices(shape)
    return peak * np.exp(-0.5 * ((i - at[0]) ** 2 + (j - at[1]) ** 2) / sigma**2)


def test_sparse_one_event_layer_stays_under_noise_floor_scaled_value():
    # one event, peak ~1 (operator convention), noise floor 3
    layer = _gauss_blob(peak=1.0)
    norm = normalize(layer, noise_floor=3.0)
    assert norm.max() == pytest.approx(layer.max() / 3.0)
    assert norm.max() < 1.0


def test_all_zero_layer_returns_zeros():
    norm = normalize(np.zeros((20, 20)), noise_floor=3.0)
    assert not norm.any() and np.isfinite(norm).all()
    res = combine([np.zeros((5, 5)), np.zeros((5, 5))], [1.0, 1.0], [0.5, 0.5])
    assert not res.score.any() and np.isfinite(res.score).all()


def test_dense_layer_scales_by_p99():
    rng = np.random.default_rng(0)
    layer = rng.uniform(10, 20, size=(50, 50))
    norm = normalize(layer, noise_floor=1.0)
    assert norm.max() == pytest.approx(layer.max() / np.percentile(layer, 99))
    assert np.mean(norm > 1.0) <= 0.011


def test_negative_and_nonfinite_cells_count_as_zero():
    layer = np.array([[-5.0, np.nan], [np.inf, 2.0]])
    norm = normalize(layer, noise_floor=4.0)
    np.testing.assert_allclose(norm, [[0, 0], [0, 0.5]])


def test_noise_floor_must_be_positive():
    with pytest.raises(ValueError):
        normalize(np.ones((2, 2)), 0.0)


def test_weighted_sum_is_clamped():
    stack = np.stack([np.full((3, 3), 0.8), np.full((3, 3), 0.9)])
    np.testing.assert_allclose(weighted_sum(stack, [1.0, 1.0]), 1.0)
    np.testing.assert_allclose(weighted_sum(stack, [-2.0, 0.0]), 0.0)
    np.testing.assert_allclose(weighted_sum(stack, [0.5, 0.0]), 0.4)
    with pytest.raises(ValueError):
        weighted_sum(stack, [1.0])


def test_overrides_win_after_weighted_sum():
    a = _gauss_blob(peak=10.0)
    ov = np.zeros((20, 20))
    ov[0:3, 0:3] = 1.0
    res = combine([a], [1.0], [0.0], override=ov)  # weight 0: only the override shows
    assert res.score[0:3, 0:3].min() == 1.0
    assert res.score[5:, 5:].max() == 0.0
    # override never lowers a score
    np.testing.assert_array_equal(apply_overrides(np.full((2, 2), 0.7), np.zeros((2, 2))), 0.7)


def test_normalized_layers_do_not_depend_on_weights():
    layers = [_gauss_blob(peak=4.0), _gauss_blob(at=(3, 3), peak=7.0)]
    r1 = combine(layers, [2.0, 2.0], [0.2, 0.8])
    r2 = combine(layers, [2.0, 2.0], [0.9, 0.1])
    np.testing.assert_array_equal(r1.normalized, r2.normalized)
    np.testing.assert_allclose(r1.normalized, normalize_stack(layers, [2.0, 2.0]))


def test_shape_checks():
    with pytest.raises(ValueError):
        combine([np.ones((3, 3))], [1.0, 1.0], [1.0])
    with pytest.raises(ValueError):
        combine([np.ones((3, 3))], [1.0], [1.0], override=np.ones((2, 2)))
