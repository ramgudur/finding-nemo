import pytest

from app.core.db import Db
from app.core.models import AppSpec
from app.tools import registry
from app.tools.context import ToolContext


@pytest.fixture(autouse=True)
def _permissive_url_guard(monkeypatch):
    """These tests cover connector behaviour against a local/mock feed; the SSRF guard has its own
    tests (test_url_guard.py)."""
    from app.connectors import url_guard

    monkeypatch.setattr(url_guard, "check_url", lambda url, **kw: url)


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
        "verification": [{"method": "predictive_check", "priority": 1}],
    }
    r = _call(ctx, "validate_spec", {"spec": spec})
    assert r["result"]["valid"] is True
    assert r["result"]["errors"] == []
    assert r["result"]["warnings"] == []


def test_validate_spec_warns_when_verification_leaves_out_a_method_the_inputs_can_feed(ctx):
    """The Critic tunes flexible weights only once two methods have data (cold start), so a
    proposal with one method on an app that could feed three would never let it act."""

    def inp(id_, tier="flexible", op="density"):
        return {
            "id": id_,
            "kind": "push",
            "shape": "point",
            "tier": tier,
            "meaning": "a report",
            "operators": [{"op": op, "weight": 1.0}],
            "plausibility": {"max_speed_mps": 12},
        }

    base = {**_draft().model_dump(mode="json"), "inputs": [inp("a"), inp("b"), inp("c", "locked", "override")]}
    one = {**base, "verification": [{"method": "predictive_check", "priority": 1}]}
    w = _call(ctx, "validate_spec", {"spec": one})["result"]["warnings"]
    assert any("cross_input_agreement" in x and "authority_confirmation" in x for x in w)
    full = {
        **base,
        "verification": [
            {"method": "authority_confirmation", "priority": 1},
            {"method": "predictive_check", "priority": 2},
            {"method": "cross_input_agreement", "priority": 3},
        ],
    }
    assert not any("leaves out" in x for x in _call(ctx, "validate_spec", {"spec": full})["result"]["warnings"])
    # one weighted input: cross_input_agreement can't have data, so it isn't asked for
    single = {**base, "inputs": [inp("a")], "verification": [{"method": "predictive_check", "priority": 1}]}
    assert _call(ctx, "validate_spec", {"spec": single})["result"]["warnings"] == []


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


def test_test_connector_does_a_real_fetch_against_an_injected_client(ctx):
    import httpx

    _call(
        ctx,
        "create_input",
        {"id": "weather", "kind": "pull", "shape": "field", "tier": "stiff", "meaning": "wind"},
    )
    created = _call(
        ctx,
        "create_connector",
        {
            "input_id": "weather",
            "type": "json_api",
            "preset": "open_meteo",
            "config": {"latitude": 32.8, "longitude": -96.7},
        },
    )

    def handler(request):
        return httpx.Response(200, json={"current": {"wind_speed_10m": 5.0, "wind_direction_10m": 180}})

    ctx.extra["http_client"] = httpx.Client(transport=httpx.MockTransport(handler))
    r = _call(ctx, "test_connector", {"connector_id": created["result"]["connector_id"]})
    assert r["ok"] is True and r["result"]["config_valid"] is True
    assert r["result"]["items_parsed"] == 1


def test_test_connector_reports_a_failed_fetch(ctx):
    import httpx

    _call(
        ctx,
        "create_input",
        {"id": "weather", "kind": "pull", "shape": "field", "tier": "stiff", "meaning": "wind"},
    )
    _call(
        ctx,
        "create_connector",
        {
            "input_id": "weather",
            "type": "json_api",
            "preset": "open_meteo",
            "config": {"latitude": 32.8, "longitude": -96.7},
        },
    )

    def handler(request):
        return httpx.Response(500, text="server error")

    ctx.extra["http_client"] = httpx.Client(transport=httpx.MockTransport(handler))
    r = _call(ctx, "test_connector", {"connector_id": "weather"})
    assert r["ok"] is False and "connector test failed" in r["error"]


def test_a_failed_agent_connector_test_removes_the_connector(ctx):
    import httpx

    _call(ctx, "create_input", {"id": "weather", "kind": "pull", "shape": "field", "tier": "stiff", "meaning": "wind"})
    _call(ctx, "create_connector", {"input_id": "weather", "type": "json_api", "preset": "open_meteo"})
    ctx.extra["http_client"] = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    r = _call(ctx, "test_connector", {"connector_id": "weather"})
    assert r["ok"] is False and "removed" in r["error"]
    assert next(i for i in ctx.db.get_app("draft-app").inputs if i.id == "weather").connector is None
    removal = ctx.db.list_changes("draft-app", kind="connector")[-1]
    assert removal.after is None and "failed test" in removal.reason


def test_test_connector_counts_rss_items_without_coordinates(ctx):
    """A normal news feed has no georss points; the live poller places its items with the locator,
    so the test must count them (it used to report 'no items' and fail every such feed)."""
    import httpx

    _call(ctx, "create_input", {"id": "news", "kind": "pull", "shape": "document", "tier": "stiff", "meaning": "news"})
    _call(ctx, "create_connector", {"input_id": "news", "type": "rss", "url": "https://feeds.example.org/a.xml"})
    xml = (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
        "<item><title>Road closed on Main St</title><guid>1</guid></item>"
        "<item><title>Another item</title><guid>2</guid></item>"
        "</channel></rss>"
    )
    ctx.extra["http_client"] = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, text=xml, headers={"content-type": "application/rss+xml"})
        )
    )
    r = _call(ctx, "test_connector", {"connector_id": "news"})
    assert r["ok"] is True, r
    assert r["result"]["items_parsed"] == 2 and r["result"]["items_located"] == 0


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


# -- #137: routing destinations, label set, authority-first verification -----------------


def _spec_with(**extra):
    base = {
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
    base.update(extra)
    return base


def test_validate_spec_rejects_routing_without_any_destination(ctx):
    r = _call(ctx, "validate_spec", {"spec": _spec_with(routing={"enabled": True})})
    assert r["result"]["valid"] is False
    assert any("nothing can be a destination" in e for e in r["result"]["errors"])
    with_label = _spec_with(
        routing={"enabled": True},
        outputs={"poi_labels": {"mode": "on", "label_set": [{"label": "exit", "destination": True}]}},
    )
    assert _call(ctx, "validate_spec", {"spec": with_label})["result"]["valid"] is True
    with_point = _spec_with(
        routing={"enabled": True, "destination_points": [{"lat": 0.001, "lon": 0.001, "label": "gate"}]}
    )
    assert _call(ctx, "validate_spec", {"spec": with_point})["result"]["valid"] is True
    assert _call(ctx, "validate_spec", {"spec": _spec_with(routing={"enabled": False})})["result"]["valid"] is True


def test_validate_spec_warns_on_labels_on_with_an_empty_set(ctx):
    r = _call(ctx, "validate_spec", {"spec": _spec_with(outputs={"poi_labels": {"mode": "on"}})})
    assert r["result"]["valid"] is True
    assert any("label_set is empty" in w for w in r["result"]["warnings"])
    auto = _call(ctx, "validate_spec", {"spec": _spec_with(outputs={"poi_labels": {"mode": "auto"}})})
    assert not any("label_set" in w for w in auto["result"]["warnings"])


def test_validate_spec_warns_unless_authority_confirmation_leads_with_locked_inputs(ctx):
    locked = {
        "id": "marker",
        "kind": "push",
        "shape": "fixed_point",
        "tier": "locked",
        "meaning": "an authority marker",
        "operators": [{"op": "override", "weight": 1.0}],
    }
    spec = _spec_with()
    spec["inputs"].append(locked)
    wrong = {
        **spec,
        "verification": [
            {"method": "cross_input_agreement", "priority": 1},
            {"method": "authority_confirmation", "priority": 2},
        ],
    }
    r = _call(ctx, "validate_spec", {"spec": wrong})
    assert any("authority_confirmation should be verification priority 1" in w for w in r["result"]["warnings"])
    right = {**spec, "verification": [{"method": "authority_confirmation", "priority": 1}]}
    assert not any("priority 1" in w for w in _call(ctx, "validate_spec", {"spec": right})["result"]["warnings"])
    # no locked inputs: no opinion on the order
    plain = _spec_with(verification=[{"method": "cross_input_agreement", "priority": 1}])
    assert not any("priority 1" in w for w in _call(ctx, "validate_spec", {"spec": plain})["result"]["warnings"])


def test_compiler_prompt_is_domain_neutral():
    from app.agents import compiler

    words = ("people", "person", "bird", "police", "wind", "weather", "news", "campus", "threat")
    assert not any(w in compiler.SYSTEM_PROMPT.lower() for w in words)


def test_source_suggester_prompt_is_domain_neutral():
    from app.agents import source_suggester

    words = ("people", "person", "bird", "police", "wind", "weather", "news", "campus", "threat", "fire", "phone")
    text = source_suggester.SYSTEM_PROMPT.lower()
    assert not any(w in text for w in words)


# -- #152: the dev's chosen source (CompileInput.connector) is copied verbatim -----------


def _pull_spec_dict(connector: dict | None = None) -> dict:
    spec = _good_spec_dict()
    feed = {
        "id": "feed",
        "kind": "pull",
        "shape": "document",
        "tier": "stiff",
        "meaning": "public reports about the area",
        "operators": [{"op": "event_points", "weight": 0.3}],
    }
    if connector is not None:
        feed["connector"] = connector
    spec["inputs"].append(feed)
    return spec


def test_finalize_app_copies_the_devs_connector_over_the_models(ctx):
    dev = {"type": "rss", "url": "https://feeds.example.org/area.xml", "poll_s": 120, "config": {}, "preset": None}
    ctx.extra["dev_connectors"] = {"feed": dev}
    r = _call(ctx, "finalize_app", {"spec": _pull_spec_dict({"type": "rss", "url": "https://other.example.net/x"})})
    assert r["ok"] is True, r
    feed = next(i for i in ctx.db.get_app("draft-app").inputs if i.id == "feed")
    assert feed.connector.model_dump(mode="json") == dev
    (change,) = ctx.db.list_changes("draft-app", kind="connector")
    assert change.actor == "dev" and change.target == "feed" and change.after == dev


def test_finalize_app_adds_the_devs_connector_when_the_model_left_it_out(ctx):
    ctx.extra["dev_connectors"] = {"feed": {"type": "json_api", "preset": "open_meteo"}}
    r = _call(ctx, "finalize_app", {"spec": _pull_spec_dict()})
    assert r["ok"] is True, r
    feed = next(i for i in ctx.db.get_app("draft-app").inputs if i.id == "feed")
    assert feed.connector.type == "json_api" and feed.connector.preset == "open_meteo"


def test_finalize_app_refuses_an_unsafe_dev_connector_url(ctx, monkeypatch):
    from app.connectors import url_guard

    def refuse(url, **kw):
        raise url_guard.UnsafeUrl("that host is not allowed")

    monkeypatch.setattr(url_guard, "check_url", refuse)
    ctx.extra["dev_connectors"] = {"feed": {"type": "rss", "url": "http://internal.invalid/feed"}}
    r = _call(ctx, "finalize_app", {"spec": _pull_spec_dict()})
    assert r["ok"] is False and "connector URL refused" in r["error"]
    assert ctx.db.get_app("draft-app").status == "draft"


# -- #222: the dev's chosen webhook mapping (CompileInput.webhook) is copied verbatim ----


def test_finalize_app_copies_the_devs_webhook_mapping_over_the_models(ctx):
    dev = {
        "items_path": "items",
        "lat": "lat",
        "lon": "lon",
        "ts": None,
        "ts_format": "iso",
        "user_id": None,
        "fields": {},
    }
    ctx.extra["dev_webhooks"] = {"pings": dev}
    spec = _good_spec_dict()
    spec["inputs"][0]["webhook"] = {"items_path": "x", "lat": "a", "lon": "b"}  # the model's own guess
    r = _call(ctx, "finalize_app", {"spec": spec})
    assert r["ok"] is True, r
    pings = next(i for i in ctx.db.get_app("draft-app").inputs if i.id == "pings")
    assert pings.webhook.model_dump(mode="json") == dev
    (change,) = ctx.db.list_changes("draft-app", kind="webhook")
    assert change.actor == "dev" and change.target == "pings" and change.after == dev


def test_finalize_app_adds_the_devs_webhook_mapping_when_the_model_left_it_out(ctx):
    ctx.extra["dev_webhooks"] = {"pings": {"items_path": "", "lat": "lat", "lon": "lon"}}
    r = _call(ctx, "finalize_app", {"spec": _good_spec_dict()})
    assert r["ok"] is True, r
    pings = next(i for i in ctx.db.get_app("draft-app").inputs if i.id == "pings")
    assert pings.webhook.lat == "lat" and pings.webhook.lon == "lon"


# -- the dev's declarations are pinned at finalize (#164) -----------------------------------------


def _pinned_ctx(ctx):
    ctx.extra["dev_inputs"] = [
        {"id": "pings", "kind": "push", "shape": "point", "meaning": "the dev's own words", "fields": []}
    ]
    ctx.extra["dev_intent"] = "the dev's intent"
    ctx.extra["dev_grid"] = {"cell_m": 10, "bbox": BBOX}
    return ctx


def _proposal(**over):
    inp = {
        "id": "pings",
        "kind": "push",
        "shape": "track",  # the model changed the shape
        "tier": "flexible",
        "meaning": "a reworded meaning",
        "operators": [{"op": "density", "weight": 1.0}],
        "plausibility": {"max_speed_mps": 12},
    }
    spec = {
        **_draft().model_dump(mode="json"),
        "intent": "a reworded intent",
        "inputs": [inp],
        "verification": [{"method": "predictive_check", "priority": 1}],
    }
    return {**spec, **over}


def test_finalize_pins_the_devs_inputs_intent_and_grid(ctx):
    r = _call(_pinned_ctx(ctx), "finalize_app", {"spec": _proposal()})
    assert r["ok"], r
    saved = ctx.db.get_app("draft-app")
    (inp,) = saved.inputs
    assert (inp.meaning, inp.shape, inp.kind) == ("the dev's own words", "point", "push")
    assert saved.intent == "the dev's intent"
    assert inp.operators[0].op == "density" and inp.tier == "flexible"  # the model's choices stay


def test_finalize_rejects_inputs_the_dev_did_not_declare_or_dropped(ctx):
    _pinned_ctx(ctx)
    extra = _proposal()
    extra["inputs"] = [*extra["inputs"], {**extra["inputs"][0], "id": "invented"}]
    r = _call(ctx, "finalize_app", {"spec": extra})
    assert not r["ok"] and "not declared by the dev" in r["error"] and "invented" in r["error"]
    r2 = _call(ctx, "finalize_app", {"spec": _proposal(inputs=[])})
    assert not r2["ok"] and "missing" in r2["error"] and "pings" in r2["error"]


def test_create_connector_declares_the_connectors_implied_fields(ctx):
    """#259: an RSS connector's events carry doc_id/confidence, so they are declared with it."""
    _call(ctx, "create_input", {"id": "docs", "kind": "pull", "shape": "document", "tier": "stiff", "meaning": "d"})
    r = _call(ctx, "create_connector", {"input_id": "docs", "type": "rss", "url": "https://f.example.org/x"})
    assert r["ok"] is True
    got = {f.name: f.required for f in ctx.db.get_app("draft-app").inputs[0].fields}
    assert got == {"doc_id": False, "confidence": False}
