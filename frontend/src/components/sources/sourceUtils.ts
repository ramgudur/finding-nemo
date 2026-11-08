// Shared, pure helpers for the Sources page (ported from Builder.tsx so client and server agree).
import { MAX_GRID_CELLS } from "../../api/types";
import type { BBox, CompileInput, ConnectorConfig, DestinationPoint, InputKind, SourceOption, SourceOptionType } from "../../api/types";

/** Same pattern as core/models.py SLUG (app ids and input ids). */
export const SLUG = /^[a-z0-9][a-z0-9_-]{0,63}$/;

const M_PER_DEG = (6_371_008.8 * Math.PI) / 180; // same Earth radius as engine/grid.py and GridSpec

/** Grid size [rows, cols] for a bbox at cell_m, computed like GridSpec.shape(). */
export function gridCells(b: BBox, cellM: number): [number, number] {
  const midLat = ((b.min_lat + b.max_lat) / 2) * (Math.PI / 180);
  const h = ((b.max_lat - b.min_lat) * M_PER_DEG) / cellM;
  const w = ((b.max_lon - b.min_lon) * M_PER_DEG * Math.cos(midLat)) / cellM;
  return [Math.max(1, Math.ceil(h - 1e-9)), Math.max(1, Math.ceil(w - 1e-9))];
}

export const round6 = (v: number) => Math.round(v * 1e6) / 1e6;

export const OPTION_KIND: Record<SourceOptionType, InputKind> = {
  post_api: "push",
  webhook: "push",
  rss: "pull",
  json_api: "pull",
};

export const optionKey = (o: SourceOption) => `${o.type}:${String(o.config?.preset ?? "")}`;
export const presetOf = (o: SourceOption | null | undefined) =>
  typeof o?.config?.preset === "string" ? o.config.preset : null;

/** A pull option that must have a URL before it can be added (custom json_api). RSS may stay empty (Scout). */
export const urlRequired = (o: SourceOption) => o.type === "json_api" && !presetOf(o);

export function parseConfig(text: string): Record<string, unknown> | null {
  if (!text.trim()) return {};
  try {
    const v: unknown = JSON.parse(text);
    return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

export function areaCentre(b: BBox | null) {
  return b ? { latitude: round6((b.min_lat + b.max_lat) / 2), longitude: round6((b.min_lon + b.max_lon) / 2) } : null;
}

/** The dev's edits to a pull option before it becomes a source. */
export interface ConnectorDraft {
  url: string;
  configText: string;
}

export function connectorDraftFor(o: SourceOption, bbox: BBox | null): ConnectorDraft {
  const c = areaCentre(bbox);
  return {
    url: typeof o.config?.url === "string" ? o.config.url : "",
    configText: o.type === "json_api" && c ? JSON.stringify(c) : "",
  };
}

/** Why this draft can't be added yet, or null. */
export function connectorProblem(o: SourceOption, d: ConnectorDraft): string | null {
  if (urlRequired(o) && !d.url.trim()) return "A custom JSON API source needs its URL.";
  if (d.url.trim() && !/^https?:\/\//i.test(d.url.trim())) return "The URL must start with http:// or https://.";
  if (o.type === "json_api" && parseConfig(d.configText) === null) return "Connector config must be a JSON object.";
  return null;
}

/** The CompileInput connector for a pull option, or undefined (push; or RSS with no URL yet, so the
 *  Compiler can ask the Scout for one). */
export function connectorOf(o: SourceOption, d: ConnectorDraft): ConnectorConfig | undefined {
  if (OPTION_KIND[o.type] !== "pull") return undefined;
  const type = o.type === "rss" ? "rss" : "json_api";
  const preset = presetOf(o);
  if (!preset && !d.url.trim()) return undefined;
  return {
    type,
    url: preset ? null : d.url.trim(),
    preset,
    config: type === "json_api" ? (parseConfig(d.configText) ?? {}) : {},
  };
}

/** A SLUG-valid id from the dev's description ("Phones posting GPS" -> "phones_posting_gps"). */
export function slugFromDescription(text: string, taken: string[]): string {
  const words = text
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9\s_-]/g, " ")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 4);
  let base = words.join("_").replace(/^[_-]+/, "").slice(0, 40).replace(/[_-]+$/, "");
  if (!SLUG.test(base)) base = "source";
  let id = base;
  for (let n = 2; taken.includes(id); n++) id = `${base}_${n}`;
  return id;
}

export interface SourcesDraft {
  appId: string;
  intent: string;
  bbox: BBox | null;
  cellM: number;
  destinationPoints: DestinationPoint[];
  sources: CompileInput[];
}

/** Every reason Continue is disabled, in the order the dev should fix them. Empty = valid. */
export function draftProblems(d: SourcesDraft): string[] {
  const dims = d.bbox && d.cellM > 0 ? gridCells(d.bbox, d.cellM) : null;
  const ids = d.sources.map((s) => s.id);
  const b = d.bbox;
  return [
    !SLUG.test(d.appId) && "Name the app: lowercase letters, digits, - or _.",
    !d.intent.trim() && "Describe the app's intent.",
    !b && "Pick the area on the map.",
    !(d.cellM > 0) && "Cell size must be positive.",
    dims &&
      dims[0] * dims[1] > MAX_GRID_CELLS &&
      `The grid would be ${dims[0]} x ${dims[1]} cells; the limit is ${MAX_GRID_CELLS.toLocaleString()}.`,
    d.destinationPoints.some((p) => !p.label.trim()) && "Every destination needs a label.",
    b &&
      d.destinationPoints.some((p) => p.lat < b.min_lat || p.lat > b.max_lat || p.lon < b.min_lon || p.lon > b.max_lon) &&
      "Every destination must be inside the area.",
    d.sources.length === 0 && "Add at least one source.",
    d.sources.some((s) => !SLUG.test(s.id)) && "Every source id must be lowercase letters, digits, - or _.",
    new Set(ids).size !== ids.length && "Source ids must be unique.",
    d.sources.some((s) => s.kind === "push" && s.connector) && "A push source can't have a connector.",
    d.sources.some((s) => s.connector?.type === "json_api" && !s.connector.preset && !s.connector.url) &&
      "A custom JSON API source needs its URL.",
  ].filter(Boolean) as string[];
}
