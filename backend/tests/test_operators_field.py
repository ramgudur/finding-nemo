import numpy as np
import pytest
from pydantic import ValidationError
from synthetic import FixedClock, LocalGrid, events_from_xy

from app.engine.operators.field import FieldParams, advect, field, vector_uv

GRID = LocalGrid()
FIELDS = ("value", "speed", "dir")


def _run(rows, now=100.0, **kw):
    ev = events_from_xy(GRID, rows, FIELDS)
    return field(ev, FieldParams(**kw), None, FixedClock(now), GRID)


def test_single_station_is_uniform_and_latest_reading_wins():
    r = _run([("st1", 10.0, 100.0, 100.0, 3.0, None, None), ("st1", 90.0, 100.0, 100.0, 5.0, None, None)])
    np.testing.assert_allclose(r.layer, 5.0)
    assert r.extras["n_points"] == 1


def test_idw_leans_to_nearer_station_and_clips_negative():
    rows = [("a", 90.0, 12.5, 12.5, 10.0, None, None), ("b", 90.0, 487.5, 487.5, 0.0, None, None)]
    r = _run(rows)
    assert r.layer[0, 0] > 9.0 and r.layer[19, 19] < 1.0
    assert 4.0 < r.layer[10, 10] < 6.0
    neg = _run([("a", 90.0, 100.0, 100.0, -4.0, None, None)])
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
