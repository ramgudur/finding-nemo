from datetime import UTC, datetime

import numpy as np
import pytest

from app.core.clock import SimClock
from app.core.db import Db
from app.core.models import AppSpec, Backtest
from app.engine import backtest as bt_mod
from app.engine.tick import InMemoryEventSource, TickEngine
from app.tools import registry
from app.tools.context import CandidateStore, ToolContext
from app.tools.guards import BACKTEST_MAX_AGE_S

T0 = 1_000_000.0
BBOX = {"min_lat": 45.0, "min_lon": 10.0, "max_lat": 45.0045, "max_lon": 10.00635}
ALL = ("authority_confirmation", "predictive_check", "cross_input_agreement")


def _input(id_, tier, weight):
    return {
        "id": id_,
        "kind": "push",
        "shape": "point",
        "tier": tier,
        "meaning": "a report",
        "operators": [{"op": "density", "weight": weight, "half_life_s": 60, "sigma_m": 15}],
    }


def make_spec() -> AppSpec:
    return AppSpec.model_validate(
        {
            "app_id": "tools-app",
            "intent": "test",
            "status": "active",
            "grid": {"cell_m": 10, "bbox": BBOX},
            "inputs": [
                _input("crowd_a", "flexible", 0.5),
                _input("crowd_b", "flexible", 0.5),
                _input("auth", "locked", 1.0),
                _input("news", "stiff", 0.5),
            ],
            "verification": [{"method": m, "priority": i + 1} for i, m in enumerate(ALL)],
        }
    )


@pytest.fixture
def env():
    clock = SimClock(T0)
    db = Db(":memory:")
    src = InMemoryEventSource()
    eng = TickEngine(src, clock=clock)
    eng.add_app(make_spec())
    store = CandidateStore()

    def ctx(actor="agent", agent="critic"):
        return ToolContext("tools-app", db, eng, actor=actor, agent=agent, clock=clock, candidates=store)

    return clock, db, eng, src, ctx


@pytest.fixture
def scripted(monkeypatch):
    """Replace the real backtest with a scripted outcome: (passed, methods with data)."""
    state = {"passed": True, "methods": ALL}

    def fake(spec, events, *, now, weights, trust=None, candidate_weights=None, candidate_trust=None, **kw):
        scores = {m: 0.5 for m in state["methods"]}
        bt = Backtest(
            id=f"bt{np.random.default_rng().integers(1 << 40)}",
            kind="single",
            candidates_tested=1,
            candidates_valid=1,
            train_scores=scores,
            holdout_scores=scores,
            passed=state["passed"],
            device="cpu",
            elapsed_ms=1.0,
            t=datetime.fromtimestamp(now, UTC),
        )
        return bt_mod.BacktestOutcome(
            bt, "scripted", baseline_train=scores, baseline_holdout=scores, deciding_method=state["methods"][0]
        )

    monkeypatch.setattr("app.tools.scoring_tools.bt_mod.run_backtest", fake)
    return state


def call(ctx, name, **args):
    return registry.call_tool(ctx, name, args)


def test_schemas_per_agent():
    critic = {t["function"]["name"] for t in registry.tool_schemas("critic")}
    assert {"run_backtest", "apply_changes", "set_user_trust"} <= critic
    assert "pin_weight" not in critic
    assert all(t["function"]["parameters"]["type"] == "object" for t in registry.tool_schemas("critic"))
    assert {t["function"]["name"] for t in registry.tool_schemas("scout")} == {"annotate"}


def test_weight_change_flow_holds_flexible_sum_and_writes_changes(env, scripted):
    clock, db, eng, _, ctx = env
    r = call(ctx(), "run_backtest", weights={"crowd_a.density": 0.6}, reason="crowd_a predicts better")
    assert r["ok"], r
    tested = r["result"]["tested_weights"]
    assert set(tested) == {"crowd_a.density", "crowd_b.density"}  # renormalized
    clock.advance(10)
    a = call(ctx(), "apply_changes", backtest_id=r["result"]["backtest_id"], reason="holdout improved")
    assert a["ok"], a
    w = eng.apps["tools-app"].weights
    assert w["crowd_a.density"] + w["crowd_b.density"] == pytest.approx(1.0)
    assert w["auth.density"] == 1.0 and w["news.density"] == 0.5
    changes = db.list_changes("tools-app", kind="weight")
    assert {c.target for c in changes} == {"crowd_a.density", "crowd_b.density"}
    assert all(c.backtest_id == r["result"]["backtest_id"] and c.actor == "agent" for c in changes)
    assert db.current_weight("tools-app", "crowd_a.density").weight == pytest.approx(w["crowd_a.density"])
    # single use
    again = call(ctx(), "apply_changes", backtest_id=r["result"]["backtest_id"], reason="again")
    assert not again["ok"] and "already applied" in again["error"]


def test_apply_rejects_failed_holdout(env, scripted):
    _, _, eng, _, ctx = env
    scripted["passed"] = False
    r = call(ctx(), "run_backtest", weights={"crowd_a.density": 0.6}, reason="try")
    assert r["ok"] and not r["result"]["passed"]
    a = call(ctx(), "apply_changes", backtest_id=r["result"]["backtest_id"], reason="apply anyway")
    assert not a["ok"] and "did not pass on the holdout" in a["error"]
    assert eng.apps["tools-app"].weights["crowd_a.density"] == 0.5


def test_apply_rejects_unknown_stale_and_drifted(env, scripted):
    clock, _, eng, _, ctx = env
    assert "unknown backtest" in call(ctx(), "apply_changes", backtest_id="nope", reason="x")["error"]
    r = call(ctx(), "run_backtest", weights={"crowd_a.density": 0.6}, reason="try")
    clock.advance(BACKTEST_MAX_AGE_S + 1)
    assert "rerun it" in call(ctx(), "apply_changes", backtest_id=r["result"]["backtest_id"], reason="x")["error"]
    r = call(ctx(), "run_backtest", weights={"crowd_a.density": 0.6}, reason="try")
    eng.apps["tools-app"].set_weights({"crowd_b.density": 0.45})  # something else moved the weights
    a = call(ctx(), "apply_changes", backtest_id=r["result"]["backtest_id"], reason="x")
    assert "weights changed" in a["error"]


def test_cold_start_rejects_with_reason(env, scripted):
    _, _, _, _, ctx = env
    scripted["methods"] = ("predictive_check",)
    r = call(ctx(), "run_backtest", weights={"crowd_a.density": 0.6}, reason="try")
    assert not r["ok"] and "cold start" in r["error"] and "have 1" in r["error"]


def test_locked_and_out_of_bounds_rejected_before_backtesting(env, scripted):
    _, db, _, _, ctx = env
    r = call(ctx(), "run_backtest", weights={"auth.density": 0.5}, reason="auth too loud")
    assert not r["ok"] and "locked" in r["error"]
    r = call(ctx(), "run_backtest", weights={"crowd_a.density": 2.0}, reason="go big")
    assert not r["ok"] and "out of bounds" in r["error"]
    assert db.list_backtests("tools-app") == []


def test_stiff_needs_authority_evidence(env, scripted):
    _, _, _, _, ctx = env
    scripted["methods"] = ("predictive_check", "cross_input_agreement")
    r = call(ctx(), "run_backtest", weights={"news.density": 0.52}, reason="news helps")
    assert not r["ok"] and "authority-confirmation" in r["error"]
    scripted["methods"] = ALL
    assert call(ctx(), "run_backtest", weights={"news.density": 0.52}, reason="news helps")["ok"]


def test_dev_pin_freezes_weight_and_agents_cant_pin(env, scripted):
    clock, db, _, _, ctx = env
    denied = call(ctx(), "pin_weight", layer_id="crowd_a.density", reason="x")
    assert not denied["ok"] and "not available" in denied["error"]
    assert call(ctx(actor="dev", agent=None), "pin_weight", layer_id="crowd_a.density", reason="dev likes it")["ok"]
    r = call(ctx(), "run_backtest", weights={"crowd_a.density": 0.6}, reason="try")
    assert not r["ok"] and "pinned" in r["error"]
    # pinned layer also doesn't move when another flexible layer changes: nothing left to renormalize into
    r = call(ctx(), "run_backtest", weights={"crowd_b.density": 0.55}, reason="try")
    assert not r["ok"]
    clock.advance(1)
    call(ctx(actor="dev", agent=None), "pin_weight", layer_id="crowd_a.density", pinned=False, reason="unpin")
    assert call(ctx(), "run_backtest", weights={"crowd_a.density": 0.6}, reason="try")["ok"]
    pins = [c for c in db.list_changes("tools-app", kind="weight") if c.actor == "dev"]
    assert [c.after["pinned"] for c in pins] == [True, False]


def test_trust_lowering_free_increase_capped(env, scripted):
    clock, db, _, _, ctx = env
    assert call(ctx(), "set_user_trust", user_id="u1", trust=0.2, reason="duplicate coords")["ok"]
    r = call(ctx(), "set_user_trust", user_id="u1", trust=0.5, reason="seems fine")
    assert not r["ok"] and "exceeds +0.1" in r["error"]
    assert call(ctx(), "set_user_trust", user_id="u1", trust=0.3, reason="recovering")["ok"]
    clock.advance(60)
    r = call(ctx(), "set_user_trust", user_id="u1", trust=0.35, reason="more")
    assert not r["ok"] and "next increase in" in r["error"]
    clock.advance(241)
    assert call(ctx(), "set_user_trust", user_id="u1", trust=0.4, reason="more")["ok"]
    assert call(ctx(), "set_user_trust", user_id="u1", trust=0.0, reason="spoof burst")["ok"]  # lowering anytime
    assert not call(ctx(), "set_user_trust", user_id="u1", trust=0.1, reason="")["ok"]  # reason required
    assert [c.after["trust"] for c in db.list_changes("tools-app", kind="trust")] == [0.2, 0.3, 0.4, 0.0]


def test_trust_via_backtest_applies_with_cap(env, scripted):
    clock, db, _, _, ctx = env
    call(ctx(), "set_user_trust", user_id="u2", trust=0.5, reason="suspicious")
    clock.advance(400)
    assert not call(ctx(), "run_backtest", trust={"u2": 0.9}, reason="x")["ok"]  # over the cap: fails fast
    r = call(ctx(), "run_backtest", trust={"u2": 0.6}, reason="u2 agrees with authority")
    assert r["ok"] and r["result"]["tested_trust"] == {"u2": 0.6}
    a = call(ctx(), "apply_changes", backtest_id=r["result"]["backtest_id"], reason="holdout improved")
    assert a["ok"], a
    assert db.get_trust("tools-app", "u2").score == 0.6


def test_bad_args_and_unknown_tool(env):
    _, _, _, _, ctx = env
    assert "unknown tool" in call(ctx(), "rm_rf")["error"]
    r = call(ctx(), "set_user_trust", user_id="u", trust=2.0, reason="x")
    assert not r["ok"] and "invalid arguments" in r["error"]
    r = call(ctx(), "apply_changes", backtest_id="x", reason="y", sneaky=1)
    assert not r["ok"] and "invalid arguments" in r["error"]


def test_real_backtest_plumbing(env):
    """No scripting: the real backtest runs on live engine events and is stored."""
    clock, db, eng, src, ctx = env
    rng = np.random.default_rng(0)
    grid = eng.apps["tools-app"].grid
    recs = []
    for k in range(120):
        x, y = rng.normal(200, 15), rng.normal(250, 15)
        lat, lon = grid.to_latlon(x, y)
        recs.append({"user_id": f"u{k % 30}", "t": T0 - 600 + 5 * k, "lat": float(lat), "lon": float(lon)})
    src.add("tools-app", "crowd_a", recs[::2])
    src.add("tools-app", "crowd_b", recs[1::2])
    r = call(ctx(), "run_backtest", trust={"u1": 0.0}, reason="u1 looks spoofed")
    assert r["ok"], r
    assert db.get_backtest("tools-app", r["result"]["backtest_id"]) is not None
    assert isinstance(r["result"]["passed"], bool) and r["result"]["reason"]
