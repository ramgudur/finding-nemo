"""Tool registry: name -> JSON schema, handler, and which agents may call it.

``agents/loop.py`` uses two functions:
- ``tool_schemas(agent)`` -> OpenAI-style ``tools`` list for that agent's chat call.
- ``call_tool(ctx, name, args)`` -> ``{"ok": True, "result": ...}`` or ``{"ok": False, "error": ...}``.
  It never raises for agent mistakes or guard rejections; the error text is for the trace.

Argument validation is Pydantic (extra keys rejected). Limits live in ``guards.py``, not here
and not in prompts.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from app.tools import compiler_tools as ct
from app.tools import connector_tools as cn
from app.tools import poi_tools as pt
from app.tools import read_tools as rd
from app.tools import scoring_tools as st
from app.tools import search_tools as sr
from app.tools.context import ToolContext
from app.tools.guards import GuardError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    handler: Callable[[ToolContext, Any], dict[str, Any]]
    agents: frozenset[str]  # agent names allowed to call it; "dev" marks a dev-console action
    # True if a successful call changes app state (spec, connectors, weights, trust, POIs, label
    # proposals, source reliability). Read-only tools, backtests (a record, not a change),
    # annotate and request_scout (trace entries only) stay False. `agents/loop.py` derives a run's
    # closing trace from these, so the trace never takes the model's word that something changed.
    changes_state: bool = False


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in (
        Tool(
            "get_map_summary",
            "Latest tick: score stats, hotspots (id, position, peak, contributing layers), every layer's "
            "weight with its tier and pin, and the flexible weight total.",
            rd.NoArgs,
            rd.get_map_summary,
            frozenset({"critic"}),
        ),
        Tool(
            "get_layer_stats",
            "Per-layer stats from the latest tick: normalized max/mean, share of non-zero cells, peak position, "
            "weight, and small operator extras (e.g. a point estimate).",
            rd.LayerStatsArgs,
            rd.get_layer_stats,
            frozenset({"critic"}),
        ),
        Tool(
            "get_verification_report",
            "Verification methods (in spec priority) on the cached recent window with current weights: value, "
            "whether each has data, and whether the app is still in cold start.",
            rd.WindowArgs,
            rd.get_verification_report,
            frozenset({"critic"}),
        ),
        Tool(
            "get_suspicious_users",
            "Spoof flags per user over the window (duplicate coords across accounts, impossible speed, new-ID "
            "bursts, constant speed, straight tracks, shared device/IP). Flags, not verdicts; no coordinates.",
            rd.WindowArgs,
            rd.get_suspicious_users,
            frozenset({"critic"}),
        ),
        Tool(
            "annotate",
            "Leave a short note for the dev in the trace. Changes nothing.",
            rd.AnnotateArgs,
            rd.annotate,
            frozenset({"critic", "compiler", "scout", "poi_labeler"}),
        ),
        Tool(
            "run_backtest",
            "Backtest a proposed weight or trust change on held-out recent data. Weights are checked "
            "against tier rules first and renormalized; the result says whether the holdout passed. "
            "Only a passing backtest_id can be applied.",
            st.RunBacktestArgs,
            st.run_backtest,
            frozenset({"critic"}),
        ),
        Tool(
            "run_backtest_batch",
            "Search many weight configs at once: give a [lo, hi] range per layer_id and a sample count. "
            "Configs outside tier bounds are dropped before scoring, ranking uses train data only, and the "
            "top few are checked on held-out data. Prefer this for weight changes; apply the best passing "
            "config with apply_changes(backtest_id).",
            st.RunBacktestBatchArgs,
            st.run_backtest_batch,
            frozenset({"critic"}),
        ),
        Tool(
            "apply_changes",
            "Apply exactly what a passing backtest tested. Rejected if the holdout failed, the backtest "
            "is stale or already used, or weights changed since.",
            st.ApplyChangesArgs,
            st.apply_changes,
            frozenset({"critic"}),
            changes_state=True,
        ),
        Tool(
            "set_user_trust",
            "Lower one user's trust (to a value in [0, 1]) with a reason. Increases need evidence: use "
            "run_backtest(trust=...) and apply_changes (capped at +0.1 per user per 5 minutes).",
            st.SetUserTrustArgs,
            st.set_user_trust,
            frozenset({"critic"}),
            changes_state=True,
        ),
        Tool(
            "pin_weight",
            "Dev only: pin or unpin a layer weight so no agent change or renormalization moves it.",
            st.PinWeightArgs,
            st.pin_weight,
            frozenset({"dev"}),
            changes_state=True,
        ),
        Tool(
            "list_operators",
            "Catalog of available operators: name, one-line description, accepted input shapes, params schema.",
            ct.NoArgs,
            ct.list_operators,
            frozenset({"compiler"}),
        ),
        Tool(
            "validate_spec",
            "Check a proposed AppSpec: Pydantic errors, unknown operators, operator/shape mismatches, and a "
            "warning when a flexible point/track input has no plausibility.",
            ct.ValidateSpecArgs,
            ct.validate_spec,
            frozenset({"compiler"}),
        ),
        Tool(
            "create_input",
            "Add one input to the app's draft spec.",
            ct.CreateInputArgs,
            ct.create_input,
            frozenset({"compiler"}),
            changes_state=True,
        ),
        Tool(
            "create_connector",
            "Attach a pull connector config to an existing pull input. Config only, never code.",
            ct.CreateConnectorArgs,
            ct.create_connector,
            frozenset({"compiler", "scout"}),
            changes_state=True,
        ),
        Tool(
            "test_connector",
            "Validate a connector's config shape. Live-fetch testing lands with the connector engine.",
            ct.TestConnectorArgs,
            ct.test_connector,
            frozenset({"compiler", "scout"}),
        ),
        Tool(
            "request_scout",
            "Ask Scout to find and configure a pull connector for an input. Scout runs on request only.",
            ct.RequestScoutArgs,
            ct.request_scout,
            frozenset({"compiler"}),
        ),
        Tool(
            "finalize_app",
            "Validate the complete proposed AppSpec and set it to pending_confirmation for the dev to "
            "review. Never activates it.",
            ct.FinalizeAppArgs,
            ct.finalize_app,
            frozenset({"compiler"}),
            changes_state=True,
        ),
        Tool(
            "get_poi_candidates",
            "Current code-made candidates (id, position, radius, source, persistence, stats, existing label) plus"
            " the app's label_set, mode, min_confidence and intent.",
            pt.NoArgs,
            pt.get_poi_candidates,
            frozenset({"poi_labeler"}),
        ),
        Tool(
            "find_candidates",
            "Filter current candidates by persistence, generator, or unlabeled only.",
            pt.FindCandidatesArgs,
            pt.find_candidates,
            frozenset({"poi_labeler"}),
        ),
        Tool(
            "osm_features_near",
            "Mapped features (OpenStreetMap) near a candidate: name and a few tags, with distance. Cached and "
            "rate-limited.",
            pt.CandidateRadiusArgs,
            pt.osm_features_near,
            frozenset({"poi_labeler"}),
        ),
        Tool(
            "input_values_at",
            "What each of the app's inputs reported near a candidate over a window: event counts, distinct "
            "sources, and declared numeric fields (mean/min/max).",
            pt.CandidateWindowArgs,
            pt.input_values_at,
            frozenset({"poi_labeler"}),
        ),
        Tool(
            "get_layer_stats_at",
            "Score and each layer's normalized mean/max around a candidate in the latest tick.",
            pt.CandidateArgs,
            pt.get_layer_stats_at,
            frozenset({"poi_labeler"}),
        ),
        Tool(
            "get_time_pattern",
            "Event counts near a candidate in equal time buckets per input, with first/last seen.",
            pt.CandidateWindowArgs,
            pt.get_time_pattern,
            frozenset({"poi_labeler"}),
        ),
        Tool(
            "create_poi",
            "Label a current candidate with a label from the app's label_set (position comes from the candidate)."
            " Below min_confidence or unknown labels are rejected; propose new label types with propose_label.",
            pt.CreatePoiArgs,
            pt.create_poi,
            frozenset({"poi_labeler"}),
            changes_state=True,
        ),
        Tool(
            "update_poi",
            "Change an active POI's label, confidence or evidence (label_set and min_confidence still apply).",
            pt.UpdatePoiArgs,
            pt.update_poi,
            frozenset({"poi_labeler"}),
            changes_state=True,
        ),
        Tool(
            "retire_poi",
            "Retire a POI that no longer holds, with a reason.",
            pt.RetirePoiArgs,
            pt.retire_poi,
            frozenset({"poi_labeler"}),
            changes_state=True,
        ),
        Tool(
            "propose_label",
            "Propose a new label type for the app's label_set when none fits a place worth naming. It changes "
            "nothing until the dev accepts it; you cannot use it with create_poi before then. Not both sensitive "
            "and destination; at most 5 open proposals.",
            pt.ProposeLabelArgs,
            pt.propose_label,
            frozenset({"poi_labeler"}),
            changes_state=True,
        ),
        Tool(
            "accept_label_proposal",
            "Dev only: add an open label proposal to the active spec's label_set (flags may be overridden).",
            pt.ProposalDecisionArgs,
            pt.accept_label_proposal,
            frozenset({"dev"}),
            changes_state=True,
        ),
        Tool(
            "dismiss_label_proposal",
            "Dev only: dismiss an open label proposal.",
            pt.DismissProposalArgs,
            pt.dismiss_label_proposal,
            frozenset({"dev"}),
            changes_state=True,
        ),
        Tool(
            "web_search",
            "Search the web for candidate sources (feeds, data endpoints). Returns titles, URLs and short "
            "snippets; nothing is stored.",
            sr.WebSearchArgs,
            sr.web_search,
            frozenset({"scout"}),
        ),
        Tool(
            "fetch_url",
            "Fetch a public http(s) page as plain text (size and time capped; no redirects, no private addresses).",
            cn.UrlArgs,
            cn.fetch_url,
            frozenset({"scout"}),
        ),
        Tool(
            "probe_feed",
            "Check whether a URL is a usable feed: RSS/Atom item counts, how many items carry a location, "
            "sample titles; or a JSON endpoint's top-level shape.",
            cn.UrlArgs,
            cn.probe_feed,
            frozenset({"scout"}),
        ),
        Tool(
            "geocode",
            "Turn a place name into a point inside this app's area (or report that none was found there).",
            cn.GeocodeArgs,
            cn.geocode,
            frozenset({"scout"}),
        ),
        Tool(
            "set_source_reliability",
            "Lower how much a document source's items count, with a reason. Raising it needs the Critic's backtest.",
            cn.SourceReliabilityArgs,
            cn.set_source_reliability,
            frozenset({"scout"}),
            changes_state=True,
        ),
    )
}


def tool_schemas(agent: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.args.model_json_schema()},
        }
        for t in TOOLS.values()
        if agent in t.agents
    ]


def changes_state(name: str) -> bool:
    """Whether a successful call to tool `name` changes app state (unknown names: False)."""
    tool = TOOLS.get(name)
    return tool is not None and tool.changes_state


def _object_arg_names(model: type[BaseModel]) -> set[str]:
    """Top-level argument names whose JSON schema is an object (a dict field, a nested model, or
    an optional one of those)."""
    names: set[str] = set()
    for key, prop in model.model_json_schema().get("properties", {}).items():
        options = [prop, *prop.get("anyOf", []), *prop.get("oneOf", []), *prop.get("allOf", [])]
        if any(o.get("type") == "object" or "$ref" in o for o in options):
            names.add(key)
    return names


def _decode_object_args(model: type[BaseModel], args: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Models sometimes send an object argument as a JSON *string*. Decode those strings (the
    result is then validated normally, extra keys still rejected), or explain precisely why the
    string isn't an object, so the model can repair its next call instead of repeating it."""
    out = dict(args)
    errors: list[str] = []
    for key in _object_arg_names(model):
        value = out.get(key)
        if not isinstance(value, str):
            continue
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as e:
            errors.append(
                f"{key} was a string, not an object; JSON parse error: {e.msg} at char {e.pos} "
                f"(line {e.lineno} col {e.colno}); the string is {len(value)} chars long. Pass {key} as a JSON object."
            )
            continue
        if not isinstance(decoded, dict):
            errors.append(
                f"{key} was a string holding JSON {type(decoded).__name__}, not an object. Pass {key} as a JSON object."
            )
            continue
        out[key] = decoded
    return out, errors


def call_tool(ctx: ToolContext, name: str, args: dict[str, Any] | None) -> dict[str, Any]:
    tool = TOOLS.get(name)
    if tool is None:
        return {"ok": False, "error": f"unknown tool {name!r}"}
    caller = "dev" if ctx.actor == "dev" else (ctx.agent or "")
    if caller not in tool.agents:
        return {"ok": False, "error": f"tool {name!r} is not available to {caller or 'this caller'}"}
    if args is not None and not isinstance(args, dict):
        return {"ok": False, "error": "invalid arguments: pass the arguments as a JSON object"}
    args, decode_errors = _decode_object_args(tool.args, args or {})
    if decode_errors:
        return {"ok": False, "error": "invalid arguments: " + "; ".join(decode_errors)}
    try:
        parsed = tool.args.model_validate(args)
    except ValidationError as e:
        errs = "; ".join(f"{'.'.join(map(str, x['loc'])) or 'args'}: {x['msg']}" for x in e.errors())
        return {"ok": False, "error": f"invalid arguments: {errs}"}
    try:
        return {"ok": True, "result": tool.handler(ctx, parsed)}
    except GuardError as e:
        return {"ok": False, "error": "rejected: " + "; ".join(e.reasons)}
    except LookupError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001 - a tool bug must not crash the agent loop
        log.exception("tool %s failed", name)
        return {"ok": False, "error": f"internal error in {name}: {type(e).__name__}"}
