import numpy as np
import pytest
from pydantic import ValidationError
from synthetic import FixedClock, LocalGrid, events_from_xy

from app.engine.operators.field import FieldParams, advect, default_diffusion, field, vector_uv

GRID = LocalGrid()
FIELDS = ("value", "speed", "dir")


def _run(rows, now=100.0, **kw):
    ev = events_from_xy(GRID, rows, FIELDS)
    return field(ev, FieldParams(**kw), None, FixedClock(now), GRID)


def test_single_station_is_uniform_and_latest_reading_wins():
    r = _run(
        [("st1", 10.0, 100.0, 100.0, 3.0, None, None), ("st1", 90.0, 100.0, 100.0, 5.0, None, None)],
        baseline="zero",
    )
    np.testing.assert_allclose(r.layer, 5.0)
    assert r.extras["n_points"] == 1


def test_idw_leans_to_nearer_station_and_clips_negative():
    rows = [("a", 90.0, 12.5, 12.5, 10.0, None, None), ("b", 90.0, 487.5, 487.5, 0.0, None, None)]
    r = _run(rows, baseline="zero")
    assert r.layer[0, 0] > 9.0 and r.layer[19, 19] < 1.0
    assert 4.0 < r.layer[10, 10] < 6.0
    neg = _run([("a", 90.0, 100.0, 100.0, -4.0, None, None)], baseline="zero")
    assert not neg.layer.any()


def test_stale_readings_are_ignored():
    assert not _run([("a", 100.0 - 4000.0, 100.0, 100.0, 5.0, None, None)]).layer.any()


def test_vector_uv_conventions():
    u, v = vector_uv(np.array([2.0, 3.0]), np.array([0.0, 270.0]))  # "from" north, "from" west
    np.testing.assert_allclose([u[0], v[0]], [0.0, -2.0], atol=1e-12)
    np.testing.assert_allclose([u[1], v[1]], [3.0, 0.0], atol=1e-12)
    u, v = vector_uv(np.array([2.0, 3.0]), np.array([0.0, 90.0]), "to")  # heading north, heading east
    np.testing.assert_allclose([u[0], v[0]], [0.0, 2.0], atol=1e-12)
    np.testing.assert_allclose([u[1], v[1]], [3.0, 0.0], atol=1e-12)


def test_advect_moves_blob_along_the_field_and_conserves_mass():
    layer = np.zeros(GRID.shape)
    layer[10, 5] = 1.0
    shifted = advect(layer, 0.5, 0.0, GRID.cell_m, horizon_s=100.0)  # 50 m east = 2 cells
    assert np.unravel_index(np.argmax(shifted), shifted.shape) == (10, 7)
    assert shifted.sum() == pytest.approx(1.0)
    half = advect(layer, 0.0, 0.125, GRID.cell_m, horizon_s=100.0)  # 12.5 m north = half a cell
    assert half[10, 5] == pytest.approx(0.5) and half[11, 5] == pytest.approx(0.5)
    diffused = advect(layer, 0.5, 0.0, GRID.cell_m, horizon_s=100.0, diffusion_m2_s=5.0)
    assert diffused.sum() == pytest.approx(1.0, rel=1e-3)
    assert diffused.max() < shifted.max()
    assert np.unravel_index(np.argmax(diffused), diffused.shape) == (10, 7)


def test_vector_extras_and_shift_request():
    rows = [("p1", 90.0, 250.0, 250.0, 1.0, 4.0, 270.0)]
    r = _run(rows, vector_speed_field="speed", vector_dir_field="dir", shift_layer="plume.density")
    np.testing.assert_allclose(r.extras["vector_u"], 4.0)
    np.testing.assert_allclose(r.extras["vector_v"], 0.0, atol=1e-12)
    assert r.extras["shift"]["layer"] == "plume.density"
    assert r.extras["shift"]["args"]["horizon_s"] == 600.0


def test_params_validation():
    with pytest.raises(ValidationError):
        FieldParams(vector_speed_field="s")
    with pytest.raises(ValidationError):
        FieldParams(shift_layer="x")


def test_to_convention_reverses_the_shift():
    rows = [("p1", 90.0, 250.0, 250.0, 1.0, 0.5, 90.0)]
    frm = _run(rows, vector_speed_field="speed", vector_dir_field="dir")
    to = _run(rows, vector_speed_field="speed", vector_dir_field="dir", vector_dir_convention="to")
    layer = np.zeros(GRID.shape)
    layer[10, 10] = 1.0
    east = advect(layer, to.extras["vector_u"], to.extras["vector_v"], GRID.cell_m, horizon_s=100.0)
    west = advect(layer, frm.extras["vector_u"], frm.extras["vector_v"], GRID.cell_m, horizon_s=100.0)
    assert np.unravel_index(np.argmax(east), east.shape) == (10, 12)  # heading east: 50 m = 2 cells
    assert np.unravel_index(np.argmax(west), west.shape) == (10, 8)  # coming from the east
    with pytest.raises(ValidationError):
        FieldParams(vector_dir_convention="sideways")


def _centroid(layer):
    rows, cols = np.indices(layer.shape)
    m = layer.sum()
    return float((cols * layer).sum() / m), float((rows * layer).sum() / m)


def test_rotation_follows_the_curved_field():
    """Solid-body rotation about the grid center: a quarter turn lands where the analytic answer is.
    A single straight back-trace (the old scheme) would miss by several cells."""
    h, w = 40, 40
    c = (w - 1) / 2
    rows, cols = np.indices((h, w), dtype=float)
    cell_m, omega = 10.0, 0.01  # rad/s
    u = -omega * (rows - c) * cell_m  # m/s
    v = omega * (cols - c) * cell_m
    layer = np.zeros((h, w))
    layer[int(c), int(c) + 12] = 1.0  # 12 cells east of center
    out = advect(layer, u, v, cell_m, horizon_s=(np.pi / 2) / omega)
    x, y = _centroid(out)
    rx, ry = (int(c) + 12) - c, int(c) - c  # start relative to the center
    ex, ey = c - ry, c + rx  # a quarter turn counter-clockwise
    assert abs(x - ex) < 0.6 and abs(y - ey) < 0.6
    assert out.sum() == pytest.approx(1.0)


def test_mass_is_conserved_in_converging_and_diverging_flow():
    h, w = 30, 30
    c = (w - 1) / 2
    rows, cols = np.indices((h, w), dtype=float)
    layer = np.zeros((h, w))
    layer[10:20, 10:20] = 1.0
    for k in (-0.004, 0.004):  # converging, diverging (per second)
        out = advect(layer, k * (cols - c) * 10.0, k * (rows - c) * 10.0, 10.0, horizon_s=100.0)
        assert out.sum() == pytest.approx(layer.sum())
    conv = advect(layer, -0.004 * (cols - c) * 10.0, -0.004 * (rows - c) * 10.0, 10.0, horizon_s=100.0)
    assert conv.max() > 1.0  # mass piles up where flow converges


def test_long_horizon_is_stable_and_loses_only_outflow():
    layer = np.zeros(GRID.shape)
    layer[10, 10] = 1.0
    gone = advect(layer, 5.0, 0.0, GRID.cell_m, horizon_s=3600.0)  # 18 km east: leaves the grid
    assert np.isfinite(gone).all() and gone.sum() == 0.0
    back = advect(layer, 1e-4, 0.0, GRID.cell_m, horizon_s=3600.0)  # 0.36 m: stays put
    assert back.sum() == pytest.approx(1.0) and np.argmax(back) == np.argmax(layer)


def test_unset_diffusion_defaults_to_half_a_cell_of_spread():
    rows = [("p1", 90.0, 250.0, 250.0, 1.0, 0.5, 90.0)]
    r = _run(rows, vector_speed_field="speed", vector_dir_field="dir", shift_layer="x.y", shift_horizon_s=200.0)
    d = r.extras["shift"]["args"]["diffusion_m2_s"]
    assert d == pytest.approx(default_diffusion(GRID.cell_m, 200.0))
    assert np.sqrt(2 * d * 200.0) == pytest.approx(GRID.cell_m / 2)  # sigma scales with cell_m
    sharp = _run(rows, vector_speed_field="speed", vector_dir_field="dir", shift_layer="x.y", diffusion_m2_s=0.0)
    assert sharp.extras["shift"]["args"]["diffusion_m2_s"] == 0.0


# -- #194: contrast baseline (default) ------------------------------------------------------


def test_single_point_field_contributes_nothing_by_default():
    r = _run([("st1", 90.0, 250.0, 250.0, 25.0, None, None)])
    assert not r.layer.any()  # uniform: no spatial information
    assert r.extras["baseline"] == pytest.approx(25.0) and r.extras["n_points"] == 1


def test_smooth_multi_point_field_keeps_only_its_contrast():
    rows = [("a", 90.0, 12.5, 12.5, 20.0, None, None), ("b", 90.0, 487.5, 487.5, 23.0, None, None)]
    rel = _run(rows)
    absolute = _run(rows, baseline="zero")
    np.testing.assert_allclose(rel.layer, absolute.layer - absolute.layer.min(), atol=1e-9)
    assert rel.layer[0, 0] == 0.0  # the lower (20) point's corner is the baseline
    assert rel.layer[-1, -1] == pytest.approx(rel.layer.max())  # the higher (23) point's corner
    assert rel.layer.max() < 3.0  # at most the stations' spread


def test_single_point_field_next_to_a_point_layer_does_not_flood_the_grid():
    from app.engine.combine import combine
    from app.engine.operators.density import DensityParams, density

    field_layer = _run([("st1", 90.0, 250.0, 250.0, 25.0, None, None)]).layer
    absolute = _run([("st1", 90.0, 250.0, 250.0, 25.0, None, None)], baseline="zero").layer
    pts = [(f"u{k}", 100.0, 100.0 + k, 100.0) for k in range(8)]
    dens = density(events_from_xy(GRID, pts), DensityParams(), None, FixedClock(100.0), GRID).layer
    fixed = combine([dens, field_layer], [3.0, 1.0], [0.6, 0.8]).score
    flooded = combine([dens, absolute], [3.0, 1.0], [0.6, 0.8]).score
    assert (flooded >= 0.5).mean() == 1.0  # the old behaviour: one grid-wide hotspot
    assert 0.0 < (fixed >= 0.5).mean() < 0.05  # now only around the points
    i, j = np.unravel_index(np.argmax(fixed), fixed.shape)
    assert abs(i - 4) <= 1 and abs(j - 4) <= 1  # the point cluster near (100, 100)
