"""Per-user trust: spoof features and trust-change bounds.

Spoof features (`suspicious_users`, behind the `get_suspicious_users` tool). Each is a flag, not a
verdict; the Critic weighs them and any trust change still has to pass a backtest.
  - duplicate_coords: the exact same coordinates (to ~1 cm) as a DIFFERENT account. Real position
    readings practically never collide; scripted accounts do.
  - impossible_speed: a hop faster than `max_speed_mps` even after subtracting a position-error
    allowance (`jitter_m`) from its length.
  - new_id_burst: first seen inside a short window with many new IDs at once AND far above the
    batch's usual rate of new IDs. The first `onset_s` of a batch is skipped: the start of an
    event is a natural wave of new IDs; a scripted burst later on stands out against the background.
  - constant_speed: a run of >= 4 hops with (near-)identical speed; real movement speed wobbles.
  - straight_track: a run of >= 5 pings on a perfectly straight line over a real distance; position
    noise bends it. (Runs, not whole tracks: a scripted track may stop or turn later.)
  - shared_device / shared_ip: the same `device_id` / `ip` field value as another account.
Being close to other entities is never a feature: clusters are what the platform is for.

Domain limits come from the spec: each input's `plausibility` ({max_speed_mps?, jitter_m?,
flag_constant_speed?, flag_straight_line?}) overrides the neutral defaults below
(`SpoofParams.from_plausibility`, `suspicious_users_by_input`). Something that legitimately moves
fast or in straight lines sets its own limits or turns those checks off.

Trust bounds (`check_trust_change`, called by tools/guards.py): trust lives in [0, 1]; agents may
lower it freely with a reason; an increase is at most +0.1 and at most once per 5 minutes of Clock
time per user (the DB keeps `last_increase_at`).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

import numpy as np

from app.engine.operators.base import Events

MAX_INCREASE = 0.1
INCREASE_PERIOD_S = 300.0
DEFAULT_TRUST = 1.0
_M_PER_DEG = 6_371_000.0 * np.pi / 180.0

FLAG_WEIGHTS: dict[str, float] = {
    "duplicate_coords": 0.5,
    "shared_device": 0.5,
    "shared_ip": 0.3,
    "impossible_speed": 0.4,
    "constant_speed": 0.3,
    "straight_track": 0.3,
    "new_id_burst": 0.15,
}


@dataclass(frozen=True)
class SpoofParams:
    coord_decimals: int = 7  # ~1 cm: "exact" duplicate
    max_speed_mps: float = 12.0  # neutral default; inputs set their own via plausibility
    jitter_m: float = 30.0  # position-error allowance subtracted from each hop before the speed test
    burst_window_s: float = 10.0
    burst_min_ids: int = 15
    burst_factor: float = 5.0  # window count vs the rest of the batch's new-ID rate per window
    onset_s: float = 60.0  # the first minute of a batch is a natural wave of new IDs: never a burst
    min_hops: int = 4
    speed_cv_max: float = 0.01  # coefficient of variation below this = identical speeds
    straight_dev_m: float = 0.5  # max sideways deviation for a "perfectly straight" track
    straight_min_m: float = 20.0
    device_field: str = "device_id"
    ip_field: str = "ip"
    flag_constant_speed: bool = True
    flag_straight_line: bool = True

    @classmethod
    def from_plausibility(cls, plausibility: object | None, base: SpoofParams | None = None) -> SpoofParams:
        """Apply an input's `plausibility` (a mapping or a model with those attributes; None keeps
        the defaults). Only set values override."""
        out = base or cls()
        if plausibility is None:
            return out
        get = plausibility.get if isinstance(plausibility, Mapping) else lambda k: getattr(plausibility, k, None)
        updates = {k: get(k) for k in _PLAUSIBILITY_KEYS}
        return replace(out, **{k: v for k, v in updates.items() if v is not None})


_PLAUSIBILITY_KEYS = ("max_speed_mps", "jitter_m", "flag_constant_speed", "flag_straight_line")


@dataclass
class UserSuspicion:
    user_id: str
    score: float  # 0..1, sum of flag weights clipped
    flags: list[str]
    details: dict[str, float] = field(default_factory=dict)
    n_events: int = 0


def _xy(lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lat0 = float(np.mean(lat)) if len(lat) else 0.0
    return (lon - np.mean(lon)) * _M_PER_DEG * np.cos(np.radians(lat0)), (lat - lat0) * _M_PER_DEG


def _shared(values: np.ndarray, users: np.ndarray) -> set[str]:
    """Users who share any non-empty value with a different user."""
    by_value: dict[object, set[str]] = defaultdict(set)
    for v, u in zip(values, users, strict=True):
        if v is not None and v != "":
            by_value[v].add(u)
    return {u for us in by_value.values() if len(us) > 1 for u in us}


def suspicious_users(events: Events, params: SpoofParams | None = None) -> list[UserSuspicion]:
    """Per-user spoof flags for one batch of events (any input shape). Events without a user are skipped.
    Returns flagged users only, most suspicious first."""
    p = params or SpoofParams()
    ev = events.select(events.user_id != "")
    if not len(ev):
        return []
    users = ev.user_id
    x, y = _xy(ev.lat, ev.lon)
    flags: dict[str, set[str]] = defaultdict(set)
    details: dict[str, dict[str, float]] = defaultdict(dict)

    # duplicate coordinates across accounts
    coords = np.array(
        [f"{a:.{p.coord_decimals}f},{o:.{p.coord_decimals}f}" for a, o in zip(ev.lat, ev.lon, strict=True)],
        dtype=object,
    )
    for u in _shared(coords, users):
        flags[u].add("duplicate_coords")

    # shared device / ip
    for name, fld in (("shared_device", p.device_field), ("shared_ip", p.ip_field)):
        if fld in ev.fields:
            for u in _shared(ev.fields[fld], users):
                flags[u].add(name)

    # bursts of new ids: first-seen times
    uniq = np.unique(users)
    first = np.array([ev.t[users == u].min() for u in uniq])
    order = np.argsort(first)
    fs = first[order]
    after = fs >= fs[0] + p.onset_s  # first-seen times past the onset
    fs_after = fs[after]
    for i, u in enumerate(uniq[order]):
        if not after[i]:
            continue
        lo = np.searchsorted(fs_after, fs[i] - p.burst_window_s / 2, side="left")
        hi = np.searchsorted(fs_after, fs[i] + p.burst_window_s / 2, side="right")
        n_in = hi - lo
        n_out = len(fs_after) - n_in
        if n_in < p.burst_min_ids or n_out < p.burst_min_ids:
            continue  # too few in the window, or too little background to call it a burst
        span_out = max(fs_after[-1] - fs_after[0] - p.burst_window_s, p.burst_window_s)
        rate_out = n_out / span_out * p.burst_window_s  # new IDs per window elsewhere (post-onset)
        if n_in >= p.burst_factor * rate_out:
            flags[u].add("new_id_burst")
            details[u]["burst_ids"] = float(n_in)

    # per-track features
    for u in uniq:
        idx = np.nonzero(users == u)[0]
        idx = idx[np.argsort(ev.t[idx], kind="stable")]
        if len(idx) < 2:
            continue
        dx, dy, dt = np.diff(x[idx]), np.diff(y[idx]), np.diff(ev.t[idx])
        dist = np.hypot(dx, dy)
        ok = dt > 0
        speed = np.where(ok, dist / np.where(ok, dt, 1.0), np.inf)
        # speed beyond position error: subtract the jitter allowance from each hop first
        excess = np.where(ok, (dist - p.jitter_m) / np.where(ok, dt, 1.0), np.where(dist > p.jitter_m, np.inf, 0.0))
        if (excess > p.max_speed_mps).any():
            flags[u].add("impossible_speed")
            details[u]["max_speed_mps"] = float(np.max(speed[excess > p.max_speed_mps]))
        # constant speed / straight line: any run of min_hops consecutive hops (a scripted track
        # can stop or bend later, e.g. at an edge, so whole-track tests would miss it)
        k = p.min_hops
        if not (p.flag_constant_speed or p.flag_straight_line):
            continue
        for a0 in range(len(dist) - k + 1):
            seg = slice(a0, a0 + k)
            if p.flag_constant_speed and ok[seg].all() and (dist[seg] > 0).all():
                sp = speed[seg]
                cv = float(sp.std() / sp.mean())
                if cv < p.speed_cv_max:
                    flags[u].add("constant_speed")
                    details[u]["speed_cv"] = min(cv, details[u].get("speed_cv", cv))
            px = x[idx[a0 : a0 + k + 1]] - x[idx[a0]]
            py = y[idx[a0 : a0 + k + 1]] - y[idx[a0]]
            span = np.hypot(px[-1], py[-1])
            if p.flag_straight_line and span >= p.straight_min_m:
                dev = float((np.abs(px * py[-1] - py * px[-1]) / span).max())  # off the first->last line
                if dev < p.straight_dev_m:
                    flags[u].add("straight_track")
                    details[u]["max_dev_m"] = min(dev, details[u].get("max_dev_m", dev))

    counts = {u: int(n) for u, n in zip(*np.unique(users, return_counts=True), strict=True)}
    out = [
        UserSuspicion(
            user_id=str(u),
            score=float(min(1.0, sum(FLAG_WEIGHTS[f] for f in fl))),
            flags=sorted(fl),
            details=details.get(u, {}),
            n_events=counts[u],
        )
        for u, fl in flags.items()
        if fl
    ]
    out.sort(key=lambda s: (-s.score, s.user_id))
    return out


def suspicious_users_by_input(
    events_by_input: Mapping[str, Events],
    plausibility_by_input: Mapping[str, object | None] | None = None,
    base: SpoofParams | None = None,
) -> list[UserSuspicion]:
    """Run `suspicious_users` per input with that input's plausibility limits, then merge per user
    (union of flags, max score). Use this instead of concatenating inputs with different limits."""
    merged: dict[str, UserSuspicion] = {}
    for inp, ev in events_by_input.items():
        params = SpoofParams.from_plausibility((plausibility_by_input or {}).get(inp), base)
        for s in suspicious_users(ev, params):
            cur = merged.get(s.user_id)
            if cur is None:
                merged[s.user_id] = s
                continue
            cur.flags = sorted(set(cur.flags) | set(s.flags))
            cur.score = float(min(1.0, sum(FLAG_WEIGHTS[f] for f in cur.flags)))
            cur.details.update(s.details)
            cur.n_events += s.n_events
    return sorted(merged.values(), key=lambda s: (-s.score, s.user_id))


def summarize(suspects: list[UserSuspicion], limit: int = 20) -> dict:
    """Compact summary for the agent: no raw coordinates or event arrays."""
    by_flag: dict[str, int] = defaultdict(int)
    for s in suspects:
        for f in s.flags:
            by_flag[f] += 1
    return {
        "n_flagged": len(suspects),
        "by_flag": dict(sorted(by_flag.items())),
        "top": [
            {"user_id": s.user_id, "score": round(s.score, 2), "flags": s.flags, "n_events": s.n_events}
            for s in suspects[:limit]
        ],
    }


# -- trust bounds ---------------------------------------------------------------


@dataclass(frozen=True)
class TrustCheck:
    ok: bool
    reason: str


def check_trust_change(
    before: float | None,
    after: float,
    *,
    now: float,
    last_increase_at: float | None,
    reason: str = "",
) -> TrustCheck:
    """Validate one agent trust change. `before` None means the user has default trust (1.0).
    Times are Clock seconds."""
    prev = DEFAULT_TRUST if before is None else before
    if not np.isfinite(after) or not 0.0 <= after <= 1.0:
        return TrustCheck(False, f"trust must be in [0, 1], got {after}")
    if after < prev:
        if not reason.strip():
            return TrustCheck(False, "lowering trust needs a reason")
        return TrustCheck(True, "decrease")
    if after == prev:
        return TrustCheck(True, "no change")
    if after - prev > MAX_INCREASE + 1e-12:
        return TrustCheck(False, f"increase {after - prev:.3f} exceeds +{MAX_INCREASE} per {INCREASE_PERIOD_S:.0f} s")
    if last_increase_at is not None and now - last_increase_at < INCREASE_PERIOD_S:
        wait = INCREASE_PERIOD_S - (now - last_increase_at)
        return TrustCheck(False, f"trust was raised {now - last_increase_at:.0f} s ago; next increase in {wait:.0f} s")
    return TrustCheck(True, "increase")


def trust_map(records: Mapping[str, float]) -> dict[str, float]:
    """Clip a {user_id: trust} mapping into [0, 1] for operators (missing users default to 1.0 there)."""
    return {u: float(np.clip(v, 0.0, 1.0)) for u, v in records.items()}
