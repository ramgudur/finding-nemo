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
export type ConnectorType = "rss" | "json_api" | "open_meteo";
export type PoiLabelMode = "auto" | "on" | "off";
export type PoiStatus = "active" | "retired";
export type AgentName = "compiler" | "scout" | "critic" | "poi_labeler" | "message_guard";
export type ChangeKind = "weight" | "trust" | "poi" | "connector" | "spec_confirm";
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

export interface GridSpec {
  cell_m: number;
  bbox: BBox;
}

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
  operators?: OperatorConfig[];
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
  fallback_text?: string; // <= 360 chars
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
