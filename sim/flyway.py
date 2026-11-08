"""Flyway replay simulator: tagged birds dwelling at stopovers and flying between them.

The replay is a committed, deterministic JSONL file (sim/replays/flyway_short.jsonl) of tag pings.
Replaying posts them ONLY through the public ingest API with X-Ingest-Key, stamped ts = t0 + sim_t.
The true stopovers go to sim/ground_truth/ (read only by api/debug.py).

    python -m sim.flyway --replay sim/replays/flyway_short.jsonl --api http://localhost:8000 --app flyway --speed 20
    python -m sim.flyway --generate sim/replays/flyway_short.jsonl     # rebuild the replay file
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from sim.common import (
    REPO,
    IngestError,
    LocalFrame,
    Post,
    Poster,
    ServerClock,
    iso,
    load_spec,
    replay,
    replay_following,
    write_ground_truth,
)

SPEC = "flyway"
DEFAULT_REPLAY = REPO / "sim" / "replays" / "flyway_short.jsonl"

# Scenario constants live here, never in backend/. Local meters from the bbox SW corner (4 km box).
STOPOVERS = [
    ("north shore", (1400.0, 2700.0)),
    ("east cove", (2900.0, 1700.0)),
    ("south marsh", (1700.0, 900.0)),
]
N_BIRDS = 12
PING_S = 60.0
DURATION_S = 75 * 60.0
GPS_SIGMA_M = 15.0
SPEED_MPS = (11.0, 16.0)
FIRST_STAY_S = (15 * 60.0, 35 * 60.0)
WANDER_M = 25.0  # how far a bird ambles while it stays


@dataclass
class Scenario:
    posts: list[Post]
    truth: dict[str, Any]


def generate(seed: int = 11) -> Scenario:
    """Each bird stays at one stopover, flies to another along a gently curved path, and stays there."""
    frame = LocalFrame.from_spec(load_spec(SPEC))
    rng = np.random.default_rng(seed)
    posts: list[Post] = []
    itineraries = []

    def ll(x: float, y: float) -> tuple[float, float]:
        lat, lon = frame.to_latlon(x, y)
        return round(lat, 7), round(lon, 7)

    for b in range(N_BIRDS):
        a, z = rng.choice(len(STOPOVERS), size=2, replace=False)
        (_, pa), (_, pz) = STOPOVERS[a], STOPOVERS[z]
        home_a = np.array(pa) + rng.normal(0, 20, 2)
        home_z = np.array(pz) + rng.normal(0, 20, 2)
        leave = rng.uniform(*FIRST_STAY_S)
        speed = rng.uniform(*SPEED_MPS)
        length = float(np.linalg.norm(home_z - home_a))
        arrive = leave + length / speed
        bend = rng.normal(0, 0.15) * length  # sideways bow of the flight path
        normal = np.array([-(home_z - home_a)[1], (home_z - home_a)[0]]) / max(length, 1e-9)
        uid = f"tag-{b:03d}"
        itineraries.append(
            {"user_id": uid, "from": STOPOVERS[a][0], "to": STOPOVERS[z][0], "leave_s": leave, "arrive_s": arrive}
        )
        t = rng.uniform(0, PING_S)
        while t <= DURATION_S:
            if t < leave:
                pos = home_a + rng.normal(0, WANDER_M, 2)
            elif t < arrive:
                f = (t - leave) / (arrive - leave)
                pos = home_a + f * (home_z - home_a) + math.sin(math.pi * f) * bend * normal
            else:
                pos = home_z + rng.normal(0, WANDER_M, 2)
            pos = pos + rng.normal(0, GPS_SIGMA_M, 2)
            lat, lon = ll(float(pos[0]), float(pos[1]))
            posts.append(Post(round(float(t), 3), "tags", {"user_id": uid, "lat": lat, "lon": lon, "tag_id": uid}))
            t += PING_S
    posts.sort()
    stopovers = [{"name": n, **dict(zip(("lat", "lon"), ll(*xy), strict=True))} for n, xy in STOPOVERS]
    return Scenario(posts, {"stopovers": stopovers, "birds": itineraries, "seed": seed})


def write_replay(sc: Scenario, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"type": "meta", "app": SPEC, "truth": sc.truth}) + "\n")
        for p in sc.posts:
            f.write(json.dumps({"sim_t": p.sim_t, "input": p.input_id, "body": p.body}) + "\n")


def load_replay(path: Path) -> Scenario:
    posts, truth = [], {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("type") == "meta":
            truth = row["truth"]
        else:
            posts.append(Post(float(row["sim_t"]), row["input"], row["body"]))
    return Scenario(sorted(posts), truth)


def ground_truth_lines(sc: Scenario, t0: float) -> list[dict[str, Any]]:
    """Stopovers are fixed points: `{"type": "site"}` lines (api/debug.py's source track is for moving sources)."""
    lines: list[dict[str, Any]] = [{"type": "meta", "app": SPEC, "t0": iso(t0), "birds": sc.truth.get("birds", [])}]
    lines += [{"type": "site", **s} for s in sc.truth.get("stopovers", [])]
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--generate", type=Path, help="write a fresh replay file here and exit")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--replay", type=Path, default=DEFAULT_REPLAY)
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--app", default=SPEC)
    ap.add_argument("--speed", type=float, default=20.0, help="sim seconds per wall second (75 min at 20x is ~4 min)")
    ap.add_argument("--ingest-key", default=os.environ.get("INGEST_KEY", ""), help="or env INGEST_KEY")
    ap.add_argument("--follow-clock", action="store_true", help="pace by the server clock (DEMO_MODE SimControls)")
    args = ap.parse_args(argv)

    if args.generate:
        sc = generate(args.seed)
        write_replay(sc, args.generate)
        print(f"wrote {len(sc.posts)} pings for {N_BIRDS} tags to {args.generate}")
        return 0
    if args.speed <= 0:
        ap.error("--speed must be > 0")
    if not args.ingest_key:
        ap.error("--ingest-key (or env INGEST_KEY) is required; it is shown once at app activation")

    sc = load_replay(args.replay)
    server_clock = ServerClock(args.api) if args.follow_clock else None
    t0 = server_clock.now() if server_clock else time.time()
    gt = write_ground_truth(SPEC, ground_truth_lines(sc, t0))
    print(f"flyway replay: {len(sc.posts)} pings at {args.speed}x; ground truth {gt}")
    poster = Poster(args.api, args.app, args.ingest_key)
    try:
        if server_clock:
            replay_following(sc.posts, t0, server_clock, poster, log_every_s=600)
        else:
            replay(sc.posts, t0, args.speed, poster, log_every_s=600)
        poster.close()
    except IngestError as e:
        poster.close(cancel=True)
        print(f"stopping: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        poster.close(cancel=True)
        return 130
    print(f"done: ok={poster.ok} failed={poster.failed}")
    return 0 if poster.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
