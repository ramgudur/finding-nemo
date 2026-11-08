// Ready-to-run request examples for the keys page, built from an input's real fields. Every example
// stamps `ts` when it runs (never a frozen time) and puts one item at the centre of the app's area,
// so it passes the ingest checks as is. Pure functions: no key is logged or stored here.
import type { AppSpec, FieldType, InputSpec, WebhookMapping } from "../../api/types";

export type Lang = "bash" | "powershell" | "js" | "python";
export const LANGS: Array<{ id: Lang; label: string }> = [
  { id: "bash", label: "curl" },
  { id: "powershell", label: "PowerShell" },
  { id: "js", label: "JavaScript" },
  { id: "python", label: "Python" },
];

export type TsFmt = NonNullable<WebhookMapping["ts_format"]>;

/** Marks where the run-time clock goes; each language swaps it for its own "now" expression. */
export const TS_TOKEN = "__TS_NOW__";

export function sampleValue(type: FieldType): unknown {
  return type === "number" ? 1.5 : type === "integer" ? 1 : type === "boolean" ? true : "example";
}

export function areaCentre(spec: AppSpec): { lat: number; lon: number } {
  const b = spec.grid.bbox;
  return {
    lat: Number(((b.min_lat + b.max_lat) / 2).toFixed(6)),
    lon: Number(((b.min_lon + b.max_lon) / 2).toFixed(6)),
  };
}

/** The /events body: user_id, the area centre, every declared field, and `ts` as the clock token. */
export function sampleEventBody(spec: AppSpec, input: InputSpec, userId = "device-1"): Record<string, unknown> {
  const body: Record<string, unknown> = { user_id: userId, ...areaCentre(spec) };
  for (const f of input.fields ?? []) body[f.name] = sampleValue(f.type);
  body.ts = TS_TOKEN;
  return body;
}

/** Put `value` at a dotted path ("data.0.lat"), creating objects, or lists for integer parts. A path that
 *  runs through a value already set elsewhere is skipped (the sample stays valid JSON either way). */
function setPath(root: Record<string, unknown> | unknown[], path: string, value: unknown) {
  const parts = path.split(".");
  let node: Record<string, unknown> | unknown[] = root;
  for (let i = 0; i < parts.length; i++) {
    const part = parts[i] ?? "";
    if (Array.isArray(node) && !/^\d+$/.test(part)) return;
    const key: string | number = Array.isArray(node) ? Number(part) : part;
    const bag = node as Record<string | number, unknown>;
    if (i === parts.length - 1) {
      bag[key] = value;
      return;
    }
    const cur = bag[key];
    if (cur === undefined) {
      const fresh: Record<string, unknown> | unknown[] = /^\d+$/.test(parts[i + 1] ?? "") ? [] : {};
      bag[key] = fresh;
      node = fresh;
    } else if (cur !== null && typeof cur === "object") {
      node = cur as Record<string, unknown> | unknown[];
    } else {
      return;
    }
  }
}

/** Fill list holes left by an index past 0 (JSON has no holes) with empty objects. */
function fillHoles(v: unknown): unknown {
  if (Array.isArray(v)) return Array.from(v, (x) => (x === undefined ? {} : fillHoles(x)));
  if (v !== null && typeof v === "object") {
    return Object.fromEntries(Object.entries(v as Record<string, unknown>).map(([k, x]) => [k, fillHoles(x)]));
  }
  return v;
}

/** A minimal body the webhook mapping reads: one item at the area centre, wrapped at items_path. */
export function sampleWebhookBody(spec: AppSpec, input: InputSpec, w: WebhookMapping): unknown {
  const c = areaCentre(spec);
  const item: Record<string, unknown> = {};
  setPath(item, w.lat, c.lat);
  setPath(item, w.lon, c.lon);
  if (w.ts) setPath(item, w.ts, TS_TOKEN);
  if (w.user_id) setPath(item, w.user_id, "device-1");
  for (const f of input.fields ?? []) {
    const path = w.fields?.[f.name];
    if (path) setPath(item, path, sampleValue(f.type));
  }
  const items = (w.items_path ?? "").trim();
  if (!items) return fillHoles(item);
  const root: Record<string, unknown> | unknown[] = /^\d+(\.|$)/.test(items) ? [] : {};
  setPath(root, items, [item]);
  return fillHoles(root);
}

/** The sample as plain JSON for display, with an example time in place of the clock token. */
export function displayJson(body: unknown, fmt: TsFmt): string {
  const example = fmt === "epoch_s" ? "1767225600" : fmt === "epoch_ms" ? "1767225600000" : `"2026-01-01T00:00:00Z"`;
  return JSON.stringify(body, null, 2).replace(`"${TS_TOKEN}"`, example);
}

const indent = (s: string, pad: string) => s.replace(/\n/g, `\n${pad}`);

// --- bash (curl) ---

function bash(url: string, body: unknown, fmt: TsFmt, key: string): string {
  const now =
    fmt === "epoch_s" ? `'"$(date +%s)"'` : fmt === "epoch_ms" ? `'"$(date +%s)000"'` : `"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'"`;
  const json = JSON.stringify(body, null, 2).replace(/'/g, `'\\''`).replace(`"${TS_TOKEN}"`, now);
  return [
    `curl -X POST "${url}" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -H "X-Ingest-Key: ${key}" \\`,
    `  -d '${json}'`,
  ].join("\n");
}

// --- PowerShell (5.1 and 7) ---

const psStr = (v: string) => `'${v.replace(/'/g, "''")}'`;

function psLiteral(v: unknown, fmt: TsFmt, pad: string): string {
  if (v === TS_TOKEN) {
    return fmt === "epoch_s"
      ? "[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()"
      : fmt === "epoch_ms"
        ? "[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()"
        : "(Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')";
  }
  const inner = `${pad}  `;
  if (Array.isArray(v)) return `@(\n${v.map((x) => `${inner}${psLiteral(x, fmt, inner)}`).join(",\n")}\n${pad})`;
  if (v !== null && typeof v === "object") {
    const rows = Object.entries(v as Record<string, unknown>).map(([k, x]) => `${inner}${psStr(k)} = ${psLiteral(x, fmt, inner)}`);
    return `[ordered]@{\n${rows.join("\n")}\n${pad}}`;
  }
  if (v === null) return "$null";
  if (typeof v === "boolean") return v ? "$true" : "$false";
  if (typeof v === "number") return String(v);
  return psStr(String(v));
}

function powershell(url: string, body: unknown, fmt: TsFmt, key: string): string {
  return [
    `$body = ${psLiteral(body, fmt, "")}`,
    `Invoke-RestMethod -Method Post -Uri ${psStr(url)} \``,
    `  -ContentType 'application/json' \``,
    `  -Headers @{ 'X-Ingest-Key' = ${psStr(key)} } \``,
    `  -Body (ConvertTo-Json -InputObject $body -Depth 20)`,
  ].join("\n");
}

// --- JavaScript (fetch; Node 18+ or any server runtime) ---

function js(url: string, body: unknown, fmt: TsFmt, key: string): string {
  const now = fmt === "epoch_s" ? "Math.floor(Date.now() / 1000)" : fmt === "epoch_ms" ? "Date.now()" : "new Date().toISOString()";
  const literal = indent(JSON.stringify(body, null, 2), "  ").replace(`"${TS_TOKEN}"`, now);
  return [
    `// Run it server-side: the ingest key must not ship in browser code.`,
    `const res = await fetch(${JSON.stringify(url)}, {`,
    `  method: "POST",`,
    `  headers: { "Content-Type": "application/json", "X-Ingest-Key": ${JSON.stringify(key)} },`,
    `  body: JSON.stringify(${literal}),`,
    `});`,
    `console.log(res.status, await res.json());`,
  ].join("\n");
}

// --- Python (requests) ---

function pyLiteral(v: unknown, fmt: TsFmt, pad: string): string {
  if (v === TS_TOKEN) {
    return fmt === "epoch_s" ? "int(time.time())" : fmt === "epoch_ms" ? "int(time.time() * 1000)" : "datetime.now(timezone.utc).isoformat()";
  }
  const inner = `${pad}    `;
  if (Array.isArray(v)) return `[\n${v.map((x) => `${inner}${pyLiteral(x, fmt, inner)},`).join("\n")}\n${pad}]`;
  if (v !== null && typeof v === "object") {
    const rows = Object.entries(v as Record<string, unknown>).map(([k, x]) => `${inner}${JSON.stringify(k)}: ${pyLiteral(x, fmt, inner)},`);
    return `{\n${rows.join("\n")}\n${pad}}`;
  }
  if (v === null) return "None";
  if (typeof v === "boolean") return v ? "True" : "False";
  if (typeof v === "number") return String(v);
  return JSON.stringify(String(v));
}

function python(url: string, body: unknown, fmt: TsFmt, key: string, hasTs: boolean): string {
  const imports = ["import requests"];
  if (hasTs) imports.unshift(fmt === "iso" ? "from datetime import datetime, timezone" : "import time");
  return [
    ...imports,
    "",
    "resp = requests.post(",
    `    ${JSON.stringify(url)},`,
    `    headers={"X-Ingest-Key": ${JSON.stringify(key)}},`,
    `    json=${pyLiteral(body, fmt, "    ")},`,
    "    timeout=10,",
    ")",
    "print(resp.status_code, resp.json())",
  ].join("\n");
}

/** One example in one language. `body` may hold TS_TOKEN once, where the run-time clock goes. */
export function snippet(lang: Lang, url: string, body: unknown, fmt: TsFmt, key: string): string {
  if (lang === "bash") return bash(url, body, fmt, key);
  if (lang === "powershell") return powershell(url, body, fmt, key);
  if (lang === "js") return js(url, body, fmt, key);
  return python(url, body, fmt, key, JSON.stringify(body).includes(TS_TOKEN));
}
