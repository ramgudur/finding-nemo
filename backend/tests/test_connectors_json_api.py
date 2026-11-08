import pytest

from app.connectors import json_api
from app.connectors.presets import PRESETS


def test_open_meteo_preset_is_registered():
    assert "open_meteo" in PRESETS
    spec = PRESETS["open_meteo"]
    assert spec["response_path"] == "current"
    assert set(spec["fields"]) == {"wind_speed_10m", "wind_direction_10m"}


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
