"""Campus Alert simulator.

Timeline (sim seconds):
  t0     threat appears; panic presses start GPS streams (runners flee, hiders stay put)
  0-30   hiders form groups inside rooms
  60     room alert button near the threat
  100    spoof burst: coordinated fake escape tracks + exact-duplicate stationary accounts
  150    police marker at the threat's position
  180    scripted news article appears in the RSS feed the connector polls
  throughout, the threat keeps moving

Events are stamped `ts = t0 + sim_t` (sim time). At --speed 1 that is wall time; at other speeds
the backend clock must run in sim mode. Posts go ONLY through the public ingest API.

    python -m sim.campus_alert --api http://localhost:8000 --app campus-alert --speed 1 --ingest-key KEY
    python -m sim.campus_alert --dry-run > events.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
from dataclasses import dataclass
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from xml.sax.saxutils import escape

import numpy as np

from sim.common import IngestError, LocalFrame, Post, Poster, iso, load_spec, replay, write_ground_truth

SPEC = "campus-alert"

# Scenario constants live here, never in backend/. Local meters from the bbox SW corner.
THREAT_PATH = [(0.0, (200.0, 200.0)), (60.0, (215.0, 235.0)), (150.0, (190.0, 265.0)), (240.0, (165.0, 280.0))]
N_RUNNERS = 48
RUNNER_RING_M = (25.0, 110.0)
RUNNER_SPEED = (2.5, 4.5)
RUNNER_HEADING_NOISE_DEG = 30.0
RUNNER_STREAM_S = 70.0
RUNNER_PING_S = 2.0
HIDE_ROOMS = [("A-112", (175.0, 215.0), 5), ("A-120", (232.0, 190.0), 4), ("A-214", (205.0, 252.0), 6)]
HIDER_PING_S = 5.0
GPS_SIGMA_OUT = 6.0
GPS_SIGMA_IN = 4.0
ROOM_BUTTON = (60.0, "A-118", (222.0, 228.0))
SPOOF_T = 100.0
SPOOF_ESCAPE = {"n": 10, "origin": (320.0, 90.0), "speed": 4.0, "duration": 30.0, "ping": 2.0}
SPOOF_DUPES = {"n": 6, "at": (300.0, 320.0), "ping": 3.0}
POLICE_T = 150.0
POLICE_OFFSET = (4.0, -3.0)
NEWS_T = 180.0
DURATION = 240.0


def threat_at(t: float) -> tuple[float, float]:
    for (t_a, a), (t_b, b) in zip(THREAT_PATH, THREAT_PATH[1:], strict=False):
        if t <= t_b:
            f = max(0.0, (t - t_a) / (t_b - t_a))
            return a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])
    return THREAT_PATH[-1][1]


@dataclass
class Scenario:
    posts: list[Post]
    truth: dict[str, Any]
    news: dict[str, Any]


def build(seed: int = 7, duration: float = DURATION) -> Scenario:
    """Deterministic for a given seed. No I/O."""
    spec = load_spec(SPEC)
    frame = LocalFrame.from_spec(spec)
    w, h = frame.size_m
    rng = np.random.default_rng(seed)
    posts: list[Post] = []

    def ll(x: float, y: float) -> tuple[float, float]:
        lat, lon = frame.to_latlon(min(max(x, 1.0), w - 1.0), min(max(y, 1.0), h - 1.0))
        return round(lat, 7), round(lon, 7)

    def panic(t: float, uid: str, x: float, y: float, press: str) -> None:
        if t <= duration:
            lat, lon = ll(x, y)
            posts.append(Post(round(t, 3), "panic", {"user_id": uid, "lat": lat, "lon": lon, "press_id": press}))

    # Runners: press within the first 15 s, flee away from the threat, stream for a while, then stop.
    tx, ty = threat_at(0.0)
    runners = []
    for i in range(N_RUNNERS):
        r = rng.uniform(*RUNNER_RING_M)
        a = rng.uniform(0, 2 * math.pi)
        sx, sy = tx + r * math.cos(a), ty + r * math.sin(a)
        heading = a + math.radians(rng.uniform(-RUNNER_HEADING_NOISE_DEG, RUNNER_HEADING_NOISE_DEG))
        v = rng.uniform(*RUNNER_SPEED)
        t_press = rng.uniform(0, 15)
        uid = f"u-run-{i:03d}"
        runners.append(uid)
        for k in range(int(RUNNER_STREAM_S / RUNNER_PING_S) + 1):
            dt = k * RUNNER_PING_S
            x = sx + v * dt * math.cos(heading) + rng.normal(0, GPS_SIGMA_OUT)
            y = sy + v * dt * math.sin(heading) + rng.normal(0, GPS_SIGMA_OUT)
            if not (0 < x < w and 0 < y < h):
                break  # left the campus: stops streaming
            panic(t_press + dt, uid, x, y, f"{uid}-p1")

    # Hiders: groups in rooms press within 5-30 s and stay put, pinging until the end.
    groups = []
    for room, (rx, ry), n in HIDE_ROOMS:
        members = []
        for j in range(n):
            uid = f"u-hide-{room}-{j}"
            members.append(uid)
            ox, oy = rng.normal(0, 2.0, 2)
            t = rng.uniform(5, 30)
            while t <= duration:
                panic(t, uid, rx + ox + rng.normal(0, GPS_SIGMA_IN), ry + oy + rng.normal(0, GPS_SIGMA_IN), f"{uid}-p1")
                t += HIDER_PING_S
        lat, lon = ll(rx, ry)
        groups.append({"room": room, "lat": lat, "lon": lon, "users": members})

    # Room alert button.
    t_btn, room, (bx, by) = ROOM_BUTTON
    lat, lon = ll(bx, by)
    posts.append(Post(t_btn, "room_button", {"lat": lat, "lon": lon, "room": room}))

    # Spoof burst: perfectly straight, identical-speed fake escape from a fake origin ...
    spoofers = []
    se = SPOOF_ESCAPE
    for i in range(se["n"]):
        uid = f"u-x{i:02d}"
        spoofers.append(uid)
        a = 2 * math.pi * i / se["n"]
        k = 0
        while k * se["ping"] <= se["duration"]:
            d = 10.0 + se["speed"] * k * se["ping"]
            panic(
                SPOOF_T + k * se["ping"],
                uid,
                se["origin"][0] + d * math.cos(a),
                se["origin"][1] + d * math.sin(a),
                f"{uid}-p1",
            )
            k += 1
    # ... plus new accounts all reporting the exact same coordinates.
    sd = SPOOF_DUPES
    for i in range(sd["n"]):
        uid = f"u-d{i:02d}"
        spoofers.append(uid)
        t = SPOOF_T + 0.2 * i
        while t <= duration:
            panic(t, uid, *sd["at"], f"{uid}-p1")
            t += sd["ping"]

    # Police marker.
    px, py = threat_at(POLICE_T)
    lat, lon = ll(px + POLICE_OFFSET[0], py + POLICE_OFFSET[1])
    if POLICE_T <= duration:
        posts.append(Post(POLICE_T, "police_marker", {"lat": lat, "lon": lon, "cleared": False}))

    # Scripted news: served on the RSS feed from NEWS_T, not posted.
    nx, ny = threat_at(NEWS_T)
    news_lat, news_lon = ll(nx, ny)
    news = {
        "sim_t": NEWS_T,
        "title": "[SCRIPTED DEMO] Police respond to reported threat at exhibition hall",
        "description": "Officers are on scene at the exhibition hall. Avoid the area; follow official instructions.",
        "lat": news_lat,
        "lon": news_lon,
    }

    posts.sort()
    threat = []
    for s in range(int(duration) + 1):
        lat, lon = ll(*threat_at(float(s)))
        threat.append({"sim_t": float(s), "lat": lat, "lon": lon})
    truth = {
        "runners": runners,
        "hider_groups": groups,
        "spoof_users": spoofers,
        "room_button": {"sim_t": t_btn, "room": room},
        "police_marker": {"sim_t": POLICE_T},
        "news": news,
        "gathering_points": spec["routing"]["destination_points"],
        "threat": threat,
    }
    return Scenario(posts, truth, news)


def ground_truth_lines(sc: Scenario, t0: float, seed: int, speed: float) -> list[dict[str, Any]]:
    tr = sc.truth
    meta = {k: v for k, v in tr.items() if k != "threat"}
    lines: list[dict[str, Any]] = [{"type": "meta", "app": SPEC, "seed": seed, "t0": iso(t0), "speed": speed, **meta}]
    lines += [{"type": "threat", "ts": iso(t0 + p["sim_t"]), **p} for p in tr["threat"]]
    return lines


def rss_xml(sc: Scenario, t0: float, sim_now: float) -> str:
    items = ""
    if sim_now >= sc.news["sim_t"]:
        n = sc.news
        items = (
            f"<item><title>{escape(n['title'])}</title><description>{escape(n['description'])}</description>"
            f'<guid isPermaLink="false">campus-alert-scripted-1</guid>'
            f"<pubDate>{formatdate(t0 + n['sim_t'], usegmt=True)}</pubDate>"
            f"<georss:point>{n['lat']} {n['lon']}</georss:point></item>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:georss="http://www.georss.org/georss"><channel>'
        "<title>Local news (scripted demo feed)</title><link>http://localhost/</link>"
        f"<description>Scripted for the Campus Alert demo.</description>{items}</channel></rss>"
    )


def serve_rss(sc: Scenario, t0: float, speed: float, port: int) -> ThreadingHTTPServer:
    wall0 = time.monotonic()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?")[0] != "/campus-news.xml":
                self.send_error(404)
                return
            body = rss_xml(sc, t0, (time.monotonic() - wall0) * speed).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/rss+xml; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--app", default=SPEC)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--ingest-key", default=os.environ.get("INGEST_KEY", ""), help="or env INGEST_KEY")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--duration", type=float, default=DURATION)
    ap.add_argument("--rss-port", type=int, default=8765, help="0 disables the scripted RSS feed")
    ap.add_argument("--dry-run", action="store_true", help="print events as JSONL and exit; no posting")
    args = ap.parse_args(argv)
    if args.speed <= 0:
        ap.error("--speed must be > 0")

    sc = build(args.seed, args.duration)
    t0 = time.time()
    if args.dry_run:
        for p in sc.posts:
            print(json.dumps({"sim_t": p.sim_t, "input": p.input_id, "body": p.stamped(t0)}))
        return 0
    if not args.ingest_key:
        ap.error("--ingest-key (or env INGEST_KEY) is required; it is shown once at app activation")

    gt = write_ground_truth(SPEC, ground_truth_lines(sc, t0, args.seed, args.speed))
    print(f"campus-alert sim: {len(sc.posts)} events over {args.duration:.0f}s at {args.speed}x; ground truth {gt}")
    server = serve_rss(sc, t0, args.speed, args.rss_port) if args.rss_port else None
    if server:
        print(f"  scripted RSS feed: http://127.0.0.1:{args.rss_port}/campus-news.xml (article at t={NEWS_T:.0f}s)")
    poster = Poster(args.api, args.app, args.ingest_key)
    try:
        replay(sc.posts, t0, args.speed, poster)
        poster.close()
    except IngestError as e:
        poster.close(cancel=True)
        print(f"stopping: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        poster.close(cancel=True)
        return 130
    finally:
        if server:
            server.shutdown()
    print(f"done: ok={poster.ok} failed={poster.failed}")
    return 0 if poster.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
