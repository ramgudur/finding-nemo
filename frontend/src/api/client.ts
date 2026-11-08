import type { HotspotsResponse, MapResponse } from "./outputs";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function get<T>(path: string, authorityKey?: string): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (authorityKey) headers["X-Authority-Key"] = authorityKey;
  const r = await fetch(`/v1${path}`, { headers });
  if (!r.ok) throw new ApiError(r.status, `${r.status} ${r.statusText} for ${path}`);
  return (await r.json()) as T;
}

const app = (appId: string) => `/apps/${encodeURIComponent(appId)}`;

export const api = {
  map: (appId: string, key?: string) => get<MapResponse>(`${app(appId)}/map?format=geojson`, key),
  hotspots: (appId: string, key?: string) => get<HotspotsResponse>(`${app(appId)}/hotspots`, key),
};
