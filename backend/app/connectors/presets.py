"""Named `json_api` presets: data only, never code (rule 10). A preset fixes one API's
request shape and response path; `config` only fills its placeholders (`json_api.py`).
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
        },
        "response_path": "current",
        "fields": ["wind_speed_10m", "wind_direction_10m"],
    }
}
