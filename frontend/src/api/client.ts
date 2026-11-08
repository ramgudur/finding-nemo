import type {
  ActivateResponse,
  AppSpec,
  Change,
  ClockControl,
  ClockState,
  CompileRequest,
  CompileStatus,
  ErrorSeries,
  HotspotsResponse,
  Instruction,
  InstructionRequest,
  MapResponse,
  PinRequest,
  SourceSuggestion,
  SuggestSourceRequest,
  TraceEntry,
  VerificationView,
  WeightsView,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    /** The response's `detail` when it isn't a plain string (e.g. compile's 422 CompileFailure). */
    readonly detail?: unknown,
    /** Seconds from a Retry-After header, when the server sent one. */
    readonly retryAfter?: number,
  ) {
    super(message);
  }
}

async function request<T>(method: "GET" | "POST", path: string, authorityKey?: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (authorityKey) headers["X-Authority-Key"] = authorityKey;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(`/v1${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!r.ok) {
    let detail = "";
    let raw: unknown;
    try {
      const body = (await r.json()) as { detail?: unknown };
      raw = body.detail;
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail && typeof (body.detail as { message?: unknown }).message === "string")
        detail = (body.detail as { message: string }).message;
    } catch {
      // not JSON
    }
    const retry = Number(r.headers.get("Retry-After"));
    throw new ApiError(
      r.status,
      detail || `${r.status} ${r.statusText} for ${path}`,
      raw,
      Number.isFinite(retry) && retry > 0 ? retry : undefined,
    );
  }
  return (await r.json()) as T;
}

const get = <T>(path: string, key?: string) => request<T>("GET", path, key);
const app = (appId: string) => `/apps/${encodeURIComponent(appId)}`;

export const api = {
  map: (appId: string, key?: string) => get<MapResponse>(`${app(appId)}/map?format=geojson`, key),
  hotspots: (appId: string, key?: string) => get<HotspotsResponse>(`${app(appId)}/hotspots`, key),
  // builder (the console has no login; the authority key is issued at activation)
  compile: (appId: string, body: CompileRequest) => request<AppSpec>("POST", `${app(appId)}/compile`, undefined, body),
  compileStatus: (appId: string) => get<CompileStatus>(`${app(appId)}/compile/status`),
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
  pin: (appId: string, key: string, layerId: string, body: PinRequest) =>
    request<WeightsView>("POST", `${app(appId)}/weights/${encodeURIComponent(layerId)}/pin`, key, body),
  // DEMO_MODE only: these 404 otherwise
  debugClock: () => get<ClockState>("/debug/clock"),
  controlClock: (appId: string, key: string, body: ClockControl) =>
    request<ClockState>("POST", `/debug${app(appId)}/clock`, key, body),
  errorSeries: (appId: string, key: string) => get<ErrorSeries>(`/debug${app(appId)}/error`, key),
};
