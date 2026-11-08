import json

import numpy as np
import pytest

from app.core.clock import SimClock
from app.core.db import Db
from app.core.models import AppSpec
from app.engine.tick import InMemoryEventSource, TickEngine
from app.tools import registry
from app.tools.context import CandidateStore, ToolContext

T0 = 1_000_000.0
BBOX = {"min_lat": 45.0, "min_lon": 10.0, "max_lat": 45.0045, "max_lon": 10.00635}


def _input(id_, tier, weight, shape="point"):
    return {
        "id": id_,
        "kind": "push",
        "shape": shape,
        "tier": tier,
        "meaning": "a report",
        "operators": [{"op": "density", "weight": weight, "half_life_s": 60, "sigma_m": 15}],
    }


@pytest.fixture
def env():
    spec = AppSpec.model_validate(
        {
            "app_id": "read-app",
            "intent": "test",
            "status": "active",
            "grid": {"cell_m": 10, "bbox": BBOX},
            "inputs": [_input("crowd_a", "flexible", 0.5), _input("crowd_b", "flexible", 0.5)],
            "verification": [
                {"method": "predictive_check", "priority": 1},
                {"method": "cross_input_agreement", "priority": 2},
            ],
        }
    )
    clock = SimClock(T0)
    db = Db(":memory:")
    src = InMemoryEventSource()
    eng = TickEngine(src, clock=clock)
    eng.add_app(spec)
    grid = eng.apps["read-app"].grid

    def ctx(agent="critic"):
        return ToolContext("read-app", db, eng, agent=agent, clock=clock, candidates=CandidateStore())

    return clock, db, eng, src, grid, ctx


def feed_and_tick(clock, eng, src, grid, n_ticks=30, seed=0):
    """Two inputs reporting around the same spot, one tick every 2 s."""
    rng = np.random.default_rng(seed)
    for k in range(n_ticks):
        t = clock.now()
        for inp in ("crowd_a", "crowd_b"):
            lat, lon = grid.to_latlon(rng.normal(200, 12, 3), rng.normal(250, 12, 3))
            src.add(
                "read-app",
                inp,
                [
                    {"user_id": f"{inp}-{k}-{i}", "t": t - 0.5, "lat": float(a), "lon": float(o)}
                    for i, (a, o) in enumerate(zip(lat, lon, strict=True))
                ],
            )
        eng.tick_once()
        clock.advance(2.0)


def call(ctx, name, **args):
    return registry.call_tool(ctx, name, args)


def _max_list_len(obj) -> int:
    if isinstance(obj, dict):
        return max((_max_list_len(v) for v in obj.values()), default=0)
    if isinstance(obj, list):
        return max([len(obj), *(_max_list_len(v) for v in obj)])
    return 0


def test_read_tools_need_a_tick_first(env):
    *_, ctx = env
    r = call(ctx(), "get_map_summary")
    assert not r["ok"] and "no tick" in r["error"]


def test_map_summary_is_compact(env):
    clock, _, eng, src, grid, ctx = env
    feed_and_tick(clock, eng, src, grid)
    r = call(ctx(), "get_map_summary")
    assert r["ok"], r
    res = r["result"]
    assert res["weights"]["crowd_a.density"] == {"weight": 0.5, "tier": "flexible", "pinned": False}
    assert res["flexible_total"] == 1.0
    assert len(res["hotspots"]) >= 1 and res["score"]["max"] > 0.5
    assert len(json.dumps(res)) < 4000 and _max_list_len(res) <= 20  # summaries, not arrays


def test_layer_stats_all_and_one(env):
    clock, _, eng, src, grid, ctx = env
    feed_and_tick(clock, eng, src, grid, n_ticks=5)
    all_ = call(ctx(), "get_layer_stats")["result"]["layers"]
    assert set(all_) == {"crowd_a.density", "crowd_b.density"}
    one = call(ctx(), "get_layer_stats", layer_id="crowd_a.density")["result"]["layers"]
    assert list(one) == ["crowd_a.density"] and one["crowd_a.density"]["max"] > 0
    assert one["crowd_a.density"]["peak"] is not None
    assert "unknown layer" in call(ctx(), "get_layer_stats", layer_id="nope")["error"]


def test_verification_report_with_two_inputs(env):
    clock, _, eng, src, grid, ctx = env
    feed_and_tick(clock, eng, src, grid, n_ticks=60)
    r = call(ctx(), "get_verification_report", window_s=120)
    assert r["ok"], r
    res = r["result"]
    assert list(res["methods"]) == ["predictive_check", "cross_input_agreement"]  # spec priority order
    assert res["methods"]["cross_input_agreement"]["has_data"]
    assert res["cold_start"] == (len(res["methods_with_data"]) < 2)
    assert call(ctx(), "get_verification_report", window_s=10_000)["ok"] is False  # > 600 s rejected


def test_suspicious_users_flags_duplicates_without_coordinates(env):
    clock, db, eng, src, grid, ctx = env
    feed_and_tick(clock, eng, src, grid, n_ticks=10)
    lat, lon = grid.to_latlon(300.0, 300.0)
    t = clock.now()
    src.add(
        "read-app",
        "crowd_b",
        [{"user_id": f"fake{i}", "t": t - 1, "lat": float(lat), "lon": float(lon)} for i in range(4)],
    )
    r = call(ctx(), "get_suspicious_users", window_s=300)
    assert r["ok"], r
    res = r["result"]
    flagged = {u["user_id"] for u in res["top"]}
    assert {f"fake{i}" for i in range(4)} <= flagged
    assert not any(u.startswith("crowd_") for u in flagged)  # real, nearby users are not flagged
    assert all(u["trust"] == 1.0 for u in res["top"])
    text = json.dumps(res)
    assert '"lat"' not in text and '"lon"' not in text


def test_annotate_writes_trace_only(env):
    _, db, *_, ctx = env
    assert call(ctx(), "annotate", text="crowd_b looks noisy; watching")["ok"]
    trace = db.list_trace("read-app")
    assert len(trace) == 1 and trace[0].agent == "critic" and trace[0].action == "annotate"
    assert not call(ctx(), "annotate", text="")["ok"]


def test_critic_sees_read_and_write_tools():
    names = {t["function"]["name"] for t in registry.tool_schemas("critic")}
    assert names == {
        "get_map_summary",
        "get_layer_stats",
        "get_verification_report",
        "get_suspicious_users",
        "get_corroboration",
        "annotate",
        "run_backtest",
        "run_backtest_batch",
        "apply_changes",
        "set_user_trust",
    }
    assert {t["function"]["name"] for t in registry.tool_schemas("poi_labeler")} == {
        "annotate",
        "get_poi_candidates",
        "find_candidates",
        "osm_features_near",
        "input_values_at",
        "get_layer_stats_at",
        "get_time_pattern",
        "create_poi",
        "update_poi",
        "retire_poi",
        "propose_label",
    }
