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
    # authority_confirmation has data but another method decided: backtest runs, apply is refused
    scripted["methods"] = ("predictive_check", "authority_confirmation", "cross_input_agreement")
    r = call(ctx(), "run_backtest", weights={"news.density": 0.52}, reason="news helps")
    assert r["ok"] and r["result"]["passed"] and "not authority_confirmation" in r["result"]["note"]
    a = call(ctx(), "apply_changes", backtest_id=r["result"]["backtest_id"], reason="x")
    assert not a["ok"] and "authority-confirmation" in a["error"]
    # authority_confirmation decided: applies
    scripted["methods"] = ALL
    r = call(ctx(), "run_backtest", weights={"news.density": 0.52}, reason="news helps")
    assert call(ctx(), "apply_changes", backtest_id=r["result"]["backtest_id"], reason="authority agreed")["ok"]


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


def _raise_trust(ctx, user_id, to):
    r = call(ctx(), "run_backtest", trust={user_id: to}, reason="agrees with authority")
    if not r["ok"]:
        return r
    return call(ctx(), "apply_changes", backtest_id=r["result"]["backtest_id"], reason="holdout improved")


def test_set_user_trust_only_lowers(env, scripted):
    _, db, _, _, ctx = env
    assert call(ctx(), "set_user_trust", user_id="u1", trust=0.2, reason="duplicate coords")["ok"]
    r = call(ctx(), "set_user_trust", user_id="u1", trust=0.25, reason="seems fine")
    assert not r["ok"] and "only lowers" in r["error"] and "run_backtest" in r["error"]
    assert call(ctx(), "set_user_trust", user_id="u1", trust=0.0, reason="spoof burst")["ok"]
    assert not call(ctx(), "set_user_trust", user_id="u1", trust=0.0, reason="")["ok"]  # reason required
    assert [c.after["trust"] for c in db.list_changes("tools-app", kind="trust")] == [0.2, 0.0]


def test_trust_increases_via_backtest_are_capped(env, scripted):
    clock, db, _, _, ctx = env
    call(ctx(), "set_user_trust", user_id="u1", trust=0.2, reason="duplicate coords")
    r = _raise_trust(ctx, "u1", 0.5)
    assert not r["ok"] and "exceeds +0.1" in r["error"]
    assert _raise_trust(ctx, "u1", 0.3)["ok"]
    clock.advance(60)
    r = _raise_trust(ctx, "u1", 0.35)
    assert not r["ok"] and "next increase in" in r["error"]
    clock.advance(241)
    assert _raise_trust(ctx, "u1", 0.4)["ok"]
    assert [c.after["trust"] for c in db.list_changes("tools-app", kind="trust")] == [0.2, 0.3, 0.4]
    assert all(c.backtest_id for c in db.list_changes("tools-app", kind="trust")[1:])


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


def test_apply_rejects_trust_drift_for_weight_backtest(env, scripted):
    clock, db, eng, _, ctx = env
    live: dict[str, float] = {}
    eng.trust = lambda app_id: live  # stands in for main.py's db-backed trust provider
    r = call(ctx(), "run_backtest", weights={"crowd_a.density": 0.6}, reason="try")
    assert r["ok"]
    assert call(ctx(), "set_user_trust", user_id="u9", trust=0.1, reason="spoof")["ok"]
    live["u9"] = 0.1  # the tick now replays with different trust than the backtest did
    a = call(ctx(), "apply_changes", backtest_id=r["result"]["backtest_id"], reason="x")
    assert not a["ok"] and "trust changed" in a["error"] and "u9" in a["error"]
    assert eng.apps["tools-app"].weights["crowd_a.density"] == 0.5


def test_apply_rejects_trust_candidate_whose_start_moved(env, scripted):
    clock, db, _, _, ctx = env
    call(ctx(), "set_user_trust", user_id="u2", trust=0.5, reason="suspicious")
    clock.advance(400)
    r = call(ctx(), "run_backtest", trust={"u2": 0.6}, reason="recovering")
    assert r["ok"]
    call(ctx(), "set_user_trust", user_id="u2", trust=0.3, reason="new duplicate coords")
    a = call(ctx(), "apply_changes", backtest_id=r["result"]["backtest_id"], reason="x")
    assert not a["ok"] and "trust changed" in a["error"]
    assert db.get_trust("tools-app", "u2").score == 0.3


# -- run_backtest_batch (#52) --------------------------------------------------------


@pytest.fixture
def scripted_batch(monkeypatch, scripted):
    """Scripted batch outcome: the best passing candidate's weights, or none passing."""
    state = {"weights": None}

    def fake(spec, window, events, search_spec, *, now, bounds, grid, params=None, **kw):
        state["bounds"] = bounds
        cands = []
        if state["weights"] is not None:
            scores = {m: 0.6 for m in scripted["methods"]}
            cands = [bt_mod.BatchCandidate(dict(state["weights"]), scores, scores, True, "improved")]
        bt = Backtest(
            id=f"bb{np.random.default_rng().integers(1 << 40)}",
            kind="batch",
            candidates_tested=256,
            candidates_valid=200,
            train_scores={},
            holdout_scores={},
            passed=bool(cands),
            device="cpu",
            elapsed_ms=12.0,
            t=datetime.fromtimestamp(now, UTC),
        )
        return bt_mod.BatchOutcome(bt, "256 configs backtested in 12 ms on cpu (200 valid); scripted", cands)

    monkeypatch.setattr("app.tools.scoring_tools.bt_mod.run_backtest_batch", fake)
    return state


def test_batch_tool_is_critic_only():
    assert "run_backtest_batch" in {t["function"]["name"] for t in registry.tool_schemas("critic")}
    assert "run_backtest_batch" not in {t["function"]["name"] for t in registry.tool_schemas("scout")}


def test_batch_best_candidate_applies_through_guards(env, scripted, scripted_batch):
    clock, db, eng, _, ctx = env
    scripted_batch["weights"] = {
        "crowd_a.density": 0.6,
        "crowd_b.density": 0.4,
        "auth.density": 1.0,
        "news.density": 0.5,
    }
    r = call(ctx(), "run_backtest_batch", layers={"crowd_a.density": [0.4, 0.9]}, samples=256, reason="search")
    assert r["ok"], r
    res = r["result"]
    assert res["passed"] and res["reason"].startswith("256 configs backtested in")
    assert res["best_weights"] == {"crowd_a.density": 0.6, "crowd_b.density": 0.4}
    # bounds cover only weighted layers, in the engine's order
    assert set(scripted_batch["bounds"].layer_ids) == set(eng.apps["tools-app"].weighted_ids)
    assert db.get_backtest("tools-app", res["backtest_id"]).kind == "batch"
    clock.advance(5)
    a = call(ctx(), "apply_changes", backtest_id=res["backtest_id"], reason="best holdout")
    assert a["ok"], a
    w = eng.apps["tools-app"].weights
    assert (w["crowd_a.density"], w["crowd_b.density"]) == pytest.approx((0.6, 0.4))
    assert all(c.backtest_id == res["backtest_id"] for c in db.list_changes("tools-app", kind="weight"))


def test_batch_nothing_passing_cannot_be_applied(env, scripted, scripted_batch):
    _, _, _, _, ctx = env
    r = call(ctx(), "run_backtest_batch", layers={"crowd_a.density": [0.4, 0.9]}, reason="search")
    assert r["ok"] and not r["result"]["passed"] and r["result"]["best_weights"] == {}
    a = call(ctx(), "apply_changes", backtest_id=r["result"]["backtest_id"], reason="x")
    assert not a["ok"] and "unknown backtest" in a["error"]


def test_batch_rejects_override_or_unknown_layers(env, scripted, scripted_batch):
    _, _, _, _, ctx = env
    r = call(ctx(), "run_backtest_batch", layers={"nope.density": [0.1, 0.2]}, reason="x")
    assert not r["ok"] and "not weighted layers" in r["error"]
    r = call(ctx(), "run_backtest_batch", layers={"crowd_a.density": [0.1, 0.2]}, samples=5000, reason="x")
    assert not r["ok"] and "invalid arguments" in r["error"]


def test_batch_stiff_move_needs_authority_decided(env, scripted, scripted_batch):
    _, _, _, _, ctx = env
    scripted["methods"] = ("predictive_check", "authority_confirmation", "cross_input_agreement")
    scripted_batch["weights"] = {
        "crowd_a.density": 0.5,
        "crowd_b.density": 0.5,
        "auth.density": 1.0,
        "news.density": 0.52,
    }
    r = call(ctx(), "run_backtest_batch", layers={"news.density": [0.5, 0.55]}, reason="news")
    assert r["ok"] and r["result"]["passed"]
    a = call(ctx(), "apply_changes", backtest_id=r["result"]["backtest_id"], reason="x")
    assert not a["ok"] and "authority-confirmation" in a["error"]


def test_batch_real_plumbing(env):
    """No scripting: ticks fill the layer cache, the real batch search runs and is stored."""
    clock, db, eng, src, ctx = env
    rng = np.random.default_rng(0)
    rt = eng.apps["tools-app"]
    recs = []
    for k in range(240):
        lat, lon = rt.grid.to_latlon(rng.normal(200, 15), rng.normal(250, 15))
        recs.append({"user_id": f"u{k % 30}", "t": T0 - 600 + 2.5 * k, "lat": float(lat), "lon": float(lon)})
    src.add("tools-app", "crowd_a", recs[::2])
    src.add("tools-app", "crowd_b", recs[1::2])
    from app.engine.tick import tick_app

    for t in np.arange(T0 - 600, T0 + 1, 10.0):
        clock.set(float(t))
        rt.last = tick_app(rt, src, now=float(t), clock=clock)
    r = call(ctx(), "run_backtest_batch", layers={"crowd_a.density": [0.2, 0.8]}, samples=64, reason="search")
    assert r["ok"], r
    res = r["result"]
    assert res["candidates_tested"] == 64 and res["device"] in ("cpu", "gpu")
    assert "configs backtested in" in res["reason"]
    assert db.get_backtest("tools-app", res["backtest_id"]) is not None
