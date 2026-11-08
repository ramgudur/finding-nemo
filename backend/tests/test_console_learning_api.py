"""api/trace.py: the authority-only backtest read and the lowered-trust list (rule 8)."""

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.core.clock import SimClock
from app.core.db import Db
from app.core.models import Backtest, BacktestDetail, Change
from app.engine.backtest import accept
from app.engine.tick import TickEngine
from app.main import DbEventSource, create_app

T0 = 1_800_000_000.0
APP = "test-app"


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _spec(app_id: str = APP) -> dict:
    return {
        "app_id": app_id,
        "intent": "find where entities gather",
        "grid": {"cell_m": 10, "bbox": {"min_lat": 0.0, "min_lon": 0.0, "max_lat": 0.01, "max_lon": 0.01}},
        "inputs": [
            {
                "id": "pings",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "a ping means an entity is here",
                "operators": [{"op": "density", "weight": 1.0}],
            }
        ],
        "outputs": {
            "layers": [{"id": "score", "visibility": "public"}, {"id": "pings.density", "visibility": "public"}]
        },
    }


def _bt(bid: str, **kw) -> Backtest:
    base = dict(
        id=bid,
        kind="batch",
        candidates_tested=256,
        candidates_valid=200,
        train_scores={"predictive_check": 0.5},
        holdout_scores={"predictive_check": 0.53},
        passed=True,
        device="cpu",
        elapsed_ms=12.0,
        t=datetime.fromtimestamp(T0, UTC),
    )
    base.update(kw)
    return Backtest(**base)


@pytest.fixture
def setup():
    db = Db(":memory:")
    clock = SimClock(start=T0)
    engine = TickEngine(DbEventSource(db), clock=clock)
    app_obj = create_app(db=db, engine=engine, autostart_engine=False)
    with TestClient(app_obj) as client:
        for a in (APP, "other-app"):
            assert client.post("/v1/apps", json=_spec(a)).status_code == 201
        keys = client.post(f"/v1/apps/{APP}/activate").json()
        client.post("/v1/apps/other-app/activate")
        ing = {"X-Ingest-Key": keys["ingest_key"]}
        for i in range(5):
            for k in range(2):  # the second event is each user's latest position
                lat = 0.002 + 0.001 * i + 0.0005 * k
                body = {"user_id": f"u{i}", "lat": lat, "lon": 0.005, "ts": _iso(T0 + k)}
                assert client.post(f"/v1/apps/{APP}/inputs/pings/events", json=body, headers=ing).status_code == 202
        # u1 and u3 lowered (a recorded Change each, as set_user_trust writes); u4 raised back to 1.0
        for uid, after in (("u1", 0.4), ("u3", 0.7), ("u4", 1.0)):
            db.set_trust(APP, uid, after, T0 + 5)
            db.add_change(
                Change(
                    id=f"c-{uid}",
                    app_id=APP,
                    kind="trust",
                    target=uid,
                    before={"trust": 1.0},
                    after={"trust": after},
                    reason=f"duplicate coordinates ({uid})",
                    actor="agent",
                    t=datetime.fromtimestamp(T0 + 5, UTC),
                )
            )
        db.set_trust("other-app", "x9", 0.1, T0)
        db.add_backtest(
            APP, _bt("bt-new", baseline_holdout_scores={"predictive_check": 0.5}, deciding_method="predictive_check")
        )
        db.add_backtest(APP, _bt("bt-old"))  # stored before the baseline was recorded
        db.add_backtest("other-app", _bt("bt-other"))
        engine.tick_once()
        yield client, keys, {"X-Authority-Key": keys["authority_key"]}


ROUTES = ["backtests/bt-new", "trust/lowered"]


@pytest.mark.parametrize("route", ROUTES)
def test_authority_only(setup, route):
    client, _, auth = setup
    assert client.get(f"/v1/apps/{APP}/{route}").status_code == 401
    assert client.get(f"/v1/apps/{APP}/{route}", headers={"X-Authority-Key": "wrong"}).status_code == 401
    assert client.get(f"/v1/apps/{APP}/{route}", headers={"X-Authority-Key": ""}).status_code == 401
    assert client.get(f"/v1/apps/nope/{route}", headers=auth).status_code == 404
    assert client.get(f"/v1/apps/{APP}/{route}", headers=auth).status_code == 200


def test_backtest_reports_the_holdout_delta_of_its_deciding_method(setup):
    client, _, auth = setup
    bt = client.get(f"/v1/apps/{APP}/backtests/bt-new", headers=auth).json()
    assert bt["deciding_method"] == "predictive_check" and bt["candidates_tested"] == 256
    assert bt["holdout_baseline"] == pytest.approx(0.5) and bt["holdout_candidate"] == pytest.approx(0.53)
    assert bt["holdout_delta"] == pytest.approx(0.03)


def test_backtest_without_a_recorded_baseline_has_no_delta(setup):
    client, _, auth = setup
    bt = client.get(f"/v1/apps/{APP}/backtests/bt-old", headers=auth).json()
    assert bt["deciding_method"] is None and bt["holdout_delta"] is None and bt["holdout_baseline"] is None
    assert bt["holdout_scores"] == {"predictive_check": 0.53}


def test_backtest_of_another_app_or_unknown_is_404(setup):
    client, _, auth = setup
    assert client.get(f"/v1/apps/{APP}/backtests/bt-other", headers=auth).status_code == 404
    assert client.get(f"/v1/apps/{APP}/backtests/missing", headers=auth).status_code == 404


def test_detail_delta_matches_the_acceptance_rule():
    """The derived delta is the comparison `accept` made: positive exactly when the decider improved."""
    base, cand = {"predictive_check": 0.41}, {"predictive_check": 0.47}
    ok, m, _ = accept(base, cand, ["authority_confirmation", "predictive_check"], 0.02)
    d = BacktestDetail.from_backtest(_bt("x", holdout_scores=cand, baseline_holdout_scores=base, deciding_method=m))
    assert ok and m == "predictive_check" and d.holdout_delta == pytest.approx(0.06)


def test_lowered_trust_lists_only_users_below_default_with_latest_position(setup):
    client, _, auth = setup
    rows = client.get(f"/v1/apps/{APP}/trust/lowered", headers=auth).json()
    assert [r["user_id"] for r in rows] == ["u1", "u3"]  # lowest first; u4 is back at 1.0; other-app absent
    u1 = rows[0]
    assert u1["trust"] == pytest.approx(0.4) and u1["input_id"] == "pings"
    assert u1["reason"] == "duplicate coordinates (u1)"
    assert u1["last_lat"] == pytest.approx(0.0035) and u1["last_lon"] == pytest.approx(0.005)
    assert datetime.fromisoformat(u1["last_seen"].replace("Z", "+00:00")).timestamp() == pytest.approx(T0 + 1)
    assert datetime.fromisoformat(u1["lowered_at"].replace("Z", "+00:00")).timestamp() == pytest.approx(T0 + 5)


def test_lowered_trust_is_capped(setup):
    client, _, auth = setup
    db = client.app.state.db
    for i in range(230):
        db.set_trust(APP, f"bulk{i}", 0.5, T0)
    rows = client.get(f"/v1/apps/{APP}/trust/lowered", headers=auth).json()
    assert len(rows) == 200 and rows[0]["user_id"] == "u1"
    assert all(r["last_lat"] is None for r in rows if r["user_id"].startswith("bulk"))


def test_distrusted_users_never_reach_public_rest_or_ws(setup):
    """Rule 8: the lowered-trust users and their positions appear on no keyless output."""
    client, _, _ = setup
    public = [
        client.get(f"/v1/apps/{APP}/map?format=geojson"),
        client.get(f"/v1/apps/{APP}/hotspots"),
        client.get(f"/v1/apps/{APP}/pois"),
    ]
    with client.websocket_connect(f"/v1/apps/{APP}/stream") as ws:
        frame = ws.receive_json()
    assert frame["type"] == "tick"
    texts = [r.text for r in public if r.status_code == 200] + [json.dumps(frame)]
    assert len(texts) >= 2
    for text in texts:
        assert '"u1"' not in text and '"u3"' not in text
        assert "duplicate coordinates" not in text and "last_lat" not in text and "trust" not in text
