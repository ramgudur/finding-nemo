import pytest

from app.connectors import json_api
from app.connectors.presets import PRESETS


def test_open_meteo_preset_is_registered():
    assert "open_meteo" in PRESETS
    spec = PRESETS["open_meteo"]
    assert spec["response_path"] == "current"
    assert set(spec["fields"]) == {"wind_speed_10m", "wind_direction_10m"}
    # Open-Meteo defaults to km/h; vector magnitudes are m/s everywhere in the engine
    assert spec["query"]["wind_speed_unit"] == "ms"


def test_resolve_preset_unknown_raises():
    with pytest.raises(KeyError, match="unknown connector preset"):
        json_api.resolve_preset("not-a-real-preset", {})


def test_resolve_preset_none_returns_empty():
    assert json_api.resolve_preset(None, {}) == {}


def test_render_query_fills_placeholders_from_config():
    query = {"latitude": "{latitude}", "longitude": "{longitude}", "current": "wind_speed_10m"}
    out = json_api.render_query(query, {"latitude": 32.8, "longitude": -96.7})
    assert out == {"latitude": 32.8, "longitude": -96.7, "current": "wind_speed_10m"}


def test_render_query_missing_config_value_raises():
    with pytest.raises(KeyError, match="latitude"):
        json_api.render_query({"latitude": "{latitude}"}, {})


def test_extract_fields_reads_the_response_path_and_adds_lat_lon():
    spec = PRESETS["open_meteo"]
    data = {"current": {"time": "2026-11-08T05:00", "wind_speed_10m": 5.0, "wind_direction_10m": 180}}
    event = json_api.extract_fields(data, spec, {"latitude": 32.8, "longitude": -96.7})
    assert event == {"wind_speed_10m": 5.0, "wind_direction_10m": 180, "lat": 32.8, "lon": -96.7}
    assert "ts" not in event  # the caller stamps the fetch time, not the response's own


def test_extract_fields_skips_a_missing_field_rather_than_raising():
    spec = {"response_path": "current", "fields": ["wind_speed_10m", "missing_field"]}
    data = {"current": {"wind_speed_10m": 5.0}}
    event = json_api.extract_fields(data, spec, {})
    assert event == {"wind_speed_10m": 5.0}


def test_extract_fields_without_lat_lon_config_omits_them():
    spec = {"response_path": "", "fields": ["x"]}
    event = json_api.extract_fields({"x": 1}, spec, {})
    assert "lat" not in event and "lon" not in event


# -- #259: custom (non-preset) response_path / fields, data only ----------------------------


def test_custom_fields_list_reads_names_from_the_response_path():
    data = {"result": {"a": 1.0, "b": 2.0, "ignored": 3}}
    event = json_api.extract_fields(data, {}, {"response_path": "result", "fields": ["a", "b", "missing"]})
    assert event == {"a": 1.0, "b": 2.0}


def test_custom_fields_map_uses_dotted_paths_with_list_indices():
    data = {"rows": [{"v": {"x": 4}}, {"v": {"x": 5}}]}
    event = json_api.extract_fields(data, {}, {"fields": {"first": "rows.0.v.x", "second": "rows.1.v.x"}})
    assert event == {"first": 4, "second": 5}


def test_a_preset_ignores_custom_read_keys_in_config():
    data = {"current": {"wind_speed_10m": 1.0, "wind_direction_10m": 2.0}, "other": {"z": 9}}
    config = {"response_path": "other", "fields": ["z"]}
    assert json_api.extract_fields(data, PRESETS["open_meteo"], config) == {
        "wind_speed_10m": 1.0,
        "wind_direction_10m": 2.0,
    }


@pytest.mark.parametrize(
    "config",
    [
        {"fields": "a,b"},
        {"fields": [1, 2]},
        {"fields": ["not a name"]},
        {"fields": ["lat"]},
        {"fields": {"ts": "x"}},
        {"fields": {"a": 5}},
        {"fields": {"a": "x..y"}},
        {"response_path": 3},
        {"response_path": "a" * 500},
    ],
)
def test_custom_config_is_validated_as_data(config):
    with pytest.raises(ValueError):
        json_api.check_custom_config(config)


def test_field_names_for_presets_and_custom_sources():
    assert json_api.field_names("open_meteo", {}) == ["wind_speed_10m", "wind_direction_10m"]
    assert json_api.field_names(None, {"fields": {"level": "a.b"}}) == ["level"]
    assert json_api.field_names(None, {}) == []
