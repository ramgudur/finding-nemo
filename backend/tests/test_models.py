from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.core.models import (
    AppSpec,
    BBox,
    CellCollection,
    ConnectorConfig,
    Event,
    Hotspot,
    Instruction,
    MapResponse,
    Plausibility,
    PoiLabel,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _spec(**over):
    spec = {
        "app_id": "test-app",
        "intent": "find where people gather",
        "grid": {"cell_m": 10, "bbox": {"min_lat": 0.0, "min_lon": 0.0, "max_lat": 0.01, "max_lon": 0.01}},
        "inputs": [
            {
                "id": "pings",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "a ping means someone is here",
                "fields": [{"name": "kind", "type": "string", "required": False}],
                "operators": [{"op": "density", "weight": 1.0, "half_life_s": 60}],
            }
        ],
        "verification": [{"method": "predictive_check", "priority": 1}],
        "outputs": {"layers": [{"id": "score", "visibility": "public"}, {"id": "pings.density"}]},
    }
    spec.update(over)
    return spec


def test_minimal_spec_fills_defaults():
    s = AppSpec.model_validate(_spec())
    assert s.status == "draft"
    inp = s.inputs[0]
    assert inp.visibility == "authority"
    assert inp.operators[0].layer_id == "pings.density"
    assert inp.operators[0].model_extra == {"half_life_s": 60}
    assert s.outputs.layers[1].visibility == "authority"
    assert s.routing.rephrase is False
    assert s.routing.fallback_text == "No route available."  # neutral default (rule 3)


def test_operator_params_feed_the_registry():
    from app.engine.operators import registry

    s = AppSpec.model_validate(_spec())
    op = s.inputs[0].operators[0]
    assert op.params() == {"half_life_s": 60}
    assert registry.parse_params(op.op, op.params()).half_life_s == 60
    op.noise_floor = 2.0
    assert registry.parse_params(op.op, op.params()).noise_floor == 2.0


def test_spec_round_trips_through_json():
    s = AppSpec.model_validate(_spec())
    assert AppSpec.model_validate_json(s.model_dump_json()) == s


def test_unknown_output_layer_rejected():
    with pytest.raises(ValidationError, match="unknown layer"):
        AppSpec.model_validate(_spec(outputs={"layers": [{"id": "nope"}]}))


def test_push_input_with_connector_rejected():
    bad = _spec()
    bad["inputs"][0]["connector"] = {"type": "rss", "url": "https://example.com/feed"}
    with pytest.raises(ValidationError, match="must not have a connector"):
        AppSpec.model_validate(bad)


def test_html_connector_type_rejected():
    bad = _spec()
    bad["inputs"][0].update(kind="pull", connector={"type": "html", "url": "https://example.com"})
    with pytest.raises(ValidationError):
        AppSpec.model_validate(bad)


def test_label_cannot_be_sensitive_and_destination():
    with pytest.raises(ValidationError, match="both sensitive and a destination"):
        PoiLabel(label="x", sensitive=True, destination=True)


def test_bbox_must_be_ordered():
    bad = _spec(grid={"cell_m": 10, "bbox": {"min_lat": 1.0, "min_lon": 0.0, "max_lat": 0.5, "max_lon": 1.0}})
    with pytest.raises(ValidationError, match="bbox"):
        AppSpec.model_validate(bad)


def test_bbox_as_geojson_order():
    b = BBox(min_lat=32.0, min_lon=-97.0, max_lat=33.0, max_lon=-96.0)
    assert b.as_geojson() == (-97.0, 32.0, -96.0, 33.0)


def test_event_keeps_input_fields_as_extras():
    e = Event.model_validate({"lat": 1.0, "lon": 2.0, "ts": "2026-01-01T00:00:00Z", "kind": "press"})
    assert e.model_extra == {"kind": "press"}
    assert e.user_id is None


def test_verification_priorities_must_be_unique():
    bad = _spec(
        verification=[
            {"method": "predictive_check", "priority": 1},
            {"method": "cross_input_agreement", "priority": 1},
        ]
    )
    with pytest.raises(ValidationError, match="priorities must be unique"):
        AppSpec.model_validate(bad)


def test_fallback_text_capped_at_360():
    with pytest.raises(ValidationError):
        AppSpec.model_validate(_spec(routing={"fallback_text": "x" * 361}))


def test_naive_datetimes_rejected_and_aware_ones_normalized_to_utc():
    with pytest.raises(ValidationError):
        Event.model_validate({"lat": 1.0, "lon": 2.0, "ts": "2026-01-01T00:00:00"})
    e = Event.model_validate({"lat": 1.0, "lon": 2.0, "ts": "2026-01-01T02:00:00+02:00"})
    assert e.ts == NOW and e.ts.utcoffset().total_seconds() == 0


def test_instruction_text_capped_at_360():
    with pytest.raises(ValidationError):
        Instruction(text="x" * 361, computed_at=NOW, guard={"passed": True})


def test_map_response_accepts_grid_to_geojson_output():
    import numpy as np

    from app.engine.grid import Grid

    grid = Grid(bbox=(0.0, 0.0, 0.01, 0.01), cell_m=50.0)
    values = np.zeros(grid.shape)
    values[0, 0] = 0.5
    fc = grid.to_geojson(values, min_value=0.0099, property_name="value")
    coll = CellCollection.model_validate(fc)
    assert len(coll.features) == 1
    assert coll.features[0].properties.value == 0.5

    m = MapResponse(app_id="a", t=NOW, cell_m=grid.cell_m, bbox=grid.bbox, layers={"score": fc})
    assert m.layers["score"].features[0].properties.row == 0


def test_outputs_hotspot_threshold_defaults_and_bounds():
    s = AppSpec.model_validate(_spec())
    assert s.outputs.hotspot_threshold == 0.5
    with pytest.raises(ValidationError):
        AppSpec.model_validate(_spec(outputs={"hotspot_threshold": 0.0}))
    with pytest.raises(ValidationError):
        AppSpec.model_validate(_spec(outputs={"hotspot_threshold": 1.5}))


def test_input_plausibility_is_optional_and_partial():
    s = AppSpec.model_validate(_spec())
    assert s.inputs[0].plausibility is None

    bad = _spec()
    bad["inputs"][0]["plausibility"] = {"max_speed_mps": 30.0, "flag_straight_line": False}
    s = AppSpec.model_validate(bad)
    p = s.inputs[0].plausibility
    assert p.max_speed_mps == 30.0
    assert p.flag_straight_line is False
    assert p.jitter_m is None and p.flag_constant_speed is None

    with pytest.raises(ValidationError):
        Plausibility(max_speed_mps=-1.0)


def test_connector_config_accepts_a_preset_and_rejects_open_meteo_as_a_type():
    c = ConnectorConfig(type="json_api", preset="open_meteo")
    assert c.preset == "open_meteo"
    with pytest.raises(ValidationError):
        ConnectorConfig(type="open_meteo")


def test_hotspot_rejects_both_sensitive_defaults_and_bad_radius():
    Hotspot(id="h1", lat=1.0, lon=2.0, radius_m=5.0, score=0.9, mean=0.5, area_m2=78.5, n_cells=1,
             persisted_ticks=0, visibility="authority")  # fmt: skip
    with pytest.raises(ValidationError):
        Hotspot(id="h1", lat=1.0, lon=2.0, radius_m=-1.0, score=0.9, mean=0.5, area_m2=78.5, n_cells=1,
                 persisted_ticks=0, visibility="authority")  # fmt: skip
