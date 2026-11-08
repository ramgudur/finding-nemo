import numpy as np
import pytest
from synthetic import LocalGrid, events_from_xy, flee_scenario

from app.engine.operators.base import Events
from app.engine.trust import (
    INCREASE_PERIOD_S,
    SpoofParams,
    check_trust_change,
    summarize,
    suspicious_users,
    suspicious_users_by_input,
    trust_map,
)

GRID = LocalGrid()


def _concat(*evs: Events) -> Events:
    names = sorted({k for e in evs for k in e.fields})
    return Events(
        np.concatenate([e.user_id for e in evs]),
        np.concatenate([e.t for e in evs]),
        np.concatenate([e.lat for e in evs]),
        np.concatenate([e.lon for e in evs]),
        {k: np.concatenate([e.fields.get(k, np.full(len(e), None, dtype=object)) for e in evs]) for k in names},
    )


def _flags(events, **kw):
    return {s.user_id: s.flags for s in suspicious_users(events, SpoofParams(**kw) if kw else None)}


def test_real_crowd_is_not_flagged_even_when_dense():
    # 200 people fleeing with GPS noise, many close together: nobody is flagged
    ev = flee_scenario(GRID, origin=(250.0, 250.0), seed=0)
    # stagger first presses over a minute, like real people noticing at different times
    rng = np.random.default_rng(0)
    offset = {u: rng.uniform(0, 60) for u in np.unique(ev.user_id)}
    ev = Events(ev.user_id, ev.t + np.array([offset[u] for u in ev.user_id]), ev.lat, ev.lon)
    assert _flags(ev) == {}
    # a tight crowd standing still together is fine too
    rng = np.random.default_rng(1)
    crowd = [
        (f"c{k}", float(t), *(np.array([250.0, 250.0]) + rng.normal(0, 3, 2)))
        for k in range(30)
        for t in range(0, 120, 10)
    ]
    assert _flags(events_from_xy(GRID, crowd)) == {}


def test_exact_duplicate_stationary_accounts():
    fakes = [(f"s{k}", float(t), 100.0, 300.0) for k in range(10) for t in range(0, 60, 5)]
    lone = [("loner", float(t), 400.0, 100.0) for t in range(0, 60, 5)]  # repeats its own spot only
    f = _flags(events_from_xy(GRID, fakes + lone))
    assert all("duplicate_coords" in f[f"s{k}"] for k in range(10))
    assert "loner" not in f


def test_scripted_escape_constant_speed_and_straight():
    # coordinated fake escape: perfectly straight lines at exactly 3 m/s
    rows = []
    for k in range(8):
        ang = 2 * np.pi * k / 8
        d = np.array([np.cos(ang), np.sin(ang)])
        rows += [(f"f{k}", float(t), *(np.array([250.0, 250.0]) + d * 3.0 * t)) for t in range(0, 41, 5)]
    f = _flags(events_from_xy(GRID, rows))
    for k in range(8):
        assert {"constant_speed", "straight_track"} <= set(f[f"f{k}"])
    # the same tracks with GPS noise are not flagged
    rng = np.random.default_rng(2)
    noisy = [(u, t, x + rng.normal(0, 5), y + rng.normal(0, 5)) for u, t, x, y in rows]
    assert _flags(events_from_xy(GRID, noisy)) == {}


def test_impossible_speed_but_not_jitter():
    teleport = [("tp", 0.0, 50.0, 50.0), ("tp", 5.0, 450.0, 450.0)]  # 566 m in 5 s
    jitter = [("j", 0.0, 100.0, 100.0), ("j", 0.5, 110.0, 105.0)]  # 22 m/s but only 11 m: GPS noise
    f = _flags(events_from_xy(GRID, teleport + jitter))
    assert f == {"tp": ["impossible_speed"]}
    s = suspicious_users(events_from_xy(GRID, teleport))[0]
    assert s.details["max_speed_mps"] > 100


def test_new_id_burst():
    rng = np.random.default_rng(3)
    background = [(f"b{k}", float(k * 20 + 10), *rng.uniform(0, 500, 2)) for k in range(30)]
    burst = [(f"x{k}", 300.0 + rng.uniform(0, 4), *rng.uniform(0, 500, 2)) for k in range(20)]
    f = _flags(events_from_xy(GRID, background + burst))
    assert all("new_id_burst" in f[f"x{k}"] for k in range(20))
    assert not any(u.startswith("b") for u in f)
    # everyone appearing at once with no background to compare against is not a burst
    alone = [(f"x{k}", 300.0 + rng.uniform(0, 4), *rng.uniform(0, 500, 2)) for k in range(20)]
    assert _flags(events_from_xy(GRID, alone)) == {}


def test_shared_device_and_ip():
    rows = [("a", 0.0, 10.0, 10.0, "dev1", "1.2.3.4"), ("b", 1.0, 300.0, 300.0, "dev1", "5.6.7.8")]
    rows += [("c", 2.0, 200.0, 50.0, "dev2", "1.2.3.4"), ("d", 3.0, 50.0, 200.0, None, None)]
    f = _flags(events_from_xy(GRID, rows, ("device_id", "ip")))
    assert f == {"a": ["shared_device", "shared_ip"], "b": ["shared_device"], "c": ["shared_ip"]}


def test_scores_rank_and_summary_has_no_coordinates():
    fakes = [(f"s{k}", float(t), 100.0, 300.0) for k in range(5) for t in range(0, 60, 5)]
    teleport = [("tp", 0.0, 50.0, 50.0), ("tp", 5.0, 450.0, 450.0)]
    ev = _concat(events_from_xy(GRID, fakes), events_from_xy(GRID, teleport))
    sus = suspicious_users(ev)
    assert sus[0].score >= sus[-1].score and all(0 < s.score <= 1 for s in sus)
    summ = summarize(sus, limit=3)
    assert summ["n_flagged"] == 6 and summ["by_flag"] == {"duplicate_coords": 5, "impossible_speed": 1}
    assert len(summ["top"]) == 3
    assert "lat" not in str(summ) and "lon" not in str(summ)


def test_anonymous_and_empty():
    assert suspicious_users(events_from_xy(GRID, [])) == []
    assert suspicious_users(events_from_xy(GRID, [("", 0.0, 1.0, 1.0), ("", 0.0, 1.0, 1.0)])) == []


# -- trust bounds -----------------------------------------------------------------------


def test_trust_increase_cap_is_enforced():
    # +0.1 is fine once; more than +0.1 is not
    assert check_trust_change(0.5, 0.6, now=1000.0, last_increase_at=None).ok
    big = check_trust_change(0.5, 0.65, now=1000.0, last_increase_at=None)
    assert not big.ok and "exceeds" in big.reason
    # a second increase inside 5 minutes of Clock time is rejected, after 5 minutes it's allowed
    early = check_trust_change(0.6, 0.7, now=1000.0 + 299.0, last_increase_at=1000.0)
    assert not early.ok and "next increase" in early.reason
    assert check_trust_change(0.6, 0.7, now=1000.0 + INCREASE_PERIOD_S, last_increase_at=1000.0).ok


def test_decreases_are_free_with_a_reason():
    assert check_trust_change(0.9, 0.0, now=0.0, last_increase_at=None, reason="duplicate_coords").ok
    assert check_trust_change(None, 0.2, now=0.0, last_increase_at=None, reason="spoof cluster").ok
    no_reason = check_trust_change(0.9, 0.1, now=0.0, last_increase_at=None)
    assert not no_reason.ok and "reason" in no_reason.reason
    # decreases ignore the increase timer
    assert check_trust_change(0.9, 0.8, now=10.0, last_increase_at=5.0, reason="r").ok


@pytest.mark.parametrize("after", [-0.1, 1.1, float("nan")])
def test_trust_range(after):
    assert not check_trust_change(0.5, after, now=0.0, last_increase_at=None, reason="r").ok


def test_default_trust_and_trust_map():
    assert not check_trust_change(None, 1.0 + 1e-9, now=0.0, last_increase_at=None).ok
    assert check_trust_change(None, 1.0, now=0.0, last_increase_at=None).reason == "no change"
    assert trust_map({"a": 1.5, "b": -1.0, "c": 0.3}) == {"a": 1.0, "b": 0.0, "c": 0.3}


@pytest.mark.parametrize("seed", [7, 1, 2])
def test_campus_alert_sim_spoofers_flagged_and_real_users_not(seed):
    from sim.campus_alert import build  # test-only: demo values stay out of backend/app

    sc = build(seed=seed)
    ev = Events.from_records([{**p.body, "t": p.sim_t} for p in sc.posts if p.input_id == "panic"])
    flagged = {s.user_id for s in suspicious_users(ev)}
    spoof = set(sc.truth["spoof_users"])
    assert flagged == spoof


def _fast_straight_tracks(prefix, speed=40.0, n=6):
    """Fast, perfectly straight, constant-speed tracks: suspicious for slow entities,
    normal for entities that legitimately move that way."""
    rows = []
    for k in range(n):
        y0 = 50.0 + 60 * k
        rows += [(f"{prefix}{k}", float(t), 10.0 + speed * t, y0) for t in range(0, 21, 2)]
    return events_from_xy(GRID, rows)


def test_plausibility_from_spec_overrides_defaults():
    ev = _fast_straight_tracks("m")
    flagged = _flags(ev)
    assert all({"impossible_speed", "constant_speed", "straight_track"} <= set(f) for f in flagged.values())
    fast_ok = SpoofParams.from_plausibility(
        {"max_speed_mps": 30.0, "flag_constant_speed": False, "flag_straight_line": False}
    )
    assert suspicious_users(ev, fast_ok) == []

    # unset keys keep the defaults; objects with attributes work too
    class Plaus:
        max_speed_mps = 30.0
        jitter_m = None
        flag_constant_speed = None
        flag_straight_line = None

    p = SpoofParams.from_plausibility(Plaus())
    assert p.max_speed_mps == 30.0 and p.jitter_m == SpoofParams().jitter_m and p.flag_straight_line
    assert SpoofParams.from_plausibility(None) == SpoofParams()


def test_by_input_uses_each_inputs_limits_and_merges():
    fast = _fast_straight_tracks("m")
    dup = events_from_xy(GRID, [(f"s{k}", float(t), 100.0, 300.0) for k in range(3) for t in range(0, 30, 5)])
    sus = suspicious_users_by_input(
        {"tags": fast, "presses": dup},
        {"tags": {"max_speed_mps": 30.0, "flag_constant_speed": False, "flag_straight_line": False}},
    )
    assert {s.user_id for s in sus} == {"s0", "s1", "s2"}
    # a user flagged in two inputs gets the union of flags
    both = suspicious_users_by_input({"a": dup, "b": _fast_straight_tracks("s", n=1)})
    s0 = next(s for s in both if s.user_id == "s0")
    assert {"duplicate_coords", "impossible_speed"} <= set(s0.flags)


def test_plausibility_from_the_spec_model():
    from app.core.models import InputSpec

    inp = InputSpec.model_validate(
        {
            "id": "tags",
            "kind": "push",
            "shape": "track",
            "tier": "flexible",
            "meaning": "tracker pings",
            "plausibility": {"max_speed_mps": 30.0, "flag_straight_line": False},
        }
    )
    p = SpoofParams.from_plausibility(inp.plausibility)
    assert p.max_speed_mps == 30.0 and not p.flag_straight_line and p.flag_constant_speed
