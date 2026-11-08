from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.core.models import AppSpec, Event, Instruction, PoiLabel

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


def test_event_keeps_input_fields_as_extras():
    e = Event.model_validate({"lat": 1.0, "lon": 2.0, "ts": "2026-01-01T00:00:00Z", "kind": "press"})
    assert e.model_extra == {"kind": "press"}
    assert e.user_id is None


def test_instruction_text_capped_at_360():
    with pytest.raises(ValidationError):
        Instruction(text="x" * 361, computed_at=NOW, guard={"passed": True})
