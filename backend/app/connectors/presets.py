"""Named `json_api` presets: data only, never code (rule 10). A preset fixes one API's
request shape and response path; `config` only fills its placeholders (`json_api.py`).

`area_grid` (optional, data): the API accepts comma-joined lists for its `lat_key`/`lon_key`
placeholders and answers with a JSON list holding one response per point, in the same order.
The connector engine then samples an n x n grid of points across the app's bbox in ONE request
(n is the largest with n^2 <= `max_points` and spacing >= `min_spacing_m`; a small area gets one
point, its centre) and emits one event per point, positioned at that point. The config's own
`lat_key`/`lon_key` values are used only when no bbox is known.
"""

from __future__ import annotations

from typing import Any

PRESETS: dict[str, dict[str, Any]] = {
    "open_meteo": {
        "url": "https://api.open-meteo.com/v1/forecast",
        "query": {
            "latitude": "{latitude}",
            "longitude": "{longitude}",
            "current": "wind_speed_10m,wind_direction_10m",
            "wind_speed_unit": "ms",  # the field operator reads vector magnitudes in m/s
        },
        "response_path": "current",
        "fields": ["wind_speed_10m", "wind_direction_10m"],
        # the provider's resolution is a few km: points closer than that would repeat one reading
        "area_grid": {"max_points": 16, "min_spacing_m": 2000, "multi_param_join": ","},
    }
}
