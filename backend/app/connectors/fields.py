"""The fields a pull input's connector implies, declared on the spec (#259).

Events a connector writes go through the same field validation as pushed ones
(`api/ingest._check_fields`): every extra must be a declared field. A connector's events carry
fields of its own (an RSS document's `doc_id` / `confidence`, a preset's or custom JSON source's
readings), so a source added in the Builder with `fields: []` would have every event rejected.

One rule, applied wherever a connector enters a spec (compile, `POST /v1/apps`, `PUT /spec`, the
`create_connector` tool) and by the poller itself (so specs stored before this rule still work):
**a connector's implied fields are declared on its input, optional, unless the dev already
declared a field of that name** (the dev's own declaration always wins and is never changed).
Data only (rule 10); nothing here names a domain (rule 3).
"""

from __future__ import annotations

from app.connectors import json_api
from app.core.models import AppSpec, CompileInput, ConnectorConfig, InputField, InputSpec

# What `connectors/engine._rss_event` writes besides user_id/lat/lon/ts.
RSS_FIELDS = (
    InputField(
        name="doc_id", type="string", required=False, description="Document id from the feed (set by the connector)."
    ),
    InputField(
        name="confidence", type="number", required=False, description="Location confidence 0-1 (set by the connector)."
    ),
)


def implied_fields(conn: ConnectorConfig | None) -> list[InputField]:
    """The fields this connector's events carry. An unknown preset or an invalid custom config
    implies nothing here; it is reported where the connector is checked or polled."""
    if conn is None:
        return []
    if conn.type == "rss":
        return [f.model_copy() for f in RSS_FIELDS]
    if conn.type == "json_api":
        try:
            names = json_api.field_names(conn.preset, conn.config)
        except (KeyError, ValueError):
            return []
        return [
            InputField(name=n, type="number", required=False, description="Reading from the connector's response.")
            for n in names
        ]
    return []


def with_implied(fields: list[InputField], conn: ConnectorConfig | None) -> list[InputField]:
    """`fields` plus every implied field whose name isn't already declared (order kept)."""
    declared = {f.name for f in fields}
    extra = [f for f in implied_fields(conn) if f.name not in declared]
    return [*fields, *extra] if extra else list(fields)


def declare_input(inp: InputSpec) -> InputSpec:
    if inp.kind != "pull" or inp.connector is None:
        return inp
    fields = with_implied(inp.fields, inp.connector)
    return inp if len(fields) == len(inp.fields) else inp.model_copy(update={"fields": fields})


def declare_compile_input(inp: CompileInput) -> CompileInput:
    if inp.kind != "pull" or inp.connector is None:
        return inp
    fields = with_implied(inp.fields, inp.connector)
    return inp if len(fields) == len(inp.fields) else inp.model_copy(update={"fields": fields})


def declare_spec(spec: AppSpec) -> AppSpec:
    """The spec with every pull input's implied connector fields declared."""
    inputs = [declare_input(i) for i in spec.inputs]
    if all(a is b for a, b in zip(inputs, spec.inputs, strict=True)):
        return spec
    return spec.model_copy(update={"inputs": inputs})
