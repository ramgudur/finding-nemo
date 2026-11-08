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
ConnectorType = Literal["rss", "json_api"]  # html is stubbed: rejected here
PoiLabelMode = Literal["auto", "on", "off"]
PoiStatus = Literal["active", "retired"]
AgentName = Literal["compiler", "scout", "critic", "poi_labeler", "message_guard"]
ChangeKind = Literal["weight", "trust", "poi", "connector", "spec_confirm"]
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
    operators: list[OperatorConfig] = Field(default_factory=list)
    plausibility: Plausibility | None = None

    @model_validator(mode="after")
    def _defaults_and_shape(self) -> InputSpec:
        if self.kind == "push" and self.connector is not None:
            raise ValueError(f"push input {self.id!r} must not have a connector")
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

    @model_validator(mode="after")
    def _push_has_no_connector(self) -> CompileInput:
        if self.kind == "push" and self.connector is not None:
            raise ValueError(f"push input {self.id!r} must not have a connector")
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


class CompileStatus(Model):
    """`GET /v1/apps/{app}/compile/status`: public-safe progress of a compile (apps not active only).
    Counts and the name of the last step only; never trace content or tool arguments (rule 8)."""

    app_id: str
    running: bool
    steps: int = 0
    last_action: str | None = Field(
        default=None, description="A registered compiler tool name or a fixed step kind (output, error, ...)."
    )
    started_at: UtcDatetime | None = None


class CompileFailure(Model):
    """`detail` of the 422 from `POST /v1/apps/{app}/compile` when the Compiler never finalized."""

    message: str
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


SourceOptionType = Literal["post_api", "webhook", "rss", "json_api"]  # no HTML scraper (rule 10)


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


class Instruction(Model):
    text: str = Field(max_length=360)
    destination: DestinationPoint | None = Field(default=None, description="None on fallback.")
    route: list[tuple[Lat, Lon]] = Field(default_factory=list, description="[lat, lon] pairs.")
    distance_m: float = Field(default=0, ge=0)
    computed_at: UtcDatetime
    guard: GuardResult


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
