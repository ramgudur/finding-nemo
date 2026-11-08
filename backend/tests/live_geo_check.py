"""Live check of the external geo services (#219): Overpass and Nominatim. NOT part of the test suite
(it calls real public services); run by hand, sparingly:

    cd backend && python tests/live_geo_check.py [--save-fixtures]

Neutral location only (a city centre unrelated to any demo). About 6 Overpass and 5 Nominatim
requests in total, all through the clients' own 1 req/s limiter. Reports latency, result shapes,
caching, rate limiting, the timeout / failure-cache path, and the User-Agent actually sent.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent)]

from app.tools import geo_tools  # noqa: E402

# Neutral test area: central Bern, Switzerland (~1.5 x 1.5 km).
LAT, LON = 46.9480, 7.4474
BBOX = (7.437, 46.941, 7.458, 46.955)  # (min_lon, min_lat, max_lon, max_lat)
FIXTURES = HERE / "fixtures" / "geo_live"


def timed(fn, *a, **k):
    s = time.perf_counter()
    try:
        return fn(*a, **k), time.perf_counter() - s, None
    except Exception as e:  # noqa: BLE001 - report, don't crash the check
        return None, time.perf_counter() - s, f"{type(e).__name__}: {e}"


def overpass(save: bool) -> None:
    print("== Overpass ==")
    raw_seen: dict[str, dict] = {}

    def recording_fetch(query: str) -> dict:
        raw = geo_tools._http_fetch(query)
        raw_seen[query] = raw
        return raw

    c = geo_tools.OverpassClient(fetch=recording_fetch)
    for r in (50, 150, 400):
        feats, dt, err = timed(c.features_near, LAT, LON, r)
        n_raw = len(next(reversed(raw_seen.values()), {}).get("elements", [])) if raw_seen else 0
        print(f"  radius {r:>3} m: {dt:5.2f} s, {n_raw} raw elements -> {len(feats or [])} features {err or ''}")
        if feats:
            keys = sorted({k for f in feats for k in f})
            print(f"    shape keys {keys}; nearest {feats[0]}")
            assert len(feats) <= geo_tools.MAX_FEATURES
            assert all(set(f) == {"distance_m", "tags"} for f in feats)
    feats, dt, err = timed(c.features_near, LAT + 0.00001, LON + 0.00001, 50)  # same rounded key
    print(f"  repeat (cache): {dt * 1000:.1f} ms, requests so far {c.requests} {err or ''}")
    t0 = time.monotonic()
    stamps = []
    for k in range(2):  # a small burst at new places: must be spaced >= 1 s
        timed(c.features_near, LAT + 0.002 * (k + 1), LON, 50)
        stamps.append(time.monotonic() - t0)
    print(f"  burst of 2 new places finished at {[round(s, 2) for s in stamps]} s (limiter: >= 1 s apart)")

    # timeout + failure cache, without hammering the real host: an unreachable address, short timeout
    def unreachable(query: str) -> dict:
        import httpx

        return httpx.post("http://10.255.255.1/api/interpreter", data={"data": query}, timeout=2.0).json()

    f = geo_tools.OverpassClient(fetch=unreachable, min_interval_s=0)
    _, dt1, err1 = timed(f.features_near, LAT, LON, 50)
    _, dt2, err2 = timed(f.features_near, LAT, LON, 50)
    print(f"  failure: first {dt1:.2f} s ({err1}); repeat {dt2 * 1000:.1f} ms ({(err2 or '')[:70]}...)")
    print(f"  real-host timeout is {geo_tools.FETCH_TIMEOUT_S} s")
    if save and raw_seen:
        FIXTURES.mkdir(parents=True, exist_ok=True)
        q, raw = next(iter(raw_seen.items()))
        raw = {**raw, "elements": raw.get("elements", [])[:25]}
        meta = {"recorded": "live, Overpass, #219", "center": [LAT, LON], "radius_m": 50, "query": q}
        (FIXTURES / "overpass_bern_50m.json").write_text(json.dumps({"_meta": meta, **raw}, indent=1))


def nominatim(save: bool) -> None:
    print("== Nominatim ==")
    import httpx

    sent_ua = []
    raw_seen: dict[str, list] = {}

    def recording_search(params: dict[str, str]) -> list:
        from app.core.config import get_settings

        ua = get_settings().nominatim_user_agent or "geo-heatmap-platform"
        resp = httpx.get(geo_tools.NOMINATIM_URL, params=params, headers={"User-Agent": ua}, timeout=15.0)
        sent_ua.append(resp.request.headers.get("user-agent"))
        resp.raise_for_status()
        raw_seen[params["q"]] = resp.json()
        return resp.json()

    c = geo_tools.NominatimClient(search=recording_search)
    t0 = time.monotonic()
    for place in ("Bundesplatz", "Bahnhofplatz", "Copenhagen", "Springfield"):
        hit, dt, err = timed(c.geocode, place, BBOX)
        print(f"  {place!r:<14} at {time.monotonic() - t0:5.2f} s ({dt:4.2f} s): {hit} {err or ''}")
    _, dt, _ = timed(c.geocode, "bundesplatz", BBOX)
    print(f"  repeat (cache, other case): {dt * 1000:.1f} ms; requests {c.requests}")
    print(f"  User-Agent sent: {set(sent_ua)}")
    if save and "Bundesplatz" in raw_seen:
        FIXTURES.mkdir(parents=True, exist_ok=True)
        meta = {"recorded": "live, Nominatim jsonv2, #219", "q": "Bundesplatz", "viewbox_bbox": BBOX}
        (FIXTURES / "nominatim_bundesplatz.json").write_text(
            json.dumps({"_meta": meta, "results": raw_seen["Bundesplatz"]}, indent=1)
        )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--save-fixtures", action="store_true")
    a = ap.parse_args()
    overpass(a.save_fixtures)
    nominatim(a.save_fixtures)
