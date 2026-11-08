"""External geo lookups for agent tools: OpenStreetMap features near a point (Overpass).

- Cached by rounded (lat, lon, radius): repeated questions about the same place cost nothing.
- Fails fast: an ~8 s timeout, and a failed lookup is remembered for ``FAILURE_TTL_S`` per rounded
  key, so an agent asking again right away gets the error at once instead of waiting again.
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
FETCH_TIMEOUT_S = 8.0  # whole HTTP request; Overpass's own [timeout:] is set just under it
FAILURE_TTL_S = 60.0
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
    resp = httpx.post(OVERPASS_URL, data={"data": query}, headers={"User-Agent": ua}, timeout=FETCH_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json()


class OverpassUnavailable(RuntimeError):
    """A recent lookup for this place failed; not retried until the failure expires."""


class OverpassClient:
    def __init__(
        self,
        fetch: Fetcher | None = None,
        min_interval_s: float = MIN_INTERVAL_S,
        failure_ttl_s: float = FAILURE_TTL_S,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch or _http_fetch
        self._min_interval = min_interval_s
        self._failure_ttl = failure_ttl_s
        self._monotonic = monotonic
        self._cache: dict[tuple[float, float, int], list[dict[str, Any]]] = {}
        self._failed: dict[tuple[float, float, int], tuple[float, str]] = {}  # key -> (expires, error)
        self._last = -math.inf
        self._lock = threading.Lock()
        self.requests = 0  # real fetches made (for tests and the trace)

    def features_near(self, lat: float, lon: float, radius_m: float) -> list[dict[str, Any]]:
        radius = int(min(max(radius_m, 1.0), MAX_RADIUS_M))
        key = (round(lat, 4), round(lon, 4), radius)  # ~11 m grid
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            failed = self._failed.get(key)
            if failed is not None:
                expires, err = failed
                left = expires - self._monotonic()
                if left > 0:
                    raise OverpassUnavailable(
                        f"OpenStreetMap lookup failed here recently ({err}); not retried for another {left:.0f} s"
                    )
                del self._failed[key]
            wait = self._min_interval - (self._monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            query = (
                f'[out:json][timeout:{int(FETCH_TIMEOUT_S) - 1}];(nwr(around:{radius},{lat:.6f},{lon:.6f})[~"^(name|'
                + "|".join(SUMMARY_KEYS[1:])
                + ')$"~"."];);out center 60;'
            )
            try:
                raw = self._fetch(query)
                feats = summarize(raw, lat, lon)
            except Exception as e:
                self._failed[key] = (self._monotonic() + self._failure_ttl, f"{type(e).__name__}: {e}"[:200])
                raise
            finally:
                self._last = self._monotonic()
                self.requests += 1
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


# -- geocoding (Nominatim) -------------------------------------------------------------

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
MAX_PLACE_CHARS = 200

Searcher = Callable[[dict[str, str]], list[dict[str, Any]]]  # query params -> parsed JSON results


def _http_search(params: dict[str, str]) -> list[dict[str, Any]]:
    import httpx

    from app.core.config import get_settings

    ua = get_settings().nominatim_user_agent or "geo-heatmap-platform"
    resp = httpx.get(NOMINATIM_URL, params=params, headers={"User-Agent": ua}, timeout=15.0)
    resp.raise_for_status()
    return resp.json()


class NominatimClient:
    """Place name -> (lat, lon), bounded to an area. One request per second (Nominatim policy), cached
    by (place, rounded viewbox) so the same lookup never repeats."""

    def __init__(self, search: Searcher | None = None, min_interval_s: float = MIN_INTERVAL_S) -> None:
        self._search = search or _http_search
        self._min_interval = min_interval_s
        self._cache: dict[tuple[str, tuple[float, ...]], dict[str, Any] | None] = {}
        self._last = -math.inf
        self._lock = threading.Lock()
        self.requests = 0

    def geocode(self, place: str, bbox: tuple[float, float, float, float]) -> dict[str, Any] | None:
        """``bbox`` is (min_lon, min_lat, max_lon, max_lat). Returns {lat, lon, display_name} or None."""
        place = " ".join(place.split())[:MAX_PLACE_CHARS]
        if not place:
            return None
        key = (place.lower(), tuple(round(v, 3) for v in bbox))
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            wait = self._min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            min_lon, min_lat, max_lon, max_lat = bbox
            params = {
                "q": place,
                "format": "jsonv2",
                "limit": "1",
                "bounded": "1",
                "viewbox": f"{min_lon},{max_lat},{max_lon},{min_lat}",
            }
            try:
                results = self._search(params)
            finally:
                self._last = time.monotonic()
                self.requests += 1
            hit = None
            if results:
                r = results[0]
                hit = {
                    "lat": float(r["lat"]),
                    "lon": float(r["lon"]),
                    "display_name": str(r.get("display_name", ""))[:80],
                }
            self._cache[key] = hit
            return hit


NOMINATIM = NominatimClient()


async def geocode_place(place: str, spec: Any, client: NominatimClient | None = None) -> tuple[float, float] | None:
    """Plain async geocoder for code paths outside a tool call (connectors' locate). Bounded to the
    app's area; None when nothing is found there."""
    import asyncio

    hit = await asyncio.to_thread((client or NOMINATIM).geocode, place, spec.grid.bbox.as_geojson())
    return None if hit is None else (hit["lat"], hit["lon"])
