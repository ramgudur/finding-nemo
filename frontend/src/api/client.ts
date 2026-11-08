import type { ClockControl, ClockState, ErrorSeries, HotspotsResponse, MapResponse } from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(method: "GET" | "POST", path: string, authorityKey?: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (authorityKey) headers["X-Authority-Key"] = authorityKey;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(`/v1${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  if (!r.ok) throw new ApiError(r.status, `${r.status} ${r.statusText} for ${path}`);
  return (await r.json()) as T;
}

const get = <T>(path: string, key?: string) => request<T>("GET", path, key);
const app = (appId: string) => `/apps/${encodeURIComponent(appId)}`;

export const api = {
  map: (appId: string, key?: string) => get<MapResponse>(`${app(appId)}/map?format=geojson`, key),
  hotspots: (appId: string, key?: string) => get<HotspotsResponse>(`${app(appId)}/hotspots`, key),
  // DEMO_MODE only: these 404 otherwise
  debugClock: () => get<ClockState>("/debug/clock"),
  controlClock: (appId: string, key: string, body: ClockControl) =>
    request<ClockState>("POST", `/debug${app(appId)}/clock`, key, body),
  errorSeries: (appId: string, key: string) => get<ErrorSeries>(`/debug${app(appId)}/error`, key),
};
