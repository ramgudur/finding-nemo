import json

import numpy as np
import pytest

from app.engine.grid import Grid, from_geojson_point, haversine_m, point_feature, to_geojson_point

# Synthetic ~1 km box; no app-specific coordinates.
BBOX = (10.0, 45.0, 10.0127, 45.009)


@pytest.fixture
def grid() -> Grid:
    return Grid(bbox=BBOX, cell_m=25.0)


def test_shape_covers_bbox(grid: Grid):
    h, w = grid.shape
    assert h * grid.cell_m >= grid.height_m > (h - 1) * grid.cell_m
    assert w * grid.cell_m >= grid.width_m > (w - 1) * grid.cell_m
    assert grid.zeros().shape == (h, w)
    assert 950 < grid.width_m < 1050 and 950 < grid.height_m < 1050


def test_xy_round_trip_and_origin(grid: Grid):
    rng = np.random.default_rng(0)
    lat = rng.uniform(BBOX[1], BBOX[3], 500)
    lon = rng.uniform(BBOX[0], BBOX[2], 500)
    x, y = grid.to_xy(lat, lon)
    lat2, lon2 = grid.to_latlon(x, y)
    np.testing.assert_allclose(lat2, lat, atol=1e-12)
    np.testing.assert_allclose(lon2, lon, atol=1e-12)
    x0, y0 = grid.to_xy(BBOX[1], BBOX[0])
    assert float(x0) == 0.0 and float(y0) == 0.0


def test_local_meters_match_haversine(grid: Grid):
    rng = np.random.default_rng(1)
    lat = rng.uniform(BBOX[1], BBOX[3], (2, 200))
    lon = rng.uniform(BBOX[0], BBOX[2], (2, 200))
    x, y = grid.to_xy(lat, lon)
    planar = np.hypot(x[0] - x[1], y[0] - y[1])
    great = haversine_m(lat[0], lon[0], lat[1], lon[1])
    np.testing.assert_allclose(planar, great, rtol=2e-3, atol=0.05)


def test_cell_indexing_row0_is_south(grid: Grid):
    # just inside the SW and NE corners
    r, c, inside = grid.cell_of([BBOX[1] + 1e-6, BBOX[3] - 1e-6], [BBOX[0] + 1e-6, BBOX[2] - 1e-6])
    assert inside.all()
    assert (r[0], c[0]) == (0, 0)
    assert (r[1], c[1]) == (grid.shape[0] - 1, grid.shape[1] - 1)
    # outside and NaN are flagged, indices stay in range
    r, c, inside = grid.cell_of([BBOX[1] - 0.01, np.nan, BBOX[3] + 0.01], [BBOX[0], BBOX[0], BBOX[2]])
    assert not inside.any()
    assert (r >= 0).all() and (r < grid.shape[0]).all() and (c >= 0).all() and (c < grid.shape[1]).all()


def test_cell_center_maps_back_to_its_cell(grid: Grid):
    h, w = grid.shape
    rows, cols = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
    lat, lon = grid.cell_center(rows, cols)
    r, c, inside = grid.cell_of(lat, lon)
    assert inside.all()
    assert (r == rows).all() and (c == cols).all()
    clat, clon = grid.centers_latlon()
    np.testing.assert_allclose(clat, lat)
    np.testing.assert_allclose(clon, lon)


def test_cells_within_radius(grid: Grid):
    lat, lon = grid.to_latlon(500.0, 500.0)
    mask = grid.cells_within(float(lat), float(lon), 100.0)
    area = mask.sum() * grid.cell_m**2
    assert area == pytest.approx(np.pi * 100.0**2, rel=0.15)
    tiny = grid.cells_within(float(lat), float(lon), 1.0)
    assert tiny.sum() == 1


def test_geojson_is_lon_lat_and_skips_zeros(grid: Grid):
    vals = grid.zeros()
    vals[0, 0] = 0.5
    vals[2, 3] = np.nan
    fc = grid.to_geojson(vals)
    json.dumps(fc)  # serializable
    assert len(fc["features"]) == 1
    f = fc["features"][0]
    assert f["properties"] == {"score": 0.5, "row": 0, "col": 0}
    ring = f["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1] and len(ring) == 5
    assert ring[0] == [BBOX[0], BBOX[1]]  # [lon, lat] of the SW corner
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    assert min(lons) >= BBOX[0] - 1e-12 and max(lats) <= BBOX[3] + 1e-12
    with pytest.raises(ValueError):
        grid.to_geojson(np.zeros((1, 1)))


def test_point_helpers_swap_order():
    assert to_geojson_point(45.1, 10.2) == [10.2, 45.1]
    assert from_geojson_point([10.2, 45.1]) == (45.1, 10.2)
    assert point_feature(45.1, 10.2, {"a": 1})["geometry"]["coordinates"] == [10.2, 45.1]


def test_from_spec_and_validation():
    g = Grid.from_spec({"cell_m": 50, "bbox": list(BBOX)})
    assert g.cell_m == 50.0 and g.bbox == BBOX
    named = {"min_lat": BBOX[1], "min_lon": BBOX[0], "max_lat": BBOX[3], "max_lon": BBOX[2]}
    assert Grid.from_spec({"cell_m": 50, "bbox": named}).bbox == BBOX

    class _BBox:  # stands in for core.models.BBox
        def as_geojson(self):
            return BBOX

    class _GridSpec:
        bbox = _BBox()
        cell_m = 50

    assert Grid.from_spec(_GridSpec()).bbox == BBOX
    with pytest.raises(ValueError):
        Grid(bbox=(10.0, 45.0, 9.0, 46.0), cell_m=10)
    with pytest.raises(ValueError):
        Grid(bbox=BBOX, cell_m=0)
