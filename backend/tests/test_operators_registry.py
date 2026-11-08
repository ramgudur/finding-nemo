import pytest
from pydantic import ValidationError
from synthetic import FixedClock, LocalGrid, events_from_xy

from app.engine.operators import registry
from app.engine.operators.density import DensityParams


def test_catalog_lists_operators_with_descriptions_and_noise_floor():
    cat = {o["name"]: o for o in registry.list_operators()}
    assert {"flow_source", "density"} <= set(cat)
    for o in cat.values():
        assert o["description"] and "\n" not in o["description"]
        assert o["shapes"]
        assert o["params"]["properties"]["noise_floor"]["default"] > 0


def test_parse_params_drops_spec_keys_and_rejects_unknown():
    p = registry.parse_params("density", {"op": "density", "weight": 0.7, "half_life_s": 30})
    assert isinstance(p, DensityParams) and p.half_life_s == 30
    with pytest.raises(ValidationError):
        registry.parse_params("density", {"half_life": 30})
    with pytest.raises(ValidationError):
        registry.parse_params("density", {"noise_floor": 0})


def test_unknown_operator():
    with pytest.raises(KeyError, match="unknown operator"):
        registry.get("telepathy")


def test_run_with_raw_spec_entry():
    grid = LocalGrid()
    ev = events_from_xy(grid, [("u1", 10.0, 262.5, 262.5)])
    r = registry.run("density", ev, {"op": "density", "weight": 1.0}, None, FixedClock(10.0), grid)
    assert r.layer.shape == grid.shape and r.layer.max() == pytest.approx(1.0)
