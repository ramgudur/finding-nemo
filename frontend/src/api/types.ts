// Mirrors backend/app/core/models.py. Update both together (lead approves contract changes).
// Datetimes are ISO-8601 strings with a timezone (naive times are rejected; the server returns UTC).
// A bare coordinate pair is [lat, lon].

export type Visibility = "public" | "authority";
export type Tier = "locked" | "stiff" | "flexible";
export type InputKind = "push" | "pull";
export type InputShape = "point" | "track" | "fixed_point" | "field" | "document";
export type AppStatus = "draft" | "pending_confirmation" | "active";
export type VerificationMethod = "authority_confirmation" | "predictive_check" | "cross_input_agreement";
export type FieldType = "number" | "integer" | "string" | "boolean";
export type ConnectorType = "rss" | "json_api" | "json_list"; // json_list: contract E (a fetched JSON / GeoJSON list)
export type PoiLabelMode = "auto" | "on" | "off";
export type PoiStatus = "active" | "retired";
export type AgentName = "compiler" | "scout" | "critic" | "poi_labeler" | "message_guard";
export type ChangeKind = "weight" | "trust" | "poi" | "connector" | "webhook" | "spec_confirm" | "spec_edit";
export type Actor = "agent" | "dev";
export type Device = "cpu" | "gpu";
export type IsoDateTime = string;
export type LatLon = [lat: number, lon: number];

// --- AppSpec ---

export interface BBox {
  min_lat: number;
  min_lon: number;
  max_lat: number;
  max_lon: number;
}

/** Rows x cols (equirectangular, as engine/grid.py) must be <= MAX_GRID_CELLS or the server rejects it. */
export interface GridSpec {
  cell_m: number;
  bbox: BBox;
}

export const MAX_GRID_CELLS = 250_000;

export interface InputField {
  name: string;
  type: FieldType;
  required?: boolean; // default true
  description?: string;
}

/** Extra keys are the operator's own params. */
export interface OperatorConfig {
  op: string;
  weight: number;
  noise_floor?: number | null;
  layer_id?: string; // defaults to "<input_id>.<op>"
  [param: string]: unknown;
}

export interface ConnectorConfig {
  type: ConnectorType;
  url?: string | null;
  poll_s?: number; // default 300
  config?: Record<string, unknown>;
  preset?: string | null; // a ready-made config under connectors/presets/
}

/** Per-input bounds for spoof checks; omitted values use the engine defaults. */
export interface Plausibility {
  max_speed_mps?: number | null;
  jitter_m?: number | null;
  flag_constant_speed?: boolean | null;
  flag_straight_line?: boolean | null;
}

export interface InputSpec {
  id: string;
  kind: InputKind;
  shape: InputShape;
  tier: Tier;
  visibility?: Visibility; // default "authority"
  meaning: string;
  fields?: InputField[];
  connector?: ConnectorConfig | null;
  webhook?: WebhookMapping | null; // push only
  operators?: OperatorConfig[];
  plausibility?: Plausibility | null;
}

export type TsFormat = "iso" | "epoch_s" | "epoch_ms";

/** Push-input webhook mapping: data only (rule 10). Paths are dotted field names with optional list indices. */
export interface WebhookMapping {
  items_path?: string; // "" = the body itself
  lat: string;
  lon: string;
  ts?: string | null; // null = the item's receive time
  ts_format?: TsFormat; // default "iso"
  user_id?: string | null;
  fields?: Record<string, string>; // declared field name -> path
}

/** 202 from POST /v1/apps/{app}/webhooks/{input}. */
export interface WebhookReceipt {
  received: number;
  accepted: number;
  rejected: { index: number; reason: string }[]; // first 20 only
}

export interface VerificationSpec {
  method: VerificationMethod;
  priority: number; // 1 is highest; unique within a spec
}

export interface OutputLayer {
  id: string; // a layer_id or "score"
  visibility?: Visibility; // default "authority"
}

export interface PoiLabel {
  label: string;
  sensitive?: boolean;
  destination?: boolean; // never both sensitive and destination
}

export interface PoiLabelConfig {
  mode?: PoiLabelMode; // default "auto"
  label_set?: PoiLabel[];
  min_confidence?: number; // default 0.6
}

export interface Outputs {
  layers?: OutputLayer[];
  poi_labels?: PoiLabelConfig;
  hotspot_threshold?: number; // default 0.5, in (0, 1]
}

export interface DestinationPoint {
  lat: number;
  lon: number;
  label: string;
}

export interface RoutingSpec {
  enabled?: boolean;
  destination_points?: DestinationPoint[];
  impassable_score?: number; // default 0.8
  alpha?: number; // default 4.0
  fallback_text?: string; // <= 360 chars; default "No route available."
  rephrase?: boolean; // default false
}

export interface AppSpec {
  app_id: string;
  intent: string;
  status?: AppStatus; // default "draft"
  grid: GridSpec;
  inputs?: InputSpec[];
  verification?: VerificationSpec[];
  outputs?: Outputs;
  routing?: RoutingSpec;
}

// --- Ingest ---

/** Extra keys are the input's declared fields. */
export interface Event {
  user_id?: string | null;
  lat: number;
  lon: number;
  ts: IsoDateTime;
  [field: string]: unknown;
}

// --- Engine outputs ---

export interface Candidate {
  id: string;
  lat: number;
  lon: number;
  radius_m: number;
  source: string;
  stats: Record<string, number>;
  contributing_layers: string[];
  persisted_ticks: number;
}

export interface Evidence {
  kind: string;
  text: string;
  data: Record<string, unknown>;
}

export interface POI {
  id: string;
  candidate_id: string;
  lat: number;
  lon: number;
  radius_m: number;
  label: string;
  confidence: number;
  evidence: Evidence[];
  visibility: Visibility;
  status: PoiStatus;
  created_at: IsoDateTime;
  updated_at: IsoDateTime;
}

export interface Backtest {
  id: string;
  kind: "single" | "batch";
  candidates_tested: number;
  candidates_valid: number;
  train_scores: Partial<Record<VerificationMethod, number>>;
  holdout_scores: Partial<Record<VerificationMethod, number>>;
  passed: boolean;
  device: Device;
  elapsed_ms: number;
  t: IsoDateTime;
  /** The current weights/trust scored on the same holdout; {} on backtests stored before it was recorded. */
  baseline_holdout_scores?: Partial<Record<VerificationMethod, number>>;
  /** The method that decided acceptance; null when none had holdout data (or not recorded). */
  deciding_method?: VerificationMethod | null;
}

/** GET /v1/apps/{app}/backtests/{id} (authority only): the Backtest plus its deciding method's holdout comparison. */
export interface BacktestDetail extends Backtest {
  holdout_baseline: number | null;
  holdout_candidate: number | null;
  /** holdout_candidate - holdout_baseline; null when not recoverable. */
  holdout_delta: number | null;
}

/** GET /v1/apps/{app}/trust/lowered (authority only, rule 8): a user whose trust is below 1.0, at their latest event. */
export interface DistrustedUser {
  user_id: string;
  input_id: string | null;
  trust: number;
  reason: string;
  lowered_at: IsoDateTime;
  last_lat: number | null;
  last_lon: number | null;
  last_seen: IsoDateTime | null;
}

export interface ToolCall {
  name: string;
  args: Record<string, unknown>;
  ok: boolean;
  result_summary: string;
}

export interface TraceEntry {
  t: IsoDateTime;
  app_id: string;
  agent: AgentName;
  model: string | null;
  saw: string;
  hypothesis: string;
  tested: string;
  result: string;
  action: string;
  why: string;
  tool_calls: ToolCall[];
  skipped_reason: string | null;
}

export interface Change {
  id: string;
  app_id: string;
  kind: ChangeKind;
  target: string;
  before: unknown;
  after: unknown;
  reason: string;
  backtest_id: string | null;
  actor: Actor;
  t: IsoDateTime;
}

// --- End-user instructions ---

export interface InstructionRequest {
  lat: number;
  lon: number;
}

export interface GuardCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface GuardResult {
  passed: boolean;
  checks: GuardCheck[];
}

export interface Instruction {
  text: string; // <= 360 chars
  destination: DestinationPoint | null; // null on fallback
  route: LatLon[];
  distance_m: number;
  computed_at: IsoDateTime;
  guard: GuardResult;
  // Smarter routing (end-user batch, contract D). Optional: older backends don't send them.
  mode?: InstructionMode | null;
  walk_minutes?: number | null; // at about 1.3 m/s walking pace
}

// --- Outputs (GET /map?format=geojson, GET /hotspots) ---

/** GeoJSON order: [min_lon, min_lat, max_lon, max_lat]. */
export type GeoJsonBBox = [number, number, number, number];

/** One grid cell. Cells with value < 0.01 are omitted: a missing cell means 0. */
export interface CellFeature {
  type: "Feature";
  geometry: { type: "Polygon"; coordinates: number[][][] }; // [lon, lat] rings
  properties: { value: number; row: number; col: number };
}

export interface CellCollection {
  type: "FeatureCollection";
  bbox?: GeoJsonBBox;
  features: CellFeature[];
}

/** Without the authority key, `layers` holds only public layers. "score" is the combined map. */
export interface MapResponse {
  app_id: string;
  t: IsoDateTime;
  cell_m: number;
  bbox: GeoJsonBBox;
  layers: Record<string, CellCollection>;
}

/** `score` is the peak cell score; radius_m = sqrt(area_m2 / pi). */
export interface Hotspot {
  id: string;
  lat: number;
  lon: number;
  radius_m: number;
  score: number;
  mean: number;
  area_m2: number;
  n_cells: number;
  persisted_ticks: number;
  contributing_layers: string[];
  visibility: Visibility;
}

export interface HotspotsResponse {
  app_id: string;
  t: IsoDateTime;
  hotspots: Hotspot[];
}

// --- WS /v1/apps/{app}/stream ---
// Client -> server, first frame only: {type: "auth", key}. Frames are filtered like REST.

export interface StreamAuthFrame {
  type: "auth";
  key: string;
}

export type StreamFrame =
  | { type: "auth_ok"; authority: boolean }
  | { type: "tick"; t: IsoDateTime; map: MapResponse; hotspots: HotspotsResponse }
  | { type: "error"; reason: string };

// --- Debug (DEMO_MODE only; 404 otherwise) ---

export interface ClockState {
  now: IsoDateTime;
  now_s: number;
  paused: boolean;
  speed: number | null; // sim seconds per wall second; null when paused
  controllable: boolean; // a SimClock is installed
}

export interface ClockControl {
  paused?: boolean;
  speed?: number; // (0, 20]
}

export interface ErrorPoint {
  t: IsoDateTime;
  hotspot_error_m: number | null; // top hotspot to the true source
  point_error_m: number | null; // best operator point estimate to the true source
}

export interface ChangeMarker {
  t: IsoDateTime;
  id: string;
  kind: string;
  target: string;
  actor: string;
  reason: string;
}

export interface ErrorSeries {
  app_id: string;
  truth_file: string | null;
  series: ErrorPoint[];
  changes: ChangeMarker[];
}

// --- Console reads (authority only; api/trace.py) ---

export interface LayerWeight {
  layer_id: string;
  weight: number;
  tier: Tier;
  pinned: boolean;
}

export interface WeightsView {
  app_id: string;
  t: IsoDateTime;
  layers: LayerWeight[];
  flexible_total: number; // renormalization keeps it fixed
}

export interface MethodScore {
  method: VerificationMethod;
  priority: number;
  has_data: boolean;
  value: number | null;
}

export interface VerificationView {
  app_id: string;
  window_s: number;
  ticks: number;
  methods: MethodScore[];
  cold_start: boolean; // fewer than two methods have data
}

export interface PinRequest {
  pinned?: boolean; // default true
  reason?: string;
}

// --- Builder: compile + activate (api/apps.py) ---

/** One raw input for the Compiler; it proposes tier, operators and plausibility itself. */
export interface CompileInput {
  id: string;
  kind: InputKind;
  shape: InputShape;
  meaning: string; // the dev's words, verbatim
  fields?: InputField[];
  /** Pull only: the dev's chosen source (URL or preset), copied verbatim into the spec. */
  connector?: ConnectorConfig | null;
  /** Push only: the dev's webhook mapping, copied verbatim into the spec. */
  webhook?: WebhookMapping | null;
}

/** "Add a source" option types. No HTML scraper (rule 10). */
export type SourceOptionType = "post_api" | "webhook" | "rss" | "json_api" | "json_list";

/** Body of POST /v1/apps/{app}/sources/suggest. */
export interface SuggestSourceRequest {
  intent: string;
  description: string;
}

/** config: {preset} for a json_api preset; {url} only when the dev's description contained that URL. */
export interface SourceOption {
  type: SourceOptionType;
  label: string;
  why: string;
  config?: Record<string, unknown> | null;
}

/** Ranked best first; when the model couldn't answer, the full catalogue unranked (both kinds).
 *  meaning is the description, verbatim. */
export interface SourceSuggestion {
  kind: InputKind;
  shape: InputShape;
  options: SourceOption[];
  meaning: string;
}

/** Body of POST /v1/apps/{app}/compile. Response: AppSpec at "pending_confirmation". */
export interface CompileRequest {
  intent: string;
  grid: GridSpec;
  inputs: CompileInput[];
  /** Fixed destinations picked on the map; copied verbatim into routing.destination_points. */
  destination_points?: DestinationPoint[];
}

/** Response of PUT /v1/apps/{app}/spec (body: the full AppSpec; only while pending_confirmation).
 * Validation errors are a 422 whose `detail` is the full list of messages. */
export interface SpecEditResponse {
  spec: AppSpec;
  warnings: string[];
}

/** POST /v1/apps/{app}/activate. The keys are shown once. */
export interface ActivateResponse {
  app_id: string;
  ingest_key: string;
  authority_key: string;
}

// --- Public operator catalogue (api/catalog.py) ---

/** One entry of GET /v1/operators: platform data only (names/descriptions/params schema), no app data. */
export interface OperatorInfo {
  name: string;
  description: string;
  shapes: InputShape[];
  role: "layer" | "override";
  params: Record<string, unknown>;
}

// --- Architecture right panel additions (LayerStackPanel) ---

/** Change gains `reverts` (POST /v1/apps/{app}/changes/{id}/revert returns the new Change). Declaration
 * merging keeps this block separate from the original Change interface above. */
export interface Change {
  reverts?: string | null; // id of the Change this one reverts; null otherwise
}

// --- Compile progress and failure (api/apps.py; added for the Sources page) ---

/** Errors and warnings from the platform's spec checks, as plain strings. */
export interface ValidateResult {
  errors: string[];
  warnings: string[];
}

/** GET /v1/apps/{app}/compile/status: public-safe progress (apps not active only; 404 otherwise). */
export interface CompileStatus {
  running: boolean;
  step: number; // Compiler trace entries (model steps) since this compile started
  elapsed_s: number; // 0 when not running
  last_validate?: ValidateResult | null;
}

/** `detail` of the 422 from POST /v1/apps/{app}/compile when the Compiler never finalized. */
export interface CompileFailure {
  reason: string;
  errors: string[];
  warnings: string[];
}

// --- Console: label proposals and dev-requested Scout runs (api/labels.py, api/scout.py) ---

export type LabelProposalStatus = "open" | "accepted" | "dismissed";

/** A label type the POI Labeler proposed. `why` is agent text: authority view only (rule 8). */
export interface LabelProposal {
  id: string;
  label: string; // 1..60 chars
  sensitive?: boolean; // default false
  destination?: boolean; // default false
  why: string; // <= 500 chars
  status?: LabelProposalStatus; // default "open"
  t: string;
}

/** Optional body of POST /label-proposals/{id}/accept: override the proposed flags. */
export interface LabelProposalAccept {
  sensitive?: boolean | null;
  destination?: boolean | null;
}

export interface LabelProposalAcceptResponse {
  proposal: LabelProposal;
  label_set: PoiLabel[];
}

/** Body of POST /v1/apps/{app}/scout (authority key): one pull input, request 1..500 chars. */
export interface ScoutRequest {
  input_id: string;
  request: string;
}

/** 202 from POST /scout: queued; the Scout's steps appear in the trace. */
export interface ScoutJobAccepted {
  job_id: string;
  app_id: string;
  input_id: string;
}

// --- Console: source status and test events (FE3; GET /sources/status, POST /simulate; #267/#268) ---
// Both are authority only. Older backends answer 404; the console then hides these panels' data.

/** How an input receives data: push endpoints or a pull connector type. */
export type SourceVia = "post_api" | "webhook" | "rss" | "json_api" | "json_list";

/** One row of GET /v1/apps/{app}/sources/status, one per input. */
export interface SourceStatus {
  input_id: string;
  kind: InputKind;
  via: SourceVia | null; // null: a pull input with no connector yet (#277)
  poll_s: number | null;
  last_polled_at: string | null;
  next_poll_at: string | null;
  /** "ok", or "error: ..." (no URL, no body). Null before the first poll and for push inputs. */
  last_status: string | null;
  events_total: number;
  events_last_hour: number;
  last_event_at: string | null;
}

export type SimulateMode = "normal" | "spoof";

/** Body of POST /v1/apps/{app}/simulate: push inputs only; the point must be inside the area. */
export interface SimulateRequest {
  input_id: string;
  count: number; // 1..200
  lat: number;
  lon: number;
  radius_m: number; // 10..5000
  mode: SimulateMode;
}

/** 202 from POST /simulate. */
export interface SimulateResult {
  accepted: number;
  rejected: number;
  user_id_prefix: string;
}

// --- End-user page (FE1, #269): public app info and routing modes ---

/** escape: started inside a blocked area. nearest_safe: no fixed destination within reach, so the
 *  route goes to the nearest clear cell. fallback: nothing reachable (text is the dev's fallback). */
export type InstructionMode = "route" | "escape" | "nearest_safe" | "fallback";

/** One public source. `label` is the input's meaning (<= 80 chars), or "N private sources". */
export interface PublicSource {
  kind: InputKind;
  label: string;
}

/** GET /v1/apps/{app}/public (no key). 404 for an unknown app; any status, it says when not active. */
export interface PublicAppInfo {
  app_id: string;
  title: string;
  intent: string;
  status: AppStatus;
  routing_enabled: boolean;
  bbox: BBox;
  last_update: IsoDateTime | null;
  sources: PublicSource[];
}
