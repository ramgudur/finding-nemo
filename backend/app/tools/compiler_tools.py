"""Compiler (and, for connectors, Scout) tools: shape an `AppSpec` and hand it to the dev.

Neither agent writes state directly (rule 1): these tools are their only way to touch a
spec. Two ways to reach `pending_confirmation`:
  - one-shot: `finalize_app(spec=<the whole proposed AppSpec>)` validates and upserts it
    directly (this is what the Compiler's recorded runs do).
  - incremental: `create_input` adds one input at a time to an existing draft row.
Either way, nothing here can make a spec `active` -- only the dev's confirmation (at
`api/apps.py`'s `activate`) does that, which is also the only point that writes a Change
for the spec itself. `create_connector` does write a `Change` (kind="connector"), since it
can touch an already-active app's pull input (Scout's job after confirmation too). Pull
connectors are config, not code (rule 10): `create_connector` only stores and validates one.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.connectors import engine as connector_engine
from app.connectors import url_guard
from app.connectors.engine import check_connector_sync
from app.connectors.fields import declare_input
from app.core.models import (
    SLUG,
    AppSpec,
    Change,
    ConnectorConfig,
    ConnectorType,
    InputField,
    InputKind,
    InputShape,
    InputSpec,
    OperatorConfig,
    Plausibility,
    Tier,
    TraceEntry,
    Visibility,
)
from app.engine.operators import registry
from app.tools.context import ToolContext
from app.tools.guards import GuardError


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_Args):
    pass


class ValidateSpecArgs(_Args):
    spec: dict[str, Any] = Field(description="The proposed AppSpec, as a JSON-able dict.")


class CreateInputArgs(_Args):
    id: str = Field(pattern=SLUG)
    kind: InputKind
    shape: InputShape
    tier: Tier
    visibility: Visibility = "authority"
    meaning: str = Field(description="The dev's words, verbatim.")
    fields: list[InputField] = Field(default_factory=list)
    operators: list[OperatorConfig] = Field(default_factory=list)
    plausibility: Plausibility | None = None


class CreateConnectorArgs(_Args):
    input_id: str
    type: ConnectorType
    url: str | None = None
    poll_s: float = Field(default=300, gt=0)
    config: dict[str, Any] = Field(default_factory=dict)
    preset: str | None = None


class TestConnectorArgs(_Args):
    connector_id: str = Field(description="The connector_id a create_connector call returned (its input_id).")


class RequestScoutArgs(_Args):
    input_id: str
    instructions: str = Field(min_length=1, max_length=1000, description="What Scout should find and configure.")


class FinalizeAppArgs(_Args):
    spec: dict[str, Any] = Field(description="The complete proposed AppSpec, as a JSON-able dict.")


def _spec(ctx: ToolContext) -> AppSpec:
    spec = ctx.db.get_app(ctx.app_id)
    if spec is None:
        raise GuardError(f"app {ctx.app_id!r} does not exist yet")
    return spec


def _draft(ctx: ToolContext) -> AppSpec:
    spec = _spec(ctx)
    if spec.status != "draft":
        raise GuardError(f"app is {spec.status!r}; new inputs can only be added to a draft spec")
    return spec


def _save(ctx: ToolContext, spec: AppSpec) -> None:
    ctx.db.upsert_app(spec, t=ctx.now())


def list_operators(ctx: ToolContext, args: NoArgs) -> dict[str, Any]:
    return {"operators": registry.list_operators()}


def _spec_warnings(spec: AppSpec) -> list[str]:
    warnings: list[str] = []
    for inp in spec.inputs:
        if inp.tier == "flexible" and inp.shape in ("point", "track") and inp.plausibility is None:
            warnings.append(
                f"input {inp.id!r} is flexible and {inp.shape}-shaped but has no plausibility; "
                "the platform's neutral defaults (no speed limit, no shape checks) will apply"
            )
    labels = spec.outputs.poi_labels
    if labels.mode == "on" and not labels.label_set:
        warnings.append(
            "poi_labels.mode is 'on' but label_set is empty, so the POI Labeler can never label anything; "
            "propose labels for the places the intent names (or use mode 'auto' / 'off')"
        )
    methods = {v.method for v in spec.verification}
    weighted_inputs = {
        inp.id
        for inp in spec.inputs
        for op in inp.operators
        if op.op in registry.OPERATORS and registry.get(op.op).role != "override"
    }
    missing = []
    if weighted_inputs and "predictive_check" not in methods:
        missing.append("predictive_check")
    if len(weighted_inputs) >= 2 and "cross_input_agreement" not in methods:
        missing.append("cross_input_agreement")
    if any(i.tier == "locked" for i in spec.inputs) and "authority_confirmation" not in methods:
        missing.append("authority_confirmation")
    if missing:
        warnings.append(
            f"verification leaves out {missing}, which this app's inputs can feed. The Critic may change "
            "flexible weights only once two methods have data (cold start), so propose every method the "
            "inputs support"
        )
    if any(i.tier == "locked" for i in spec.inputs):
        order = [v.method for v in sorted(spec.verification, key=lambda v: v.priority)]
        if not order or order[0] != "authority_confirmation":
            warnings.append(
                "the spec has authority (locked) inputs, so authority_confirmation should be "
                f"verification priority 1 (current order: {order or 'none'})"
            )
    return warnings


def _weighted_layer_ids(spec: AppSpec) -> list[str]:
    """Every layer_id whose operator is registered and isn't an override mask."""
    return [
        op.layer_id
        for inp in spec.inputs
        for op in inp.operators
        if op.op in registry.OPERATORS and registry.get(op.op).role != "override"
    ]


def _spec_errors(spec: AppSpec) -> list[str]:
    errors: list[str] = []
    if spec.status == "active":
        errors.append("a proposed spec cannot set status to 'active' directly; only dev confirmation can")
    for inp in spec.inputs:
        for op in inp.operators:
            if op.op not in registry.OPERATORS:
                errors.append(f"input {inp.id!r}: unknown operator {op.op!r}")
                continue
            if inp.shape not in registry.get(op.op).shapes:
                errors.append(f"input {inp.id!r}: operator {op.op!r} doesn't accept shape {inp.shape!r}")
            try:
                registry.parse_params(op.op, op.params())
            except Exception as e:  # noqa: BLE001 - reported to the agent, not raised
                errors.append(f"input {inp.id!r}: operator {op.op!r} params invalid: {e}")
    routing = spec.routing
    if routing.enabled:
        has_dest_label = any(lab.destination for lab in spec.outputs.poi_labels.label_set)
        if not has_dest_label and not routing.destination_points:
            errors.append(
                "routing is enabled but nothing can be a destination: add a label with destination=true "
                "(and keep poi_labels on) or routing.destination_points, or set routing.enabled false"
            )
    public_ids = {ol.id for ol in spec.outputs.layers if ol.visibility == "public"}
    if "score" in public_ids:
        private_weighted = [lid for lid in _weighted_layer_ids(spec) if lid not in public_ids]
        if private_weighted:
            errors.append(
                f"'score' is public but weighted layer(s) {private_weighted} are not; "
                "a public score would leak their values"
            )
    return errors


def validate_spec(ctx: ToolContext, args: ValidateSpecArgs) -> dict[str, Any]:
    try:
        spec = AppSpec.model_validate(args.spec)
    except Exception as e:  # noqa: BLE001 - a bad proposal is reported, not raised
        return {"valid": False, "errors": [str(e)], "warnings": []}
    errors = _spec_errors(spec)
    return {"valid": not errors, "errors": errors, "warnings": _spec_warnings(spec)}


def create_input(ctx: ToolContext, args: CreateInputArgs) -> dict[str, Any]:
    spec = _draft(ctx)
    if any(i.id == args.id for i in spec.inputs):
        raise GuardError(f"input {args.id!r} already exists; use a new id")
    new_input = InputSpec(
        id=args.id,
        kind=args.kind,
        shape=args.shape,
        tier=args.tier,
        visibility=args.visibility,
        meaning=args.meaning,
        fields=args.fields,
        operators=args.operators,
        plausibility=args.plausibility,
    )
    try:
        candidate = spec.model_copy(update={"inputs": [*spec.inputs, new_input]})
        spec = AppSpec.model_validate(candidate.model_dump())
    except Exception as e:  # noqa: BLE001 - surfaced to the agent, not raised past the tool
        raise GuardError(f"invalid input: {e}") from None
    errors = _spec_errors(spec)
    if errors:
        raise GuardError(errors)
    _save(ctx, spec)
    return {"input_id": args.id, "warnings": _spec_warnings(spec)}


def _check_connector_url(ctx: ToolContext, config: ConnectorConfig) -> None:
    url = connector_engine.connector_url(config)
    if url is not None:  # the server would fetch this every poll_s: refuse internal targets up front
        try:
            url_guard.check_url(url, allow_private=ctx.extra.get("allow_private_urls", url_guard.demo_allows_private()))
        except url_guard.UnsafeUrl as e:
            raise GuardError(f"connector URL refused: {e}") from e


def create_connector(ctx: ToolContext, args: CreateConnectorArgs) -> dict[str, Any]:
    config = ConnectorConfig(type=args.type, url=args.url, poll_s=args.poll_s, config=args.config, preset=args.preset)
    set_connector(
        ctx, args.input_id, config, reason=f"{ctx.agent or ctx.actor} configured a connector for {args.input_id!r}"
    )
    # connector_id == input_id: each pull input has at most one connector today.
    return {"connector_id": args.input_id, "input_id": args.input_id, "connector": config.model_dump(mode="json")}


def set_connector(
    ctx: ToolContext, input_id: str, config: ConnectorConfig | None, *, reason: str, reverts: str | None = None
) -> Change:
    """Store (or, with ``config=None``, remove) a pull input's connector and record a Change.
    The path behind ``create_connector`` and the dev's revert (``tools/revert_tools.py``)."""
    spec = _spec(ctx)
    inp = next((i for i in spec.inputs if i.id == input_id), None)
    if inp is None:
        raise GuardError(f"input {input_id!r} does not exist; create it first")
    if inp.kind != "pull":
        raise GuardError(f"input {input_id!r} is {inp.kind!r}; only a pull input takes a connector")
    before = inp.connector.model_dump(mode="json") if inp.connector else None
    if config is not None:
        _check_connector_url(ctx, config)
    # The connector's own fields (connectors/fields.py, #259) are declared with it; removing a
    # connector leaves the input's fields as they are (all optional, so harmless).
    new_inputs = [
        declare_input(i.model_copy(update={"connector": config})) if i.id == input_id else i for i in spec.inputs
    ]
    new_spec = spec.model_copy(update={"inputs": new_inputs})
    _save(ctx, new_spec)
    rt = getattr(ctx.engine, "apps", {}).get(ctx.app_id)
    if rt is not None:  # keep the running engine's copy in step (tools read ctx.spec = the runtime's spec)
        rt.spec = new_spec
    change = Change(
        id=str(uuid.uuid4()),
        app_id=ctx.app_id,
        kind="connector",
        target=input_id,
        before=before,
        after=config.model_dump(mode="json") if config is not None else None,
        reason=reason,
        actor=ctx.actor,
        t=ctx.now_dt(),
        reverts=reverts,
    )
    ctx.db.add_change(change)
    return change


def test_connector(ctx: ToolContext, args: TestConnectorArgs) -> dict[str, Any]:
    spec = _spec(ctx)
    inp = next((i for i in spec.inputs if i.id == args.connector_id), None)
    if inp is None:
        raise GuardError(f"connector {args.connector_id!r} does not exist")
    if inp.connector is None:
        raise GuardError(f"input {args.connector_id!r} has no connector configured yet")
    injected = ctx.extra.get("http_client")  # tests inject an httpx.Client on a MockTransport
    try:
        if injected is not None:
            events = check_connector_sync(injected, inp.connector, ctx.now())
        else:
            with httpx.Client() as client:
                events = check_connector_sync(client, inp.connector, ctx.now())
    except Exception as e:  # noqa: BLE001 - reported to the agent, not raised past the tool
        _drop_failed(ctx, args.connector_id, f"connector test failed: {type(e).__name__}")
        raise GuardError(f"connector test failed: {type(e).__name__}: {e}; the connector was removed") from None
    if not events:
        _drop_failed(ctx, args.connector_id, "connector returned no items")
        raise GuardError("connector returned no items; the connector was removed")
    located = sum(1 for e in events if e.get("lat") is not None)
    return {
        "connector_id": args.connector_id,
        "config_valid": True,
        "items_parsed": len(events),
        "items_located": located,  # the rest are placed by the locator (extract + geocode) when polled
    }


def _drop_failed(ctx: ToolContext, input_id: str, why: str) -> None:
    """An agent's connector that fails its test is removed, so a run never leaves a broken source
    polling; the removal is a Change like any other (revertible)."""
    if ctx.actor == "dev":
        return
    set_connector(ctx, input_id, None, reason=f"removed after a failed test: {why}")


def request_scout(ctx: ToolContext, args: RequestScoutArgs) -> dict[str, Any]:
    spec = _spec(ctx)
    if not any(i.id == args.input_id for i in spec.inputs):
        raise GuardError(f"input {args.input_id!r} does not exist")
    ctx.db.add_trace(
        TraceEntry(
            t=ctx.now_dt(),
            app_id=ctx.app_id,
            agent="compiler",
            action="request_scout",
            why=args.instructions,
            saw=args.input_id,
        )
    )
    return {"input_id": args.input_id, "requested": True}


def finalize_app(ctx: ToolContext, args: FinalizeAppArgs) -> dict[str, Any]:
    """The usual path to pending_confirmation: validate the complete proposed spec and
    upsert it directly (whether or not a draft row already existed for this app_id)."""
    raw = args.spec
    dev_inputs = ctx.extra.get("dev_inputs")
    if dev_inputs is not None and isinstance(raw, dict):
        # The dev's own declarations, verbatim: the model proposes tiers, operators and the rest,
        # but can't add, drop or reword an input, or change the intent or the area.
        declared = {d["id"]: d for d in dev_inputs}
        proposed = [i for i in (raw.get("inputs") or []) if isinstance(i, dict)]
        ids = [i.get("id") for i in proposed]
        extra = [i for i in ids if i not in declared]
        missing = [d for d in declared if d not in ids]
        if extra or missing:
            raise GuardError(
                f"inputs must be exactly the dev's {sorted(declared)}"
                + (f"; not declared by the dev: {extra}" if extra else "")
                + (f"; missing: {missing}" if missing else "")
            )
        pinned = ("kind", "shape", "meaning", "fields")
        raw = {
            **raw,
            "inputs": [{**i, **{k: declared[i["id"]].get(k, []) for k in pinned}} for i in proposed],
        }
        if "dev_intent" in ctx.extra:
            raw["intent"] = ctx.extra["dev_intent"]
        if "dev_grid" in ctx.extra:
            raw["grid"] = ctx.extra["dev_grid"]
    if "dev_destination_points" in ctx.extra and isinstance(raw, dict):
        # the dev's map picks, verbatim: the model can't add, move or drop them
        routing = {**(raw.get("routing") or {}), "destination_points": ctx.extra["dev_destination_points"]}
        raw = {**raw, "routing": routing}
    dev_connectors: dict[str, Any] = ctx.extra.get("dev_connectors") or {}
    if dev_connectors and isinstance(raw, dict) and isinstance(raw.get("inputs"), list):
        # the dev's chosen sources (Builder "Add a source"), verbatim: the model can't change them
        raw = {
            **raw,
            "inputs": [
                {**i, "connector": dev_connectors[i["id"]]}
                if isinstance(i, dict) and i.get("id") in dev_connectors
                else i
                for i in raw["inputs"]
            ],
        }
    dev_webhooks: dict[str, Any] = ctx.extra.get("dev_webhooks") or {}
    if dev_webhooks and isinstance(raw, dict) and isinstance(raw.get("inputs"), list):
        # the dev's webhook mapping, verbatim: the model can't change it (#222)
        raw = {
            **raw,
            "inputs": [
                {**i, "webhook": dev_webhooks[i["id"]]} if isinstance(i, dict) and i.get("id") in dev_webhooks else i
                for i in raw["inputs"]
            ],
        }
    try:
        spec = AppSpec.model_validate(raw)
    except Exception as e:  # noqa: BLE001 - surfaced to the agent, not raised past the tool
        raise GuardError(f"invalid spec: {e}") from None
    if spec.app_id != ctx.app_id:
        raise GuardError(f"spec.app_id {spec.app_id!r} does not match this app ({ctx.app_id!r})")
    existing = ctx.db.get_app(ctx.app_id)
    if existing is not None and existing.status == "active":
        raise GuardError("app is already active; finalize_app can't touch a confirmed spec")
    errors = _spec_errors(spec)
    if errors:
        raise GuardError(errors)
    for inp in spec.inputs:
        if inp.id in dev_connectors and inp.connector is not None:
            _check_connector_url(ctx, inp.connector)
    _save(ctx, spec.model_copy(update={"status": "pending_confirmation"}))
    before = {i.id: i.connector for i in existing.inputs} if existing is not None else {}
    for inp in spec.inputs:
        if inp.id in dev_connectors and inp.connector is not None and before.get(inp.id) != inp.connector:
            prev = before.get(inp.id)
            ctx.db.add_change(
                Change(
                    id=str(uuid.uuid4()),
                    app_id=ctx.app_id,
                    kind="connector",
                    target=inp.id,
                    before=prev.model_dump(mode="json") if prev is not None else None,
                    after=inp.connector.model_dump(mode="json"),
                    reason=f"the dev's chosen source for {inp.id!r}, copied at compile",
                    actor="dev",
                    t=ctx.now_dt(),
                )
            )
    before_webhooks = {i.id: i.webhook for i in existing.inputs} if existing is not None else {}
    for inp in spec.inputs:
        if inp.id in dev_webhooks and inp.webhook is not None and before_webhooks.get(inp.id) != inp.webhook:
            prev = before_webhooks.get(inp.id)
            ctx.db.add_change(
                Change(
                    id=str(uuid.uuid4()),
                    app_id=ctx.app_id,
                    kind="webhook",
                    target=inp.id,
                    before=prev.model_dump(mode="json") if prev is not None else None,
                    after=inp.webhook.model_dump(mode="json"),
                    reason=f"the dev's webhook mapping for {inp.id!r}, copied at compile",
                    actor="dev",
                    t=ctx.now_dt(),
                )
            )
    return {"app_id": ctx.app_id, "status": "pending_confirmation", "warnings": _spec_warnings(spec)}
