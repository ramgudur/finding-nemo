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

from pydantic import BaseModel, ConfigDict, Field

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
    return warnings


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


def create_connector(ctx: ToolContext, args: CreateConnectorArgs) -> dict[str, Any]:
    spec = _spec(ctx)
    inp = next((i for i in spec.inputs if i.id == args.input_id), None)
    if inp is None:
        raise GuardError(f"input {args.input_id!r} does not exist; create it first")
    if inp.kind != "pull":
        raise GuardError(f"input {args.input_id!r} is {inp.kind!r}; only a pull input takes a connector")
    before = inp.connector.model_dump(mode="json") if inp.connector else None
    config = ConnectorConfig(type=args.type, url=args.url, poll_s=args.poll_s, config=args.config, preset=args.preset)
    new_inputs = [i.model_copy(update={"connector": config}) if i.id == args.input_id else i for i in spec.inputs]
    _save(ctx, spec.model_copy(update={"inputs": new_inputs}))
    ctx.db.add_change(
        Change(
            id=str(uuid.uuid4()),
            app_id=ctx.app_id,
            kind="connector",
            target=args.input_id,
            before=before,
            after=config.model_dump(mode="json"),
            reason=f"{ctx.agent or ctx.actor} configured a connector for {args.input_id!r}",
            actor=ctx.actor,
            t=ctx.now_dt(),
        )
    )
    # connector_id == input_id: each pull input has at most one connector today.
    return {"connector_id": args.input_id, "input_id": args.input_id, "connector": config.model_dump(mode="json")}


def test_connector(ctx: ToolContext, args: TestConnectorArgs) -> dict[str, Any]:
    spec = _spec(ctx)
    inp = next((i for i in spec.inputs if i.id == args.connector_id), None)
    if inp is None:
        raise GuardError(f"connector {args.connector_id!r} does not exist")
    if inp.connector is None:
        raise GuardError(f"input {args.connector_id!r} has no connector configured yet")
    # connectors/engine.py (the live poller, #80) isn't built yet: this validates the
    # config's shape only. It never claims to have fetched anything.
    return {
        "connector_id": args.connector_id,
        "config_valid": True,
        "detail": "connector config is valid; live-fetch testing lands with the connector engine",
    }


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
    try:
        spec = AppSpec.model_validate(args.spec)
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
    _save(ctx, spec.model_copy(update={"status": "pending_confirmation"}))
    return {"app_id": ctx.app_id, "status": "pending_confirmation", "warnings": _spec_warnings(spec)}
