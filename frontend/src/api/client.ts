import type {
  ActivateResponse,
  AppSpec,
  BacktestDetail,
  Change,
  ClockControl,
  ClockState,
  CompileRequest,
  CompileStatus,
  DistrustedUser,
  ErrorSeries,
  Event,
  HotspotsResponse,
  Instruction,
  InstructionRequest,
  LabelProposal,
  LabelProposalAccept,
  LabelProposalAcceptResponse,
  MapResponse,
  OperatorInfo,
  PinRequest,
  POI,
  PublicAppInfo,
  ScoutJobAccepted,
  ScoutRequest,
  SimulateRequest,
  SimulateResult,
  SourceStatus,
  SourceSuggestion,
  SpecEditResponse,
  SuggestSourceRequest,
  TraceEntry,
  VerificationView,
  WeightsView,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    /** Every message when the server sent a list as `detail` (e.g. a 422 with all spec errors). */
    readonly details: string[] = [],
    /** The raw `detail` the server sent (e.g. a CompileFailure object on compile's 422). */
    readonly detail: unknown = undefined,
    /** Seconds from a Retry-After header (503/429), when present. */
    readonly retryAfterS: number | null = null,
  ) {
    super(message);
  }
}

/** A `detail` list item: our own messages are strings; FastAPI validation errors are {loc, msg}. */
function detailText(d: unknown): string {
  if (typeof d === "string") return d;
  if (d && typeof d === "object" && "msg" in d) {
    const { loc, msg } = d as { loc?: unknown; msg: unknown };
    const where = Array.isArray(loc) ? loc.filter((x) => x !== "body").join(".") : "";
    return where ? `${where}: ${String(msg)}` : String(msg);
  }
  return JSON.stringify(d);
}

async function request<T>(method: "GET" | "POST" | "PUT", path: string, authorityKey?: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (authorityKey) headers["X-Authority-Key"] = authorityKey;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(`/v1${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!r.ok) {
    let detail = "";
    let details: string[] = [];
    let rawDetail: unknown = undefined;
    const ra = Number(r.headers.get("Retry-After"));
    const retryAfterS = r.headers.has("Retry-After") && Number.isFinite(ra) ? ra : null;
    try {
      const body = (await r.json()) as { detail?: unknown };
      rawDetail = body.detail;
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail && typeof body.detail === "object" && typeof (body.detail as { reason?: unknown }).reason === "string")
        detail = (body.detail as { reason: string }).reason; // CompileFailure
      else if (Array.isArray(body.detail)) {
        details = body.detail.map(detailText);
        detail = details.join("; ");
      }
    } catch {
      // not JSON
    }
    throw new ApiError(r.status, detail || `${r.status} ${r.statusText} for ${path}`, details, rawDetail, retryAfterS);
  }
  return (await r.json()) as T;
}

const get = <T>(path: string, key?: string) => request<T>("GET", path, key);
const app = (appId: string) => `/apps/${encodeURIComponent(appId)}`;

export const api = {
  map: (appId: string, key?: string) => get<MapResponse>(`${app(appId)}/map?format=geojson`, key),
  hotspots: (appId: string, key?: string) => get<HotspotsResponse>(`${app(appId)}/hotspots`, key),
  pois: (appId: string, key?: string) => get<POI[]>(`${app(appId)}/pois`, key),
  // authority only, max limit 5000
  raw: (appId: string, inputId: string, key: string, opts?: { since?: string; limit?: number }) => {
    const params = new URLSearchParams();
    if (opts?.since) params.set("since", opts.since);
    if (opts?.limit) params.set("limit", String(opts.limit));
    const qs = params.toString();
    return get<Event[]>(`${app(appId)}/inputs/${encodeURIComponent(inputId)}/raw${qs ? `?${qs}` : ""}`, key);
  },
  // authority only: the full spec, for rehydrating the Architecture page on a direct/bookmarked visit
  getApp: (appId: string, key: string) => get<AppSpec>(`${app(appId)}`, key),
  // public platform catalogue (not app data): what the Compiler can pick from
  operators: () => get<OperatorInfo[]>("/operators"),
  // builder (the console has no login; the authority key is issued at activation)
  compile: (appId: string, body: CompileRequest) => request<AppSpec>("POST", `${app(appId)}/compile`, undefined, body),
  // edit a pending spec before confirming it; 422 carries every error in ApiError.details
  updateSpec: (appId: string, spec: AppSpec) => request<SpecEditResponse>("PUT", `${app(appId)}/spec`, undefined, spec),
  activate: (appId: string) => request<ActivateResponse>("POST", `${app(appId)}/activate`),
  suggestSource: (appId: string, body: SuggestSourceRequest) =>
    request<SourceSuggestion>("POST", `${app(appId)}/sources/suggest`, undefined, body),
  // public: the caller's own position only (api/instructions.py)
  instruction: (appId: string, body: InstructionRequest) =>
    request<Instruction>("POST", `${app(appId)}/instruction`, undefined, body),
  // authority only (api/trace.py)
  trace: (appId: string, key: string) => get<TraceEntry[]>(`${app(appId)}/trace?limit=50`, key),
  changes: (appId: string, key: string) => get<Change[]>(`${app(appId)}/changes`, key),
  weights: (appId: string, key: string) => get<WeightsView>(`${app(appId)}/weights`, key),
  verification: (appId: string, key: string) => get<VerificationView>(`${app(appId)}/verification`, key),
  backtest: (appId: string, key: string, backtestId: string) =>
    get<BacktestDetail>(`${app(appId)}/backtests/${encodeURIComponent(backtestId)}`, key),
  trustLowered: (appId: string, key: string) => get<DistrustedUser[]>(`${app(appId)}/trust/lowered`, key),
  pin: (appId: string, key: string, layerId: string, body: PinRequest) =>
    request<WeightsView>("POST", `${app(appId)}/weights/${encodeURIComponent(layerId)}/pin`, key, body),
  // dev revert of one Change through the same tools and guards; 409 stale, 422 not revertible / guard rejected
  revertChange: (appId: string, key: string, changeId: string) =>
    request<Change>("POST", `${app(appId)}/changes/${encodeURIComponent(changeId)}/revert`, key),
  // authority only (api/labels.py): POI Labeler proposals; 404 unknown, 409 not open / app not active, 422 guard
  labelProposals: (appId: string, key: string) => get<LabelProposal[]>(`${app(appId)}/label-proposals`, key),
  acceptLabelProposal: (appId: string, key: string, id: string, body?: LabelProposalAccept) =>
    request<LabelProposalAcceptResponse>("POST", `${app(appId)}/label-proposals/${encodeURIComponent(id)}/accept`, key, body),
  dismissLabelProposal: (appId: string, key: string, id: string) =>
    request<LabelProposal>("POST", `${app(appId)}/label-proposals/${encodeURIComponent(id)}/dismiss`, key),
  // authority only (api/scout.py): 202 queued; 404 input, 409 not active, 422 push input / bad body
  requestScout: (appId: string, key: string, body: ScoutRequest) =>
    request<ScoutJobAccepted>("POST", `${app(appId)}/scout`, key, body),
  // DEMO_MODE only: these 404 otherwise
  debugClock: () => get<ClockState>("/debug/clock"),
  controlClock: (appId: string, key: string, body: ClockControl) =>
    request<ClockState>("POST", `/debug${app(appId)}/clock`, key, body),
  errorSeries: (appId: string, key: string) => get<ErrorSeries>(`/debug${app(appId)}/error`, key),

  // --- Sources page (fe-sources) ---
  // public-safe compile progress (step count, elapsed, last validation); 404 when nothing is known
  compileStatus: (appId: string) => get<CompileStatus>(`${app(appId)}/compile/status`),

  // --- Console (FE3): authority only; 404 on a backend without these routes ---
  sourcesStatus: (appId: string, key: string) => get<SourceStatus[]>(`${app(appId)}/sources/status`, key),
  simulate: (appId: string, key: string, body: SimulateRequest) =>
    request<SimulateResult>("POST", `${app(appId)}/simulate`, key, body),
  // --- End-user page (FE1, #269) ---
  // public: title, intent, status, area, last update and public sources; 404 unknown app (or an
  // older backend without the route: callers fall back to /map and /instruction alone)
  publicInfo: (appId: string) => get<PublicAppInfo>(`${app(appId)}/public`),
};

// --- Architecture page additions (fe-arch-shell) ---

export const archApi = {
  /** Full spec; `key` may be empty (no header). The server answers 401 without the authority key. */
  getAppMaybeKey: (appId: string, key: string) => get<AppSpec>(`${app(appId)}`, key || undefined),
  /** Public: 200 while the app is not active, 404 ("no compile for this app") once it is active. */
  compileStatus: api.compileStatus,
};
