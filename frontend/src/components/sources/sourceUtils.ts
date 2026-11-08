// Shared, pure helpers for the /new builder steps (ported from Builder.tsx so client and server agree).
import { MAX_GRID_CELLS } from "../../api/types";
import type {
  BBox,
  CompileInput,
  ConnectorConfig,
  DestinationPoint,
  FieldType,
  InputField,
  InputKind,
  InputShape,
  SourceOption,
  SourceOptionType,
} from "../../api/types";

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

/** An app id from a display name ("Harbour Watch 2" -> "harbour-watch-2"). */
export function slugFromName(name: string): string {
  const base = name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^[-_]+/, "")
    .slice(0, 64)
    .replace(/[-_]+$/, "");
  return base;
}

/** Plain-language description of each input shape (the enum stays behind "Advanced"). */
export const SHAPE_INFO: Record<InputShape, { label: string; hint: string }> = {
  point: { label: "Single reports", hint: "each item is one report at one place and time" },
  track: { label: "Moving tracks", hint: "items from the same entity form a path over time" },
  fixed_point: { label: "Fixed points", hint: "each item marks a fixed place, such as an authority marker" },
  field: { label: "Measurements over the area", hint: "gridded or station values, optionally a vector field" },
  document: { label: "Documents", hint: "each item is a text, such as an article, placed on the map" },
};
export const SHAPES: InputShape[] = ["point", "track", "fixed_point", "field", "document"];

const FIELD_TYPES: FieldType[] = ["number", "integer", "string", "boolean"];
/** Every event already carries these; a source can't redeclare them. */
const RESERVED_FIELDS = ["user_id", "lat", "lon", "ts"];
const FIELD_NAME = /^[a-zA-Z_][a-zA-Z0-9_]*$/;

/** "level:number, note:string?" -> fields, plus every problem in the dev's words ("?" = optional). */
export function parseFields(text: string): { fields: InputField[]; problems: string[] } {
  const fields: InputField[] = [];
  const problems: string[] = [];
  for (const part of text.split(",").map((x) => x.trim()).filter(Boolean)) {
    const [rawName = "", rawType = "string", ...rest] = part.split(":").map((x) => x.trim());
    const optional = rawType.endsWith("?") || rawName.endsWith("?");
    const name = rawName.replace(/\?$/, "");
    const type = (rawType.replace(/\?$/, "") || "string") as FieldType;
    if (rest.length) problems.push(`"${part}" has more than one colon.`);
    else if (!FIELD_NAME.test(name)) problems.push(`"${name || part}" isn't a valid field name (letters, digits, _).`);
    else if (RESERVED_FIELDS.includes(name)) problems.push(`"${name}" is already part of every event.`);
    else if (!FIELD_TYPES.includes(type)) problems.push(`"${rawType}" isn't a type; use number, integer, string or boolean.`);
    else if (fields.some((f) => f.name === name)) problems.push(`"${name}" is listed twice.`);
    else fields.push({ name, type, required: !optional });
  }
  return { fields, problems };
}

export function fieldsToText(fields: InputField[] | undefined): string {
  return (fields ?? []).map((f) => `${f.name}:${f.type}${f.required === false ? "?" : ""}`).join(", ");
}

export interface SourcesDraft {
  name: string;
  appId: string;
  intent: string;
  bbox: BBox | null;
  cellM: number;
  destinationPoints: DestinationPoint[];
  sources: CompileInput[];
}

export type StepId = "about" | "area" | "sources" | "review";
export const STEPS: { id: StepId; label: string }[] = [
  { id: "about", label: "About" },
  { id: "area", label: "Area" },
  { id: "sources", label: "Sources" },
  { id: "review", label: "Review & compile" },
];

export function aboutProblems(d: SourcesDraft, activeIds: string[] = []): string[] {
  return [
    !d.name.trim() && "Give the app a name.",
    !SLUG.test(d.appId) && "The app id must start with a letter or digit, then lowercase letters, digits, - or _.",
    SLUG.test(d.appId) && activeIds.includes(d.appId) && "An app with this id is already live. Pick another id.",
    !d.intent.trim() && "Describe what the app is for.",
  ].filter(Boolean) as string[];
}

export function areaProblems(d: SourcesDraft): string[] {
  const dims = d.bbox && d.cellM > 0 ? gridCells(d.bbox, d.cellM) : null;
  const b = d.bbox;
  return [
    !b && "Frame the area on the map, then choose \u201cUse this view\u201d.",
    !(d.cellM > 0) && "Cell size must be a positive number of metres.",
    dims &&
      dims[0] * dims[1] > MAX_GRID_CELLS &&
      `The grid would be ${(dims[0] * dims[1]).toLocaleString()} cells; the limit is ${MAX_GRID_CELLS.toLocaleString()}. Use a smaller area or bigger cells.`,
    d.destinationPoints.some((p) => !p.label.trim()) && "Every destination needs a label.",
    b &&
      d.destinationPoints.some((p) => p.lat < b.min_lat || p.lat > b.max_lat || p.lon < b.min_lon || p.lon > b.max_lon) &&
      "Every destination must be inside the area.",
  ].filter(Boolean) as string[];
}

export function sourcesProblems(d: SourcesDraft): string[] {
  const ids = d.sources.map((s) => s.id);
  return [
    d.sources.length === 0 && "Add at least one data source.",
    d.sources.some((s) => !SLUG.test(s.id)) && "Every source id must be lowercase letters, digits, - or _.",
    new Set(ids).size !== ids.length && "Two sources share an id; rename one.",
    d.sources.some((s) => s.kind === "push" && s.connector) && "A source your app sends can't also be polled.",
    d.sources.some((s) => s.connector?.type === "json_api" && !s.connector.preset && !s.connector.url) &&
      "A custom JSON source needs its URL.",
  ].filter(Boolean) as string[];
}

export function stepProblems(step: StepId, d: SourcesDraft, activeIds: string[] = []): string[] {
  if (step === "about") return aboutProblems(d, activeIds);
  if (step === "area") return areaProblems(d);
  if (step === "sources") return sourcesProblems(d);
  return [...aboutProblems(d, activeIds), ...areaProblems(d), ...sourcesProblems(d)];
}

/** The first step that still needs work ("review" when everything is valid). */
export function firstIncompleteStep(d: SourcesDraft, activeIds: string[] = []): StepId {
  if (aboutProblems(d, activeIds).length) return "about";
  if (areaProblems(d).length) return "area";
  if (sourcesProblems(d).length) return "sources";
  return "review";
}

/** True when the draft has nothing the dev typed yet. */
export const draftIsEmpty = (d: SourcesDraft) =>
  !d.name.trim() && !d.appId && !d.intent.trim() && !d.bbox && d.sources.length === 0 && d.destinationPoints.length === 0;
