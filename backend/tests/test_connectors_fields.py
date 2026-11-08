"""#259: a connector's implied fields are declared on its input (the dev's own always win)."""

from app.connectors import fields
from app.core.models import CompileInput, ConnectorConfig, InputField, InputSpec


def _pull(connector: dict, declared: list | None = None) -> InputSpec:
    return InputSpec.model_validate(
        {
            "id": "src",
            "kind": "pull",
            "shape": "document",
            "tier": "stiff",
            "meaning": "documents",
            "fields": declared or [],
            "connector": connector,
        }
    )


def test_rss_implies_optional_doc_id_and_confidence():
    inp = fields.declare_input(_pull({"type": "rss", "url": "https://f.example.org/x"}))
    got = {f.name: (f.type, f.required) for f in inp.fields}
    assert got == {"doc_id": ("string", False), "confidence": ("number", False)}


def test_a_preset_implies_its_fields_as_optional_numbers():
    conn = {"type": "json_api", "preset": "open_meteo", "config": {"latitude": 0, "longitude": 0}}
    names = [(f.name, f.type, f.required) for f in fields.implied_fields(ConnectorConfig.model_validate(conn))]
    assert names == [("wind_speed_10m", "number", False), ("wind_direction_10m", "number", False)]


def test_custom_json_fields_are_implied_and_a_bad_config_implies_nothing():
    ok = ConnectorConfig(type="json_api", url="https://a.example.org", config={"fields": {"level": "x.y"}})
    assert [f.name for f in fields.implied_fields(ok)] == ["level"]
    bad = ConnectorConfig(type="json_api", url="https://a.example.org", config={"fields": {"lat": "x"}})
    assert fields.implied_fields(bad) == []
    assert fields.implied_fields(ConnectorConfig(type="json_api", preset="no-such-preset")) == []


def test_the_devs_declaration_wins_and_is_never_changed():
    declared = [{"name": "doc_id", "type": "string", "required": True, "description": "mine"}]
    inp = fields.declare_input(_pull({"type": "rss", "url": "https://f.example.org/x"}, declared))
    assert inp.fields[0] == InputField(name="doc_id", type="string", required=True, description="mine")
    assert [f.name for f in inp.fields] == ["doc_id", "confidence"]


def test_declaring_is_idempotent_and_leaves_push_inputs_alone():
    inp = fields.declare_input(_pull({"type": "rss", "url": "https://f.example.org/x"}))
    assert fields.declare_input(inp) is inp
    push = CompileInput(id="p", kind="push", shape="point", meaning="m")
    assert fields.declare_compile_input(push) is push
