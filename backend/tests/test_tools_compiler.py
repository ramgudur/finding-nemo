import pytest

from app.core.db import Db
from app.core.models import AppSpec
from app.tools import registry
from app.tools.context import ToolContext

BBOX = {"min_lat": 0.0, "min_lon": 0.0, "max_lat": 0.01, "max_lon": 0.01}


def _draft(app_id: str = "draft-app") -> AppSpec:
    return AppSpec.model_validate(
        {"app_id": app_id, "intent": "find where people gather", "grid": {"cell_m": 10, "bbox": BBOX}}
    )


@pytest.fixture
def ctx():
    db = Db(":memory:")
    db.upsert_app(_draft(), t=1.0)
    return ToolContext(app_id="draft-app", db=db, engine=object(), agent="compiler")


def _call(ctx, name, args):
    return registry.call_tool(ctx, name, args)


def test_list_operators_returns_the_real_catalog(ctx):
    r = _call(ctx, "list_operators", {})
    assert r["ok"] is True
    names = {o["name"] for o in r["result"]["operators"]}
    assert "density" in names and "flow_source" in names


def test_only_compiler_may_call_these_tools(ctx):
    ctx.agent = "critic"
    r = _call(ctx, "list_operators", {})
    assert r["ok"] is False and "not available" in r["error"]


# -- validate_spec --------------------------------------------------------------------


def test_validate_spec_accepts_a_good_spec(ctx):
    spec = {
        **_draft().model_dump(mode="json"),
        "inputs": [
            {
                "id": "pings",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "a ping",
                "operators": [{"op": "density", "weight": 1.0}],
                "plausibility": {"max_speed_mps": 12},
            }
        ],
    }
    r = _call(ctx, "validate_spec", {"spec": spec})
    assert r["result"]["valid"] is True
    assert r["result"]["errors"] == []
    assert r["result"]["warnings"] == []


def test_validate_spec_warns_on_missing_plausibility_for_flexible_track_or_point(ctx):
    spec = {
        **_draft().model_dump(mode="json"),
        "inputs": [
            {
                "id": "pings",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "a ping",
                "operators": [{"op": "density", "weight": 1.0}],
            }
        ],
    }
    r = _call(ctx, "validate_spec", {"spec": spec})
    assert r["result"]["valid"] is True
    assert "plausibility" in r["result"]["warnings"][0]


def test_validate_spec_rejects_an_unknown_operator(ctx):
    spec = {
        **_draft().model_dump(mode="json"),
        "inputs": [
            {
                "id": "pings",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "a ping",
                "operators": [{"op": "nonexistent_op", "weight": 1.0}],
            }
        ],
    }
    r = _call(ctx, "validate_spec", {"spec": spec})
    assert r["result"]["valid"] is False
    assert "unknown operator" in r["result"]["errors"][0]


def test_validate_spec_rejects_operator_shape_mismatch(ctx):
    spec = {
        **_draft().model_dump(mode="json"),
        "inputs": [
            {
                "id": "pings",
                "kind": "push",
                "shape": "point",  # flow_source needs "track"
                "tier": "flexible",
                "meaning": "a ping",
                "operators": [{"op": "flow_source", "weight": 1.0}],
            }
        ],
    }
    r = _call(ctx, "validate_spec", {"spec": spec})
    assert r["result"]["valid"] is False
    assert "doesn't accept shape" in r["result"]["errors"][0]


def test_validate_spec_rejects_a_typo_in_operator_params(ctx):
    spec = {
        **_draft().model_dump(mode="json"),
        "inputs": [
            {
                "id": "pings",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "a ping",
                "operators": [{"op": "density", "weight": 1.0, "half_lfie_s": 60}],  # typo: half_life_s
            }
        ],
    }
    r = _call(ctx, "validate_spec", {"spec": spec})
    assert r["result"]["valid"] is False
    assert "pings" in r["result"]["errors"][0] and "params invalid" in r["result"]["errors"][0]


def test_validate_spec_rejects_public_score_with_a_private_weighted_layer(ctx):
    spec = {
        **_draft().model_dump(mode="json"),
        "inputs": [
            {
                "id": "pings",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "a ping",
                "operators": [{"op": "density", "weight": 1.0}],
            }
        ],
        "outputs": {"layers": [{"id": "score", "visibility": "public"}]},  # pings.density stays authority
    }
    r = _call(ctx, "validate_spec", {"spec": spec})
    assert r["result"]["valid"] is False
    assert "pings.density" in r["result"]["errors"][0]


def test_validate_spec_accepts_public_score_when_every_weighted_layer_is_public(ctx):
    spec = {
        **_draft().model_dump(mode="json"),
        "inputs": [
            {
                "id": "pings",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "a ping",
                "operators": [{"op": "density", "weight": 1.0}],
                "plausibility": {"max_speed_mps": 12},
            }
        ],
        "outputs": {
            "layers": [
                {"id": "score", "visibility": "public"},
                {"id": "pings.density", "visibility": "public"},
            ]
        },
    }
    r = _call(ctx, "validate_spec", {"spec": spec})
    assert r["result"]["valid"] is True


def test_validate_spec_public_score_ignores_override_layers(ctx):
    # an override layer never enters the weighted sum, so it shouldn't block a public score
    spec = {
        **_draft().model_dump(mode="json"),
        "inputs": [
            {
                "id": "pings",
                "kind": "push",
                "shape": "point",
                "tier": "flexible",
                "meaning": "a ping",
                "operators": [{"op": "density", "weight": 1.0}],
                "plausibility": {"max_speed_mps": 12},
            },
            {
                "id": "marker",
                "kind": "push",
                "shape": "fixed_point",
                "tier": "locked",
                "meaning": "an authority marker",
                "operators": [{"op": "override", "weight": 1.0, "radius_m": 10}],
            },
        ],
        "outputs": {
            "layers": [
                {"id": "score", "visibility": "public"},
                {"id": "pings.density", "visibility": "public"},
            ]
        },
    }
    r = _call(ctx, "validate_spec", {"spec": spec})
    assert r["result"]["valid"] is True


def test_validate_spec_rejects_proposing_active_status(ctx):
    spec = {**_draft().model_dump(mode="json"), "status": "active"}
    r = _call(ctx, "validate_spec", {"spec": spec})
    assert r["result"]["valid"] is False
    assert "active" in r["result"]["errors"][0]


def test_validate_spec_reports_pydantic_errors_without_raising(ctx):
    r = _call(ctx, "validate_spec", {"spec": {"app_id": "x"}})  # missing required fields
    assert r["ok"] is True  # the tool call itself succeeds; invalidity is in the payload
    assert r["result"]["valid"] is False
    assert r["result"]["errors"]


# -- create_input / create_connector / test_connector / request_scout -----------------


def test_create_input_adds_it_to_the_draft(ctx):
    r = _call(
        ctx,
        "create_input",
        {
            "id": "pings",
            "kind": "push",
            "shape": "point",
            "tier": "flexible",
            "meaning": "a ping means someone is here",
            "operators": [{"op": "density", "weight": 1.0}],
        },
    )
    assert r["ok"] is True
    spec = ctx.db.get_app("draft-app")
    assert spec.inputs[0].id == "pings"
    assert spec.inputs[0].operators[0].layer_id == "pings.density"
    assert "plausibility" in r["result"]["warnings"][0]


def test_create_input_rejects_a_duplicate_id(ctx):
    args = {
        "id": "pings",
        "kind": "push",
        "shape": "point",
        "tier": "flexible",
        "meaning": "a ping",
        "operators": [{"op": "density", "weight": 1.0}],
    }
    _call(ctx, "create_input", args)
    r = _call(ctx, "create_input", args)
    assert r["ok"] is False and "already exists" in r["error"]


def test_create_input_rejects_unknown_operator(ctx):
    r = _call(
        ctx,
        "create_input",
        {
            "id": "pings",
            "kind": "push",
            "shape": "point",
            "tier": "flexible",
            "meaning": "a ping",
            "operators": [{"op": "nope", "weight": 1.0}],
        },
    )
    assert r["ok"] is False
    assert ctx.db.get_app("draft-app").inputs == []  # rejected, never saved


def test_create_connector_attaches_config_to_a_pull_input(ctx):
    _call(
        ctx,
        "create_input",
        {"id": "weather", "kind": "pull", "shape": "field", "tier": "stiff", "meaning": "wind"},
    )
    r = _call(ctx, "create_connector", {"input_id": "weather", "type": "json_api", "preset": "open_meteo"})
    assert r["ok"] is True
    conn = ctx.db.get_app("draft-app").inputs[0].connector
    assert conn.type == "json_api" and conn.preset == "open_meteo"


def test_create_connector_rejects_a_push_input(ctx):
    _call(
        ctx,
        "create_input",
        {"id": "pings", "kind": "push", "shape": "point", "tier": "flexible", "meaning": "a ping"},
    )
    r = _call(ctx, "create_connector", {"input_id": "pings", "type": "rss"})
    assert r["ok"] is False and "only a pull input" in r["error"]


def test_create_connector_rejects_an_unknown_input(ctx):
    r = _call(ctx, "create_connector", {"input_id": "nope", "type": "rss"})
    assert r["ok"] is False and "does not exist" in r["error"]


def test_test_connector_validates_shape_without_a_live_fetch(ctx):
    _call(
        ctx,
        "create_input",
        {"id": "weather", "kind": "pull", "shape": "field", "tier": "stiff", "meaning": "wind"},
    )
    created = _call(ctx, "create_connector", {"input_id": "weather", "type": "json_api", "preset": "open_meteo"})
    r = _call(ctx, "test_connector", {"connector_id": created["result"]["connector_id"]})
    assert r["ok"] is True and r["result"]["config_valid"] is True


def test_create_connector_writes_a_change(ctx):
    _call(
        ctx,
        "create_input",
        {"id": "weather", "kind": "pull", "shape": "field", "tier": "stiff", "meaning": "wind"},
    )
    _call(ctx, "create_connector", {"input_id": "weather", "type": "json_api", "preset": "open_meteo"})
    changes = ctx.db.list_changes("draft-app", kind="connector")
    assert len(changes) == 1
    assert changes[0].target == "weather" and changes[0].before is None
    assert changes[0].after["preset"] == "open_meteo"


def test_test_connector_rejects_an_input_with_no_connector_yet(ctx):
    _call(
        ctx,
        "create_input",
        {"id": "weather", "kind": "pull", "shape": "field", "tier": "stiff", "meaning": "wind"},
    )
    r = _call(ctx, "test_connector", {"connector_id": "weather"})
    assert r["ok"] is False and "no connector" in r["error"]


def test_request_scout_writes_a_trace_entry(ctx):
    _call(
        ctx,
        "create_input",
        {"id": "weather", "kind": "pull", "shape": "field", "tier": "stiff", "meaning": "wind"},
    )
    r = _call(ctx, "request_scout", {"input_id": "weather", "instructions": "find a wind feed"})
    assert r["ok"] is True
    traces = ctx.db.list_trace("draft-app")
    assert traces[-1].agent == "compiler" and traces[-1].action == "request_scout"
    assert traces[-1].why == "find a wind feed"


# -- finalize_app (one-shot: takes the complete proposed spec) -------------------------


def _good_spec_dict(app_id: str = "draft-app") -> dict:
    spec = _draft(app_id).model_dump(mode="json")
    spec["inputs"] = [
        {
            "id": "pings",
            "kind": "push",
            "shape": "point",
            "tier": "flexible",
            "meaning": "a ping",
            "operators": [{"op": "density", "weight": 1.0}],
        }
    ]
    return spec


def test_finalize_app_moves_to_pending_confirmation(ctx):
    r = _call(ctx, "finalize_app", {"spec": _good_spec_dict()})
    assert r["ok"] is True
    assert r["result"]["status"] == "pending_confirmation"
    saved = ctx.db.get_app("draft-app")
    assert saved.status == "pending_confirmation"
    assert saved.inputs[0].id == "pings"


def test_finalize_app_creates_the_app_if_it_never_existed(ctx):
    r = _call(ctx, "finalize_app", {"spec": _good_spec_dict("draft-app")})
    assert r["ok"] is True
    assert ctx.db.get_app("draft-app") is not None


def test_finalize_app_warns_on_missing_plausibility(ctx):
    r = _call(ctx, "finalize_app", {"spec": _good_spec_dict()})
    assert r["result"]["warnings"]


def test_finalize_app_rejects_an_invalid_spec(ctx):
    bad = _good_spec_dict()
    bad["inputs"][0]["operators"][0]["op"] = "nope"
    r = _call(ctx, "finalize_app", {"spec": bad})
    assert r["ok"] is False
    assert ctx.db.get_app("draft-app").status == "draft"  # never saved as pending_confirmation


def test_finalize_app_rejects_a_spec_proposing_active_status(ctx):
    bad = _good_spec_dict()
    bad["status"] = "active"
    r = _call(ctx, "finalize_app", {"spec": bad})
    assert r["ok"] is False and "active" in r["error"]


def test_finalize_app_rejects_a_mismatched_app_id(ctx):
    bad = _good_spec_dict()
    bad["app_id"] = "some-other-app"
    r = _call(ctx, "finalize_app", {"spec": bad})
    assert r["ok"] is False and "does not match" in r["error"]


def test_finalize_app_rejects_an_already_active_app(ctx):
    active = ctx.db.get_app("draft-app").model_copy(update={"status": "active"})
    ctx.db.upsert_app(active, t=2.0)
    r = _call(ctx, "finalize_app", {"spec": _good_spec_dict()})
    assert r["ok"] is False and "already active" in r["error"]
