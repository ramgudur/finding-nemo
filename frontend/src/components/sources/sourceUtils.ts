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
  TsFormat,
  WebhookMapping,
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

// --- Cell size: an automatic default that fits the area (MAX_GRID_CELLS is the server's hard cap) ---

/** Round cell sizes the builder offers, smallest first (metres per side). */
export const CELL_SIZES = [5, 10, 25, 50, 100, 250, 500, 1000] as const;
export const LARGEST_CELL_SIZE = 1000;

/**
 * The automatic size aims for at most this many cells. The cap (MAX_GRID_CELLS) is what the server accepts,
 * but every 2 s engine tick and the backtest layer cache scale with the cell count, so a comfortable default
 * stays well under it. A size chosen by hand may go up to the cap.
 */
export const AUTO_CELL_TARGET = 40_000;

/** rows x cols for a bbox at cellM (same maths as GridSpec.shape()). */
export function cellCount(b: BBox, cellM: number): number {
  const [h, w] = gridCells(b, cellM);
  return h * w;
}

/** True when the server would accept this cell size for this bbox. */
export function cellSizeFits(b: BBox, cellM: number): boolean {
  return Number.isFinite(cellM) && cellM > 0 && cellCount(b, cellM) <= MAX_GRID_CELLS;
}

/**
 * The automatic cell size for a bbox: the smallest round size with at most AUTO_CELL_TARGET cells; failing
 * that, the largest round size (fewest cells) while it is under the cap; null when even that is over the cap
 * (area too large).
 */
export function autoCellSize(b: BBox): number | null {
  for (const m of CELL_SIZES) if (cellCount(b, m) <= AUTO_CELL_TARGET) return m;
  return cellCount(b, LARGEST_CELL_SIZE) <= MAX_GRID_CELLS ? LARGEST_CELL_SIZE : null;
}

/** True when even the largest round cell size is over the cap: only a smaller area helps. */
export function areaTooLarge(b: BBox): boolean {
  return autoCellSize(b) === null;
}

/**
 * Cell size after the area changes. A size the dev chose by hand stays while it still fits the new area;
 * otherwise (or when it was automatic) the automatic size takes over and the manual flag resets.
 */
export function cellSizeForArea(b: BBox, cellM: number, manual: boolean): { cellM: number; manual: boolean } {
  if (manual && cellSizeFits(b, cellM)) return { cellM, manual: true };
  return { cellM: autoCellSize(b) ?? LARGEST_CELL_SIZE, manual: false };
}

export const OPTION_KIND: Record<SourceOptionType, InputKind> = {
  post_api: "push",
  webhook: "push",
  rss: "pull",
  json_api: "pull",
  json_list: "pull",
  local_file: "pull",
};

/** Shown in the "We fetch it" column even when the suggester (an older server) doesn't list it. */
export const JSON_LIST_OPTION: SourceOption = {
  type: "json_list",
  label: "JSON list / GeoJSON feed (we fetch it)",
  why: "A web address that returns a list of items with coordinates. We fetch it on a schedule and map each item.",
};

/** Shown in "Other ways" even when the suggester (an older server) doesn't list it. */
export const LOCAL_FILE_OPTION: SourceOption = {
  type: "local_file",
  label: "Local text file (we read it every minute)",
  why: "A text file on the server, one item per line as lat, lon | text. We read it on a schedule and place each line on the map.",
};

/** A local_file name: bare, no path (same pattern as the server's connector config). */
export const LOCAL_FILE_NAME = /^[A-Za-z0-9_.-]{1,64}\.txt$/;

/** The default file name: firenews.txt only when the dev's description is about news; otherwise empty. */
export const defaultLocalFile = (description: string | undefined) => (/\bnews\b/i.test(description ?? "") ? "firenews.txt" : "");

/** How often a fetched source is checked. */
export const POLL_CHOICES: { s: number; label: string }[] = [
  { s: 60, label: "every minute" },
  { s: 300, label: "every 5 minutes" },
  { s: 900, label: "every 15 minutes" },
  { s: 3600, label: "every hour" },
];

export const optionKey = (o: SourceOption) => `${o.type}:${String(o.config?.preset ?? "")}`;
export const presetOf = (o: SourceOption | null | undefined) =>
  typeof o?.config?.preset === "string" ? o.config.preset : null;

/** A pull option that must have a URL before it can be added (custom json_api, json_list). RSS may stay empty (Scout). */
export const urlRequired = (o: SourceOption) => (o.type === "json_api" && !presetOf(o)) || o.type === "json_list";

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
  /** json_list only: where each item keeps its place, time and fields (same keys as a webhook mapping). */
  mapping?: WebhookMapping;
  /** json_list only: seconds between fetches. */
  pollS?: number;
  /** json_list only: a sample body for the path preview (stays in this browser, never compiled). */
  sample?: string;
  /** local_file only: the bare file name under backend/data/feeds/. */
  filename?: string;
}

/** `description` is the dev's words for the source; it only picks the local_file default name. */
export function connectorDraftFor(o: SourceOption, bbox: BBox | null, description?: string): ConnectorDraft {
  const c = areaCentre(bbox);
  return {
    url: typeof o.config?.url === "string" ? o.config.url : "",
    configText: o.type === "json_api" && c ? JSON.stringify(c) : "",
    ...(o.type === "json_list" ? { mapping: emptyWebhook(), pollS: 300 } : {}),
    ...(o.type === "local_file" ? { filename: defaultLocalFile(description), pollS: 60 } : {}),
    ...(presetOf(o) === "open_meteo" ? { pollS: 60 } : {}),
  };
}

/** Why this draft can't be added yet, or null. */
export function connectorProblem(o: SourceOption, d: ConnectorDraft): string | null {
  if (o.type === "local_file") {
    const f = (d.filename ?? "").trim();
    if (!f) return "Add the file name.";
    return LOCAL_FILE_NAME.test(f) ? null : "Use a bare file name ending in .txt: letters, digits, . _ or -, no folders.";
  }
  if (urlRequired(o) && !d.url.trim()) return o.type === "json_list" ? "Add the web address of the JSON list." : "A custom JSON API source needs its URL.";
  if (d.url.trim() && !/^https?:\/\//i.test(d.url.trim())) return "The URL must start with http:// or https://.";
  if (o.type === "json_api" && parseConfig(d.configText) === null) return "Connector config must be a JSON object.";
  if (o.type === "json_list") {
    const first = webhookProblems(d.mapping ?? emptyWebhook(), [])[0];
    if (first) return `Item mapping: ${first}.`;
  }
  return null;
}

/** The CompileInput connector for a pull option, or undefined (push; or RSS with no URL yet, so the
 *  Compiler can ask the Scout for one). */
export function connectorOf(o: SourceOption, d: ConnectorDraft): ConnectorConfig | undefined {
  if (OPTION_KIND[o.type] !== "pull") return undefined;
  if (o.type === "local_file")
    return { type: "local_file", url: null, preset: null, poll_s: d.pollS ?? 60, config: { filename: (d.filename ?? "").trim() } };
  if (o.type === "json_list") {
    return {
      type: "json_list",
      url: d.url.trim(),
      preset: null,
      poll_s: d.pollS ?? 300,
      config: listConfigOf(d.mapping ?? emptyWebhook()),
    };
  }
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

/** Filler words dropped from generated ids, so "the current wind speed" reads "wind_speed". Plain English
 *  function words only, nothing about any domain. */
const SLUG_STOPWORDS = new Set([
  "a", "an", "the", "of", "and", "or", "for", "with", "from", "to", "in", "on", "at", "by", "into", "onto",
  "about", "over", "per", "via", "as", "is", "are", "be", "its", "their", "our", "my", "your", "this", "that",
  "these", "those", "any", "each", "every", "some", "all", "current", "latest", "live", "which", "who", "where",
  "when", "it", "they", "we", "i", "you", "near", "around", "within",
]);

/** A SLUG-valid id from the dev's description ("The current wind speed" -> "wind_speed", "PM2.5 readings" -> "pm2_5_readings"). */
export function slugFromDescription(text: string, taken: string[]): string {
  const all = text
    .toLowerCase()
    .normalize("NFKD")
    .replace(/\p{M}/gu, "") // drop accents after NFKD
    .replace(/(\d)\.(\d)/g, "$1_$2") // "PM2.5" -> "pm2_5"
    .replace(/[^a-z0-9\s_-]/g, " ")
    .split(/\s+/)
    .filter(Boolean);
  const kept = all.filter((w) => !SLUG_STOPWORDS.has(w));
  const words = (kept.length ? kept : all).slice(0, 3);
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
    !b && "Frame the area on the map and choose \u201cUse this view\u201d, or draw a box with \u201cDraw area\u201d.",
    !(d.cellM > 0) && "Cell size must be a positive number of metres.",
    b &&
      areaTooLarge(b) &&
      `Area too large: shrink it. Even ${LARGEST_CELL_SIZE} m cells would give ${cellCount(b, LARGEST_CELL_SIZE).toLocaleString()} cells; the limit is ${MAX_GRID_CELLS.toLocaleString()}.`,
    dims &&
      b &&
      !areaTooLarge(b) &&
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
    d.sources.some((s) => s.connector?.type === "json_list" && !s.connector.url) && "A JSON list source needs its web address.",
    d.sources.some((s) => s.connector?.type === "local_file" && !LOCAL_FILE_NAME.test(localFileOf(s.connector))) &&
      "A local file source needs a bare file name ending in .txt.",
    ...d.sources.flatMap((s) =>
      s.connector?.type === "json_list"
        ? webhookProblems(listMappingOf(s.connector), s.fields).map((p) => `${s.id} item mapping: ${p}.`)
        : [],
    ),
    d.sources.some((s) => s.kind === "pull" && s.webhook) && "A source we fetch can't also receive a webhook.",
    ...d.sources.flatMap((s) =>
      s.kind === "push" && s.webhook ? webhookProblems(s.webhook, s.fields).map((p) => `${s.id} webhook: ${p}.`) : [],
    ),
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

// --- Webhook mapping (push only): data only, never code (rule 10) ---

export const TS_FORMATS: { value: TsFormat; label: string }[] = [
  { value: "iso", label: "ISO 8601 text" },
  { value: "epoch_s", label: "seconds since 1970" },
  { value: "epoch_ms", label: "milliseconds since 1970" },
];

/** A fresh mapping for a source that receives another service's webhook (lat/lon still to fill). */
export const emptyWebhook = (): WebhookMapping => ({
  items_path: "",
  lat: "",
  lon: "",
  ts: null,
  ts_format: "iso",
  user_id: null,
  fields: {},
});

export type Dug = { ok: true; value: unknown } | { ok: false; error: string };

/** Resolve a dotted path with list indices ("geometry.coordinates.1") like api/webhooks.py `_dig`:
 *  a list takes an integer index, an object a key, and "" is the node itself. A pure lookup, no eval. */
export function digPath(data: unknown, path: string): Dug {
  let node = data;
  const parts = path ? path.split(".") : [];
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i] ?? "";
    const at = parts.slice(0, i).join(".") || "the item";
    if (Array.isArray(node)) {
      if (!/^\s*-?\d+\s*$/.test(part)) return { ok: false, error: `${at} is a list, and "${part}" isn't an index` };
      let idx = Number(part);
      if (idx < 0) idx += node.length;
      if (idx < 0 || idx >= node.length) return { ok: false, error: `${at} has no item ${part} (it has ${node.length})` };
      node = node[idx];
    } else if (node !== null && typeof node === "object") {
      if (!Object.prototype.hasOwnProperty.call(node, part)) return { ok: false, error: `${at} has no "${part}"` };
      node = (node as Record<string, unknown>)[part];
    } else {
      return { ok: false, error: `${at} is ${describeValue(node)}, so "${part}" can't be read from it` };
    }
  }
  return { ok: true, value: node };
}

/** A short, safe rendering of a JSON value for the preview. */
export function describeValue(v: unknown): string {
  if (v === null) return "null";
  if (v === undefined) return "nothing";
  if (Array.isArray(v)) return `a list of ${v.length}`;
  if (typeof v === "object") return "an object";
  if (typeof v === "string") return JSON.stringify(v.length > 40 ? `${v.slice(0, 40)}…` : v);
  return String(v);
}

/** "a..b" or a trailing dot never resolves on the server. */
function pathFormProblem(path: string): string | null {
  return path.split(".").some((p) => p === "") ? `"${path}" has an empty part; separate names with single dots` : null;
}

/** Why a mapping can't be sent yet. Required declared fields need a path, or every item would be rejected. */
export function webhookProblems(w: WebhookMapping, fields: InputField[] | undefined): string[] {
  const out: string[] = [];
  if (!w.lat.trim()) out.push("say where the latitude is");
  if (!w.lon.trim()) out.push("say where the longitude is");
  const paths = [w.items_path ?? "", w.lat, w.lon, w.ts ?? "", w.user_id ?? "", ...Object.values(w.fields ?? {})];
  for (const p of paths.map((x) => x.trim()).filter(Boolean)) {
    const bad = pathFormProblem(p);
    if (bad && !out.includes(bad)) out.push(bad);
  }
  for (const f of fields ?? []) {
    if (f.required !== false && !(w.fields?.[f.name] ?? "").trim()) out.push(`say where the required field ${f.name} is`);
  }
  return out;
}

/** The mapping as the compile body carries it: trimmed, blanks as null, and fields only for declared names. */
export function webhookForCompile(w: WebhookMapping, fields: InputField[] | undefined): WebhookMapping {
  const declared = new Set((fields ?? []).map((f) => f.name));
  const mapped: Record<string, string> = {};
  for (const [name, path] of Object.entries(w.fields ?? {})) {
    if (declared.has(name) && path.trim()) mapped[name] = path.trim();
  }
  return {
    items_path: (w.items_path ?? "").trim(),
    lat: w.lat.trim(),
    lon: w.lon.trim(),
    ts: w.ts?.trim() || null,
    ts_format: w.ts_format ?? "iso",
    user_id: w.user_id?.trim() || null,
    fields: mapped,
  };
}

export interface PreviewRow {
  ok: boolean;
  text: string;
  /** A soft note on an accepted value (e.g. a coordinate sent as text). Never blocks. */
  note?: string;
}

export type SamplePreview =
  | { state: "empty" }
  | { state: "bad"; error: string }
  | { state: "ok"; itemCount: number; rows: Record<string, PreviewRow> };

function previewTs(raw: unknown, fmt: TsFormat): PreviewRow {
  if (fmt === "iso") {
    const t = typeof raw === "string" ? Date.parse(raw) : NaN;
    return Number.isNaN(t)
      ? { ok: false, text: `${describeValue(raw)} isn't an ISO 8601 time` }
      : { ok: true, text: new Date(t).toISOString() };
  }
  const n = typeof raw === "number" ? raw : typeof raw === "string" && raw.trim() ? Number(raw) : NaN;
  const unit = fmt === "epoch_s" ? "seconds" : "milliseconds";
  if (!Number.isFinite(n)) return { ok: false, text: `${describeValue(raw)} isn't a number of ${unit}` };
  const d = new Date(fmt === "epoch_s" ? n * 1000 : n);
  return Number.isNaN(d.getTime()) ? { ok: false, text: `${n} ${unit} is out of range` } : { ok: true, text: d.toISOString() };
}

const TYPE_CHECK: Record<FieldType, (v: unknown) => boolean> = {
  number: (v) => typeof v === "number",
  integer: (v) => typeof v === "number" && Number.isInteger(v),
  boolean: (v) => typeof v === "boolean",
  string: (v) => typeof v === "string",
};

/** Preview key for a declared field's row. */
export const fieldKey = (name: string) => `field:${name}`;

/** What each path resolves to for the FIRST item of a pasted sample. JSON.parse only, never eval (rule 10). */
export function previewWebhook(w: WebhookMapping, fields: InputField[] | undefined, sample: string): SamplePreview {
  if (!sample.trim()) return { state: "empty" };
  let body: unknown;
  try {
    body = JSON.parse(sample);
  } catch (e) {
    return { state: "bad", error: `The sample isn't valid JSON: ${e instanceof Error ? e.message : "parse error"}.` };
  }
  const listed = digPath(body, (w.items_path ?? "").trim());
  if (!listed.ok) return { state: "bad", error: `Items list: ${listed.error}.` };
  const items = Array.isArray(listed.value) ? listed.value : [listed.value];
  if (items.length === 0) return { state: "bad", error: "The items list is empty, so there's no first item to preview." };
  const item: unknown = items[0];
  const rows: Record<string, PreviewRow> = {};
  const one = (key: string, path: string, check: (v: unknown) => PreviewRow) => {
    const p = path.trim();
    if (!p) return;
    const form = pathFormProblem(p);
    if (form) {
      rows[key] = { ok: false, text: form };
      return;
    }
    const r = digPath(item, p);
    rows[key] = r.ok ? check(r.value) : { ok: false, text: r.error };
  };
  // A numeric string ("45.51") is accepted with a soft note: the server converts it (#260).
  const coord = (limit: number, name: string) => (v: unknown): PreviewRow => {
    const text = typeof v === "string" && /^\s*[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?\s*$/.test(v);
    const n = typeof v === "number" ? v : text ? Number(v) : NaN;
    if (!Number.isFinite(n))
      return { ok: false, text: `${describeValue(v)} isn't a number${typeof v === "string" ? " (it's text)" : ""}` };
    if (Math.abs(n) > limit) return { ok: false, text: `${n} is outside ±${limit}, so it can't be a ${name}` };
    return text ? { ok: true, text: String(n), note: "sent as text; the server converts it" } : { ok: true, text: String(n) };
  };
  const any = (v: unknown): PreviewRow => ({ ok: true, text: describeValue(v) });
  one("lat", w.lat, coord(90, "latitude"));
  one("lon", w.lon, coord(180, "longitude"));
  one("ts", w.ts ?? "", (v) => previewTs(v, w.ts_format ?? "iso"));
  one("user_id", w.user_id ?? "", any);
  for (const f of fields ?? []) {
    one(fieldKey(f.name), w.fields?.[f.name] ?? "", (v) =>
      TYPE_CHECK[f.type](v)
        ? any(v)
        : { ok: false, text: `${describeValue(v)} isn't ${f.type === "integer" ? "an integer" : `a ${f.type}`}` },
    );
  }
  return { state: "ok", itemCount: items.length, rows };
}

// --- local_file connector: a text file under backend/data/feeds/, one item per line ---

/** The file name in a local_file connector's config ("" when missing). */
export const localFileOf = (c: ConnectorConfig) => (typeof c.config?.filename === "string" ? c.config.filename : "");

/** "every minute", "every 5 minutes", or "every 90 s". */
export const pollLabel = (s: number | null | undefined, fallback = 300) => {
  const v = s ?? fallback;
  return POLL_CHOICES.find((c) => c.s === v)?.label ?? (v % 60 === 0 ? `every ${v / 60} minutes` : `every ${v} s`);
};

/** "Local file · every minute" for a local_file connector. */
export const localFileLabel = (c: ConnectorConfig) => `Local file · ${pollLabel(c.poll_s, 60)}`;

// --- json_list connector: the same mapping keys as a webhook, kept in connector.config (contract E) ---

/** The mapping held in a json_list connector's config, as the shared editor edits it. */
export function listMappingOf(c: ConnectorConfig): WebhookMapping {
  const cfg = c.config ?? {};
  const str = (k: string) => (typeof cfg[k] === "string" ? (cfg[k] as string) : "");
  const fields = cfg.fields && typeof cfg.fields === "object" && !Array.isArray(cfg.fields) ? (cfg.fields as Record<string, string>) : {};
  const fmt = str("ts_format");
  return {
    items_path: str("items_path"),
    lat: str("lat"),
    lon: str("lon"),
    ts: str("ts") || null,
    ts_format: fmt === "epoch_s" || fmt === "epoch_ms" ? fmt : "iso",
    user_id: str("user_id") || null,
    fields: { ...fields },
  };
}

/** A json_list config from a mapping: {items_path, lat, lon, ts?, ts_format, user_id?, fields}. */
export function listConfigOf(m: WebhookMapping): Record<string, unknown> {
  return {
    items_path: m.items_path ?? "",
    lat: m.lat,
    lon: m.lon,
    ...(m.ts ? { ts: m.ts } : {}),
    ts_format: m.ts_format ?? "iso",
    ...(m.user_id ? { user_id: m.user_id } : {}),
    fields: m.fields ?? {},
  };
}

/** The connector as the compile body carries it: a json_list mapping trimmed to the declared fields. */
export function connectorForCompile(c: ConnectorConfig, fields: InputField[] | undefined): ConnectorConfig {
  if (c.type !== "json_list") return c;
  return { ...c, url: (c.url ?? "").trim(), config: listConfigOf(webhookForCompile(listMappingOf(c), fields)) };
}
