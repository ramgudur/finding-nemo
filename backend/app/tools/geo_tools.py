"""External geo lookups for agent tools: OpenStreetMap features near a point (Overpass).

- Cached by rounded (lat, lon, radius): repeated questions about the same place cost nothing.
- Rate-limited to one request per ``MIN_INTERVAL_S`` (Overpass fair use). This is about an
  external service, so it uses wall time (``time.monotonic``), not the platform Clock.
- Sends a real User-Agent from settings (``NOMINATIM_USER_AGENT``); never an email of its own.
- Returns a compact, neutral summary (name, a few tag keys, distance); never the raw response.
- Tests inject a fetcher with recorded responses; unit tests never touch the network.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from typing import Any

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
MIN_INTERVAL_S = 1.0
MAX_RADIUS_M = 500.0
MAX_FEATURES = 15
# Tag keys worth reporting; values are passed through as data, the platform assigns them no meaning.
SUMMARY_KEYS = (
    "name",
    "amenity",
    "natural",
    "leisure",
    "landuse",
    "water",
    "waterway",
    "building",
    "highway",
    "shop",
    "tourism",
    "place",
    "emergency",
)

Fetcher = Callable[[str], dict[str, Any]]  # Overpass QL -> parsed JSON


def _http_fetch(query: str) -> dict[str, Any]:
    import httpx

    from app.core.config import get_settings

    ua = get_settings().nominatim_user_agent or "geo-heatmap-platform"
    resp = httpx.post(OVERPASS_URL, data={"data": query}, headers={"User-Agent": ua}, timeout=20.0)
    resp.raise_for_status()
    return resp.json()


class OverpassClient:
    def __init__(self, fetch: Fetcher | None = None, min_interval_s: float = MIN_INTERVAL_S) -> None:
        self._fetch = fetch or _http_fetch
        self._min_interval = min_interval_s
        self._cache: dict[tuple[float, float, int], list[dict[str, Any]]] = {}
        self._last = -math.inf
        self._lock = threading.Lock()
        self.requests = 0  # real fetches made (for tests and the trace)

    def features_near(self, lat: float, lon: float, radius_m: float) -> list[dict[str, Any]]:
        radius = int(min(max(radius_m, 1.0), MAX_RADIUS_M))
        key = (round(lat, 4), round(lon, 4), radius)  # ~11 m grid
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            query = (
                f'[out:json][timeout:15];(nwr(around:{radius},{lat:.6f},{lon:.6f})[~"^(name|'
                + "|".join(SUMMARY_KEYS[1:])
                + ')$"~"."];);out center 60;'
            )
            try:
                raw = self._fetch(query)
            finally:
                self._last = time.monotonic()
                self.requests += 1
            feats = summarize(raw, lat, lon)
            self._cache[key] = feats
            return feats


def summarize(raw: dict[str, Any], lat: float, lon: float) -> list[dict[str, Any]]:
    """Nearest features first; only summary tags; distance in meters; no ids or geometry."""
    out = []
    for el in raw.get("elements", []):
        tags = el.get("tags") or {}
        pos = el.get("center") or el
        if "lat" not in pos or "lon" not in pos:
            continue
        kept = {k: str(tags[k])[:60] for k in SUMMARY_KEYS if k in tags}
        if not kept:
            continue
        out.append({"distance_m": round(_haversine(lat, lon, pos["lat"], pos["lon"]), 1), "tags": kept})
    out.sort(key=lambda f: f["distance_m"])
    return out[:MAX_FEATURES]


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6_371_008.8 * math.asin(math.sqrt(min(1.0, a)))


OVERPASS = OverpassClient()
