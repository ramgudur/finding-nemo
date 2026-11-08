"""Source of truth for the platform's contracts (Pydantic v2).

When this file changes, update `frontend/src/api/types.ts`, `specs/*.json` and the
"Core contracts" section of CLAUDE.md in the same commit. Changes need lead approval.

Conventions: meters, seconds, UTC ISO-8601 datetimes. Coordinates are always named
`lat`/`lon` fields; a bare pair (e.g. `Instruction.route`) is `[lat, lon]`.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Lat = float
Lon = float

Visibility = Literal["public", "authority"]
Tier = Literal["locked", "stiff", "flexible"]
InputKind = Literal["push", "pull"]
InputShape = Literal["point", "track", "fixed_point", "field", "document"]
AppStatus = Literal["draft", "pending_confirmation", "active"]
VerificationMethod = Literal["authority_confirmation", "predictive_check", "cross_input_agreement"]
FieldType = Literal["number", "integer", "string", "boolean"]
ConnectorType = Literal["rss", "json_api", "json_list"]  # html is stubbed: rejected here
PoiLabelMode = Literal["auto", "on", "off"]
PoiStatus = Literal["active", "retired"]
LabelProposalStatus = Literal["open", "accepted", "dismissed"]
AgentName = Literal["compiler", "scout", "critic", "poi_labeler", "message_guard"]
ChangeKind = Literal["weight", "trust", "poi", "connector", "webhook", "spec_confirm", "spec_edit"]
Actor = Literal["agent", "dev"]
Device = Literal["cpu", "gpu"]

SLUG = r"^[a-z0-9][a-z0-9_-]{0,63}$"


def _to_utc(d: datetime) -> datetime:
    return d.astimezone(UTC)


# Naive datetimes are rejected; aware ones are normalized to UTC.
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- AppSpec ---------------------------------------------------------------------


class BBox(Model):
    min_lat: Lat = Field(ge=-90, le=90)
    min_lon: Lon = Field(ge=-180, le=180)
    max_lat: Lat = Field(ge=-90, le=90)
    max_lon: Lon = Field(ge=-180, le=180)

    @model_validator(mode="after")
    def _ordered(self) -> BBox:
        if self.min_lat >= self.max_lat or self.min_lon >= self.max_lon:
            raise ValueError("bbox min must be below max")
        return self

    def as_geojson(self) -> tuple[Lon, Lat, Lon, Lat]:
        """(min_lon, min_lat, max_lon, max_lat): the order `engine/grid.Grid(bbox=...)` takes."""
        return (self.min_lon, self.min_lat, self.max_lon, self.max_lat)


MAX_GRID_CELLS = 250_000  # resource limit (e.g. 500 x 500): every tick and backtest scales with cell count
_EARTH_RADIUS_M = 6_371_008.8  # same as engine/grid.py, so the count matches the engine's grid shape


class GridSpec(Model):
    cell_m: float = Field(gt=0)
    bbox: BBox

    def shape(self) -> tuple[int, int]:
        """(rows, cols) the engine's Grid will have for this spec."""
        b = self.bbox
        m_per_deg = _EARTH_RADIUS_M * math.pi / 180.0
        height = (b.max_lat - b.min_lat) * m_per_deg
        width = (b.max_lon - b.min_lon) * m_per_deg * math.cos(math.radians((b.min_lat + b.max_lat) / 2))
        return max(1, math.ceil(height / self.cell_m - 1e-9)), max(1, math.ceil(width / self.cell_m - 1e-9))

    @model_validator(mode="after")
    def _size_limit(self) -> GridSpec:
        h, w = self.shape()
        if h * w > MAX_GRID_CELLS:
            raise ValueError(
                f"grid would be {h} x {w} cells; the limit is {MAX_GRID_CELLS:,}. Use a smaller area or bigger cells."
            )
        return self


class InputField(Model):
    """One dev-declared field an event for this input carries, besides user_id/lat/lon/ts."""

    name: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    type: FieldType
    required: bool = True
    description: str = ""


class OperatorConfig(BaseModel):
    """One operator applied to an input. Extra keys are the operator's own params,
    validated by the params model in `engine/operators/registry.py`."""

    model_config = ConfigDict(extra="allow")

    op: str
    weight: float = Field(ge=0)
    noise_floor: float | None = Field(default=None, gt=0)
    layer_id: str = Field(default="", description="Defaults to '<input_id>.<op>'.")

    def params(self) -> dict[str, Any]:
        """The operator's own params (extras, plus noise_floor when set): what `registry.run` takes."""
        out = dict(self.model_extra or {})
        if self.noise_floor is not None:
            out["noise_floor"] = self.noise_floor
        return out


class ConnectorConfig(Model):
    """Pull-input connector. Config only: never code."""

    type: ConnectorType
    url: str | None = None
    poll_s: float = Field(default=300, gt=0)
    config: dict[str, Any] = Field(default_factory=dict)
    preset: str | None = Field(
        default=None, description="Name of a ready-made config in the connectors/presets/ catalogue."
    )


TsFormat = Literal["iso", "epoch_s", "epoch_ms"]


class WebhookMapping(Model):
    """Push-input webhook: data only (rule 10), never code. Every path is a dotted field name with
    optional list indices (e.g. "geometry.coordinates.1"), read from one JSON item at a time."""

    items_path: str = Field(default="", description='Path to a list or one object; "" is the body itself.')
    lat: str = Field(min_length=1)
    lon: str = Field(min_length=1)
    ts: str | None = Field(default=None, description="None means the item's receive time.")
    ts_format: TsFormat = "iso"
    user_id: str | None = None
    fields: dict[str, str] = Field(default_factory=dict, description="Declared field name -> path.")


class Plausibility(Model):
    """Per-input bounds for the spoof checks in `engine/trust.py` (tracks/points only).

    The Compiler proposes these from the input's `meaning`: what speed and how straight a track is
    plausible depends on what the entities are. Omitted values use `engine/trust.py`'s neutral defaults.
    """

    max_speed_mps: float | None = Field(default=None, gt=0)
    jitter_m: float | None = Field(default=None, ge=0)
    flag_constant_speed: bool | None = None
    flag_straight_line: bool | None = None


class InputSpec(Model):
    id: str = Field(pattern=SLUG)
    kind: InputKind
    shape: InputShape
    tier: Tier
    visibility: Visibility = "authority"
    meaning: str = Field(description="The dev's words, verbatim.")
    fields: list[InputField] = Field(default_factory=list)
    connector: ConnectorConfig | None = None
    webhook: WebhookMapping | None = None
    operators: list[OperatorConfig] = Field(default_factory=list)
    plausibility: Plausibility | None = None

    @model_validator(mode="after")
    def _defaults_and_shape(self) -> InputSpec:
        if self.kind == "push" and self.connector is not None:
            raise ValueError(f"push input {self.id!r} must not have a connector")
        if self.kind == "pull" and self.webhook is not None:
            raise ValueError(f"pull input {self.id!r} must not have a webhook mapping")
        for o in self.operators:
            if not o.layer_id:
                o.layer_id = f"{self.id}.{o.op}"
        names = [f.name for f in self.fields]
        if len(names) != len(set(names)):
            raise ValueError(f"input {self.id!r} has duplicate field names")
        return self


class CompileInput(Model):
    """One raw input the dev hands the Compiler: everything about it except tier,
    operators and plausibility, which are the Compiler's own job to propose."""

    id: str = Field(pattern=SLUG)
    kind: InputKind
    shape: InputShape
    meaning: str = Field(description="The dev's words, verbatim.")
    fields: list[InputField] = Field(default_factory=list)
    connector: ConnectorConfig | None = Field(
        default=None,
        description="Pull only: the dev's chosen connector (URL or preset). Copied verbatim into the spec.",
    )
    webhook: WebhookMapping | None = Field(
        default=None, description="Push only: the dev's webhook mapping. Copied verbatim into the spec."
    )

    @model_validator(mode="after")
    def _push_has_no_connector(self) -> CompileInput:
        if self.kind == "push" and self.connector is not None:
            raise ValueError(f"push input {self.id!r} must not have a connector")
        if self.kind == "pull" and self.webhook is not None:
            raise ValueError(f"pull input {self.id!r} must not have a webhook mapping")
        return self


class CompileRequest(Model):
    """Body of `POST /v1/apps/{app}/compile`."""

    intent: str
    grid: GridSpec
    inputs: list[CompileInput] = Field(default_factory=list)
    destination_points: list[DestinationPoint] = Field(
        default_factory=list,
        description="Fixed destinations the dev picked on the map. Copied verbatim into routing.destination_points.",
    )


class ValidateResult(Model):
    """Errors and warnings from the platform's spec checks, as plain strings."""

    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CompileStatus(Model):
    """`GET /v1/apps/{app}/compile/status`: public-safe progress of a compile (apps not active only).
    Never trace text or tool arguments (rule 8)."""

    running: bool
    step: int = Field(default=0, description="Compiler trace entries (model steps) since this compile started.")
    elapsed_s: float = Field(default=0.0, description="Seconds since this compile started; 0 when not running.")
    last_validate: ValidateResult | None = Field(
        default=None, description="Errors/warnings of the last spec passed to validate_spec or finalize_app."
    )


class CompileFailure(Model):
    """`detail` of the 422 from `POST /v1/apps/{app}/compile` when the Compiler never finalized."""

    reason: str
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


SourceOptionType = Literal["post_api", "webhook", "rss", "json_api", "json_list"]  # no HTML scraper (rule 10)


class SuggestSourceRequest(Model):
    """Body of `POST /v1/apps/{app}/sources/suggest`: the dev describes one source in plain words."""

    intent: str = Field(max_length=4000)
    description: str = Field(min_length=1, max_length=2000)


class SourceOption(Model):
    """One way to connect a source. `config` carries pull settings: `{"preset": name}` for a
    `json_api` preset, `{"url": ...}` only when the dev's own description gave that URL."""

    type: SourceOptionType
    label: str
    why: str
    config: dict[str, Any] | None = None


class OperatorInfo(Model):
    """One entry of `GET /v1/operators`: the public operator catalogue (no app data)."""

    name: str
    description: str
    shapes: list[InputShape]
    role: Literal["layer", "override"]
    params: dict[str, Any] = Field(description="JSON schema of the operator's params.")


class SourceSuggestion(Model):
    """Response of `POST /v1/apps/{app}/sources/suggest`. `options` are ranked best first; when the
    model's answer could not be used, they are the full catalogue, unranked. `meaning` is the
    dev's description, verbatim."""

    kind: InputKind
    shape: InputShape
    options: list[SourceOption]
    meaning: str


class VerificationSpec(Model):
    method: VerificationMethod
    priority: int = Field(ge=1, description="1 is the highest priority; unique within a spec.")


class OutputLayer(Model):
    id: str = Field(description="A layer_id from inputs[].operators[], or 'score'.")
    visibility: Visibility = "authority"


class PoiLabel(Model):
    label: str
    sensitive: bool = False
    destination: bool = False

    @model_validator(mode="after")
    def _not_both(self) -> PoiLabel:
        if self.sensitive and self.destination:
            raise ValueError(f"label {self.label!r} cannot be both sensitive and a destination")
        return self


class PoiLabelConfig(Model):
    mode: PoiLabelMode = "auto"
    label_set: list[PoiLabel] = Field(default_factory=list)
    min_confidence: float = Field(default=0.6, ge=0, le=1)


class Outputs(Model):
    layers: list[OutputLayer] = Field(default_factory=list)
    poi_labels: PoiLabelConfig = Field(default_factory=PoiLabelConfig)
    hotspot_threshold: float = Field(
        default=0.5, gt=0, le=1, description="Score at or above which connected cells form a hotspot."
    )


class DestinationPoint(Model):
    lat: Lat = Field(ge=-90, le=90)
    lon: Lon = Field(ge=-180, le=180)
    label: str


class RoutingSpec(Model):
    enabled: bool = False
    destination_points: list[DestinationPoint] = Field(default_factory=list)
    impassable_score: float = Field(default=0.8, gt=0, le=1)
    alpha: float = Field(default=4.0, ge=0)
    fallback_text: str = Field(default="No route available.", max_length=360)
    rephrase: bool = False


class AppSpec(Model):
    app_id: str = Field(pattern=SLUG)
    intent: str
    status: AppStatus = "draft"
    grid: GridSpec
    inputs: list[InputSpec] = Field(default_factory=list)
    verification: list[VerificationSpec] = Field(default_factory=list)
    outputs: Outputs = Field(default_factory=Outputs)
    routing: RoutingSpec = Field(default_factory=RoutingSpec)

    def layer_ids(self) -> list[str]:
        return [o.layer_id for i in self.inputs for o in i.operators]

    @model_validator(mode="after")
    def _references(self) -> AppSpec:
        ids = [i.id for i in self.inputs]
        if len(ids) != len(set(ids)):
            raise ValueError("input ids must be unique")
        layers = self.layer_ids()
        if len(layers) != len(set(layers)):
            raise ValueError("layer ids must be unique")
        known = set(layers) | {"score"}
        for out in self.outputs.layers:
            if out.id not in known:
                raise ValueError(f"outputs.layers references unknown layer {out.id!r}")
        methods = [v.method for v in self.verification]
        if len(methods) != len(set(methods)):
            raise ValueError("verification methods must be unique")
        priorities = [v.priority for v in self.verification]
        if len(priorities) != len(set(priorities)):
            raise ValueError("verification priorities must be unique")
        labels = [p.label for p in self.outputs.poi_labels.label_set]
        if len(labels) != len(set(labels)):
            raise ValueError("POI labels must be unique")
        return self


class SpecEditResponse(Model):
    """Response of `PUT /v1/apps/{app}/spec`: the saved spec (still `pending_confirmation`) and
    the `validate_spec` warnings it carries. Errors are a `422` with the full list instead."""

    spec: AppSpec
    warnings: list[str] = Field(default_factory=list)


# --- Ingest ----------------------------------------------------------------------


class Event(BaseModel):
    """Push event body for `POST /v1/apps/{app}/inputs/{input}/events` (header `X-Ingest-Key`).
    Extra keys are the input's declared `fields`, validated against the AppSpec at ingest."""

    model_config = ConfigDict(extra="allow")

    user_id: str | None = None
    lat: Lat = Field(ge=-90, le=90)
    lon: Lon = Field(ge=-180, le=180)
    ts: UtcDatetime


# --- Engine outputs --------------------------------------------------------------


class Candidate(Model):
    id: str
    lat: Lat
    lon: Lon
    radius_m: float = Field(ge=0)
    source: str = Field(description="Name of the candidate generator.")
    stats: dict[str, float] = Field(default_factory=dict)
    contributing_layers: list[str] = Field(default_factory=list)
    persisted_ticks: int = Field(default=0, ge=0)


class Evidence(Model):
    kind: str = Field(description="Evidence source, e.g. 'osm_feature', 'layer_stats', 'input_values', 'time_pattern'.")
    text: str
    data: dict[str, Any] = Field(default_factory=dict)


class POI(Model):
    id: str
    candidate_id: str
    lat: Lat
    lon: Lon
    radius_m: float = Field(ge=0)
    label: str
    confidence: float = Field(ge=0, le=1)
    evidence: list[Evidence] = Field(default_factory=list)
    visibility: Visibility = "authority"
    status: PoiStatus = "active"
    created_at: UtcDatetime
    updated_at: UtcDatetime


class LabelProposal(Model):
    """A new POI label type the POI Labeler proposed (`propose_label`). It changes nothing until the dev
    accepts it (`POST /label-proposals/{id}/accept`), which adds it to the active spec's `label_set`.
    `why` is agent text: authority view only (rule 8)."""

    id: str
    label: str = Field(min_length=1, max_length=60)
    sensitive: bool = False
    destination: bool = False
    why: str = Field(max_length=500)
    status: LabelProposalStatus = "open"
    t: UtcDatetime


class LabelProposalAccept(Model):
    """Optional body of `POST /label-proposals/{id}/accept`: override the proposed flags."""

    sensitive: bool | None = None
    destination: bool | None = None


class LabelProposalAcceptResponse(Model):
    proposal: LabelProposal
    label_set: list[PoiLabel]


class Backtest(Model):
    id: str
    kind: Literal["single", "batch"]
    candidates_tested: int = Field(ge=0)
    candidates_valid: int = Field(ge=0)
    train_scores: dict[VerificationMethod, float] = Field(default_factory=dict)
    holdout_scores: dict[VerificationMethod, float] = Field(default_factory=dict)
    passed: bool
    device: Device
    elapsed_ms: float = Field(ge=0)
    t: UtcDatetime
    # What the candidate was compared against: the current weights/trust scored on the same
    # holdout, and the method that decided acceptance. Empty / None on backtests stored before
    # these were recorded, and when the window had too little data to compare.
    baseline_holdout_scores: dict[VerificationMethod, float] = Field(default_factory=dict)
    deciding_method: VerificationMethod | None = None


class BacktestDetail(Backtest):
    """`GET /v1/apps/{app}/backtests/{id}` (authority only): the stored Backtest plus the holdout
    comparison for its deciding method, derived from the stored scores. The three derived fields
    are None when the baseline wasn't recorded or the deciding method has no score on one side."""

    holdout_baseline: float | None = None
    holdout_candidate: float | None = None
    holdout_delta: float | None = Field(default=None, description="holdout_candidate - holdout_baseline")

    @classmethod
    def from_backtest(cls, bt: Backtest) -> BacktestDetail:
        m = bt.deciding_method
        base = bt.baseline_holdout_scores.get(m) if m else None
        cand = bt.holdout_scores.get(m) if m else None
        delta = cand - base if base is not None and cand is not None else None
        return cls(**bt.model_dump(), holdout_baseline=base, holdout_candidate=cand, holdout_delta=delta)


class DistrustedUser(Model):
    """`GET /v1/apps/{app}/trust/lowered` (authority only, rule 8: it carries users' positions).
    One per user whose trust is below the default (1.0). The position is the user's latest event
    in any input of the app; None when no event of theirs is stored any more."""

    user_id: str
    input_id: str | None = None
    trust: float = Field(ge=0, le=1)
    reason: str = ""
    lowered_at: UtcDatetime
    last_lat: Lat | None = None
    last_lon: Lon | None = None
    last_seen: UtcDatetime | None = None


class ToolCall(Model):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    result_summary: str = ""


class TraceEntry(Model):
    t: UtcDatetime
    app_id: str
    agent: AgentName
    model: str | None = None
    saw: str = ""
    hypothesis: str = ""
    tested: str = ""
    result: str = ""
    action: str = ""
    why: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    skipped_reason: str | None = None


class Change(Model):
    id: str
    app_id: str
    kind: ChangeKind
    target: str = Field(description="What changed, e.g. a layer_id, user_id, POI id or connector id.")
    before: Any = None
    after: Any = None
    reason: str
    backtest_id: str | None = None
    actor: Actor
    t: UtcDatetime
    reverts: str | None = Field(
        default=None, description="Id of the Change this one reverts (POST /changes/{id}/revert); None otherwise."
    )


# --- End-user instructions -------------------------------------------------------


class InstructionRequest(Model):
    """Body of `POST /v1/apps/{app}/instruction`: the caller's own position only."""

    lat: Lat = Field(ge=-90, le=90)
    lon: Lon = Field(ge=-180, le=180)


class GuardCheck(Model):
    name: str
    passed: bool
    detail: str = ""


class GuardResult(Model):
    passed: bool
    checks: list[GuardCheck] = Field(default_factory=list)


InstructionMode = Literal["route", "escape", "nearest_safe", "fallback"]


class Instruction(Model):
    text: str = Field(max_length=360)
    destination: DestinationPoint | None = Field(default=None, description="None on fallback.")
    route: list[tuple[Lat, Lon]] = Field(default_factory=list, description="[lat, lon] pairs.")
    distance_m: float = Field(default=0, ge=0)
    computed_at: UtcDatetime
    guard: GuardResult
    mode: InstructionMode | None = Field(
        default=None,
        description="route: to a destination; escape: the route starts by leaving the blocked area the caller "
        "is in; nearest_safe: no destination within a reasonable distance, so to the nearest cell clear of "
        "danger; fallback: nothing reachable (fallback_text).",
    )
    walk_minutes: int | None = Field(default=None, ge=0, description="distance_m at about 1.3 m/s walking pace.")


# --- Outputs (GET /map?format=geojson, GET /hotspots) ----------------------------

GeoJsonBBox = tuple[Lon, Lat, Lon, Lat]


class CellProperties(Model):
    value: float
    row: int
    col: int


class CellFeature(Model):
    type: Literal["Feature"] = "Feature"
    geometry: dict[str, Any] = Field(description='{"type": "Polygon", "coordinates": [[lon, lat], ...]}')
    properties: CellProperties


class CellCollection(Model):
    """One layer's cells. Cells with value < 0.01 are omitted: a missing cell means 0."""

    type: Literal["FeatureCollection"] = "FeatureCollection"
    bbox: GeoJsonBBox | None = None
    features: list[CellFeature] = Field(default_factory=list)


class MapResponse(Model):
    """Without the authority key, `layers` holds only public layers. 'score' is the combined map."""

    app_id: str
    t: UtcDatetime
    cell_m: float
    bbox: GeoJsonBBox
    layers: dict[str, CellCollection] = Field(default_factory=dict)


class Hotspot(Model):
    """`score` is the peak cell score; `radius_m` = sqrt(area_m2 / pi)."""

    id: str
    lat: Lat
    lon: Lon
    radius_m: float = Field(ge=0)
    score: float
    mean: float
    area_m2: float = Field(ge=0)
    n_cells: int = Field(ge=0)
    persisted_ticks: int = Field(ge=0)
    contributing_layers: list[str] = Field(default_factory=list)
    visibility: Visibility


class HotspotsResponse(Model):
    app_id: str
    t: UtcDatetime
    hotspots: list[Hotspot] = Field(default_factory=list)


# --- Dev-requested Scout (POST /v1/apps/{app}/scout) ----------------------------------


class ScoutRequest(Model):
    """Body of `POST /v1/apps/{app}/scout` (authority key): ask the Scout to find sources for one
    pull input. The text goes only into the authority-only trace and is never echoed."""

    input_id: str = Field(min_length=1)
    request: str = Field(min_length=1, max_length=500, description="Plain-text request to the Scout.")


class ScoutJobAccepted(Model):
    """202 response: the request is queued; the Scout runs asynchronously on the shared rate budget."""

    job_id: str
    app_id: str
    input_id: str


# --- Webhook receiver (POST /v1/apps/{app}/webhooks/{input}) -------------------------


class WebhookRejection(Model):
    index: int
    reason: str


class WebhookReceipt(Model):
    """202 response: each mapped item was validated like one `/events` body. The raw request
    body is never stored (rule 8) -- only these per-item outcomes."""

    received: int
    accepted: int
    rejected: list[WebhookRejection] = Field(default_factory=list, description="First 20 rejections only.")


# --- Simulate (BE3, #268): POST /v1/apps/{app}/simulate (authority key) -------------

SimulateMode = Literal["normal", "spoof"]


class SimulateRequest(Model):
    """Body of `POST /v1/apps/{app}/simulate`: generate test events for one push input. They go
    through the same validation and storage as `/events` and carry a `sim-` / `spoof-` user_id
    prefix so they stay identifiable."""

    input_id: str = Field(min_length=1, description="A push input of the app (else 422).")
    count: int = Field(ge=1, le=200)
    lat: Lat = Field(ge=-90, le=90, description="Centre; must be inside the app's area (else 422).")
    lon: Lon = Field(ge=-180, le=180)
    radius_m: float = Field(ge=10, le=5000)
    mode: SimulateMode = "normal"


class SimulateResult(Model):
    """202 response: how many generated events were stored, and the user_id prefix they carry."""

    accepted: int = Field(ge=0)
    rejected: int = Field(ge=0)
    user_id_prefix: str


# --- Public app info and smarter routing (#266, BE1) ----------------------------------
# `Instruction.mode` / `Instruction.walk_minutes` (above) are part of the same change.


class PublicSource(Model):
    """One source as the public sees it: an input with a public output layer (label = its meaning,
    cut to 80 chars), or a count of the private ones of one kind ("N private sources")."""

    kind: InputKind
    label: str


class PublicAppInfo(Model):
    """`GET /v1/apps/{app}/public` (no key): what the end-user page may show about an app (rule 8)."""

    app_id: str
    title: str = Field(description="From app_id: hyphens/underscores become spaces, words title-cased.")
    intent: str
    status: AppStatus
    routing_enabled: bool
    bbox: BBox
    last_update: UtcDatetime | None = Field(default=None, description="Time of the app's last engine tick.")
    sources: list[PublicSource] = Field(default_factory=list)


# --- Source status (GET /v1/apps/{app}/sources/status, #267, BE2) ---------------------

SourceVia = Literal["post_api", "webhook", "rss", "json_api", "json_list"]


class SourceStatus(Model):
    """One input's delivery health for the console (authority key only, rule 8). `last_status` is
    the connector's last poll: "ok" or "error: <reason>", never a URL or a response body."""

    input_id: str
    kind: InputKind
    via: SourceVia | None = Field(description="Null only for a pull input with no connector yet.")
    poll_s: int | None = None
    last_polled_at: UtcDatetime | None = None
    next_poll_at: UtcDatetime | None = Field(default=None, description="last_polled_at + poll_s.")
    last_status: str | None = None
    events_total: int = 0
    events_last_hour: int = 0
    last_event_at: UtcDatetime | None = None
