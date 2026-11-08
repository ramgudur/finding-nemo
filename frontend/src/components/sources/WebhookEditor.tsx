// The item mapping for JSON that isn't in our own event format: another service's webhook body (a push
// source, POST /v1/apps/{app}/webhooks/{input}) or a JSON list / GeoJSON feed we fetch (a json_list pull
// source). It says where the list of items is, and where each item keeps its latitude, longitude, time,
// sender id and declared fields. A sample (pasted, or fetched from the browser when the feed allows it)
// previews what every path resolves to for the first item. Data only (rule 10): the sample goes through
// JSON.parse and a plain dotted-path lookup, never eval.
import { Check, ChevronDown, CircleAlert, Download, LoaderCircle } from "lucide-react";
import { useId, useState } from "react";
import type { ReactNode } from "react";
import type { InputField, TsFormat, WebhookMapping } from "../../api/types";
import { MAX_SAMPLE_CHARS, useBuilderStore } from "../../store/useBuilderStore";
import { TS_FORMATS, fieldKey, previewWebhook } from "./sourceUtils";
import type { PreviewRow } from "./sourceUtils";

interface Props {
  sourceId: string;
  mapping: WebhookMapping;
  fields: InputField[] | undefined;
  onChange: (m: WebhookMapping) => void;
  /** Keep the sample here instead of the builder store (e.g. before the source has an id). */
  sample?: string;
  onSample?: (text: string) => void;
  /** A feed URL the browser may try to GET for a sample ("Fetch a sample"). */
  fetchUrl?: string;
  /** Wording: "webhook" (a body sent to us) or "list" (a feed we fetch). */
  variant?: "webhook" | "list";
}

function Preview({ row, id }: { row: PreviewRow | undefined; id: string }) {
  if (!row) return null;
  return (
    <span
      id={id}
      className={`flex min-w-0 items-start gap-1 font-mono text-[11px] leading-snug ${row.ok ? "text-slate" : "text-danger"}`}
    >
      {row.ok ? (
        <Check size={12} className="mt-px shrink-0 text-mutedTeal" aria-hidden />
      ) : (
        <CircleAlert size={12} className="mt-px shrink-0" aria-hidden />
      )}
      <span className="min-w-0 break-words">
        <span className="sr-only">{row.ok ? "resolves to " : "problem: "}</span>
        {row.text}
        {row.note && <span className="text-ink"> · {row.note}</span>}
      </span>
    </span>
  );
}

/** Make a big fetched body small enough to keep as a sample: lists are cut to their first 20 items. */
function shrinkSample(body: unknown): string {
  const cut = (v: unknown): unknown => {
    if (Array.isArray(v)) return v.slice(0, 20).map(cut);
    if (v && typeof v === "object") return Object.fromEntries(Object.entries(v as Record<string, unknown>).map(([k, x]) => [k, cut(x)]));
    return v;
  };
  let text = JSON.stringify(body, null, 2);
  if (text.length <= MAX_SAMPLE_CHARS) return text;
  text = JSON.stringify(cut(body), null, 2);
  if (text.length <= MAX_SAMPLE_CHARS) return text;
  text = JSON.stringify(cut(body));
  return text.length <= MAX_SAMPLE_CHARS ? text : "";
}

type FetchState = { state: "idle" } | { state: "busy" } | { state: "done" } | { state: "error"; message: string };

export function WebhookEditor({ sourceId, mapping, fields, onChange, sample: sampleProp, onSample, fetchUrl, variant = "webhook" }: Props) {
  const uid = useId();
  const stored = useBuilderStore((s) => s.webhookSamples[sourceId] ?? "");
  const setStored = useBuilderStore((s) => s.setWebhookSample);
  const sample = sampleProp ?? stored;
  const setSample = (t: string) => (onSample ? onSample(t.slice(0, MAX_SAMPLE_CHARS)) : setStored(sourceId, t));
  const [showSample, setShowSample] = useState(sample.trim().length > 0);
  const [fetching, setFetching] = useState<FetchState>({ state: "idle" });
  const preview = previewWebhook(mapping, fields, sample);
  const rows = preview.state === "ok" ? preview.rows : {};
  const set = (patch: Partial<WebhookMapping>) => onChange({ ...mapping, ...patch });
  const setField = (name: string, path: string) => set({ fields: { ...(mapping.fields ?? {}), [name]: path } });
  const url = (fetchUrl ?? "").trim();
  const canFetch = /^https?:\/\//i.test(url);
  const isList = variant === "list";

  // A plain GET from this browser: it works only when the feed allows cross-origin reads (CORS). The body
  // is parsed as JSON and kept in this browser as the preview sample; it is never sent anywhere.
  const fetchSample = async () => {
    if (!canFetch) return;
    setShowSample(true);
    setFetching({ state: "busy" });
    try {
      const r = await fetch(url, { method: "GET", mode: "cors", credentials: "omit", headers: { Accept: "application/json" } });
      if (!r.ok) throw new Error(`The feed answered ${r.status}${r.statusText ? ` ${r.statusText}` : ""}.`);
      const text = await r.text();
      let body: unknown;
      try {
        body = JSON.parse(text);
      } catch {
        throw new Error("The feed didn't return JSON.");
      }
      const kept = shrinkSample(body);
      if (!kept) throw new Error("The response is too large to keep as a sample.");
      setSample(kept);
      setFetching({ state: "done" });
    } catch (e) {
      const blocked = e instanceof TypeError; // network failure or a CORS refusal: the browser can't tell us which
      setFetching({
        state: "error",
        message: blocked
          ? "This feed doesn't let browsers read it directly, which is common; the platform can still fetch it. Open the address in a new tab and paste part of the response below."
          : `${e instanceof Error ? e.message : "Couldn't fetch a sample."} Paste part of the response below instead.`,
      });
    }
  };

  const row = (
    key: string,
    label: ReactNode,
    value: string,
    onValue: (v: string) => void,
    opts: { placeholder?: string; required?: boolean; after?: ReactNode; hint?: string } = {},
  ) => {
    const id = `${uid}-${key}`;
    const missing = opts.required && !value.trim();
    const pv = rows[key];
    return (
      <div key={key} className="grid grid-cols-1 gap-x-3 gap-y-1 sm:grid-cols-[10rem_minmax(0,1fr)] sm:items-start">
        <label htmlFor={id} className="flex flex-col pt-1 text-[13px] font-semibold text-slate">
          <span>
            {label}
            {opts.required && <span className="text-danger"> *</span>}
          </span>
          {opts.hint && <span className="text-[11.5px] font-normal leading-snug text-ink">{opts.hint}</span>}
        </label>
        <div className="flex min-w-0 flex-col gap-1">
          <div className="flex min-w-0 items-center gap-2">
            <input
              id={id}
              value={value}
              onChange={(e) => onValue(e.target.value)}
              placeholder={opts.placeholder}
              spellCheck={false}
              autoComplete="off"
              aria-required={opts.required}
              aria-invalid={missing || (pv ? !pv.ok : false)}
              aria-describedby={pv ? `${id}-pv` : undefined}
              className="bld-field is-mono is-sm w-full"
            />
            {opts.after}
          </div>
          <Preview row={pv} id={`${id}-pv`} />
        </div>
      </div>
    );
  };

  const extra = fields ?? [];

  return (
    <div className="bld-card-quiet flex flex-col gap-3 px-4 py-3.5">
      <p className="bld-hint">
        {isList ? "Tell us where each item in the feed keeps its values." : "Tell us where each item in their JSON keeps its values."}{" "}
        Use a dotted path; numbers pick from a list, so{" "}
        <code className="rounded bg-white/70 px-1 text-[11px] text-slate">geometry.coordinates.1</code> is the second
        coordinate (GeoJSON puts longitude first).
      </p>

      <div className="flex flex-col gap-2.5">
        {row("items", "List of items", mapping.items_path ?? "", (v) => set({ items_path: v }), {
          placeholder: isList ? "e.g. features (blank: the whole response)" : "e.g. items (blank: the whole body)",
          hint: "where the list starts",
        })}
        {row("lat", "Latitude", mapping.lat, (v) => set({ lat: v }), { required: true, placeholder: "e.g. lat" })}
        {row("lon", "Longitude", mapping.lon, (v) => set({ lon: v }), { required: true, placeholder: "e.g. lon" })}
        {row("ts", "Time", mapping.ts ?? "", (v) => set({ ts: v }), {
          placeholder: "e.g. time (blank: now)",
          after: (
            <select
              aria-label="Time format"
              value={mapping.ts_format ?? "iso"}
              disabled={!(mapping.ts ?? "").trim()}
              onChange={(e) => set({ ts_format: e.target.value as TsFormat })}
              className="bld-field is-sm w-auto shrink-0"
            >
              {TS_FORMATS.map((f) => (
                <option key={f.value} value={f.value}>
                  {f.label}
                </option>
              ))}
            </select>
          ),
        })}
        {row("user_id", "Sender id", mapping.user_id ?? "", (v) => set({ user_id: v }), {
          placeholder: "e.g. device_id (optional)",
          hint: "which thing sent it",
        })}
        {extra.length > 0 && (
          <div className="mt-1 flex flex-col gap-2.5 border-t border-[rgba(99,143,145,0.25)] pt-3">
            {extra.map((f) =>
              row(
                fieldKey(f.name),
                <>
                  <span className="font-mono">{f.name}</span>
                  {f.required === false && <span className="font-normal text-ink"> · optional</span>}
                </>,
                mapping.fields?.[f.name] ?? "",
                (v) => setField(f.name, v),
                { required: f.required !== false, placeholder: `e.g. properties.${f.name}` },
              ),
            )}
          </div>
        )}
      </div>

      <div className="flex flex-col gap-2 border-t border-[rgba(99,143,145,0.25)] pt-3">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            aria-expanded={showSample}
            aria-controls={`${uid}-sample`}
            onClick={() => setShowSample((v) => !v)}
            className="tap flex w-fit items-center gap-1 rounded-md px-1 text-[13px] font-semibold text-slate transition-colors duration-150 hover:bg-white/60"
          >
            <ChevronDown
              size={13}
              aria-hidden
              className={`transition-transform duration-200 ${showSample ? "rotate-0" : "-rotate-90"}`}
            />
            {isList ? "Check the paths against a sample" : "Paste a sample payload to check the paths"}
          </button>
          {fetchUrl !== undefined && (
            <button
              type="button"
              onClick={() => void fetchSample()}
              disabled={!canFetch || fetching.state === "busy"}
              title={canFetch ? undefined : "Add the web address first"}
              className="bld-btn bld-btn-secondary bld-btn-sm tap"
            >
              {fetching.state === "busy" ? (
                <LoaderCircle size={14} className="animate-spin motion-reduce:animate-none" aria-hidden />
              ) : (
                <Download size={14} aria-hidden />
              )}
              Fetch a sample
            </button>
          )}
        </div>
        {fetching.state === "error" && (
          <p role="status" className="bld-warn">
            {fetching.message}
          </p>
        )}
        {showSample && (
          <div id={`${uid}-sample`} className="flex flex-col gap-1">
            <label htmlFor={`${uid}-sample-text`} className="sr-only">
              Sample JSON
            </label>
            <textarea
              id={`${uid}-sample-text`}
              rows={5}
              value={sample}
              onChange={(e) => {
                setSample(e.target.value);
                if (fetching.state !== "idle") setFetching({ state: "idle" });
              }}
              placeholder='e.g. {"items": [{"lat": 0.5, "lon": 0.5, "time": "2026-01-01T00:00:00Z"}]}'
              spellCheck={false}
              aria-describedby={`${uid}-sample-hint`}
              aria-invalid={preview.state === "bad"}
              className="bld-field is-mono is-sm w-full resize-y text-[12px] leading-relaxed"
            />
            <p
              id={`${uid}-sample-hint`}
              aria-live="polite"
              className={`text-[11.5px] ${preview.state === "bad" ? "text-danger" : "text-ink"}`}
            >
              {preview.state === "bad"
                ? preview.error
                : preview.state === "ok"
                  ? `${preview.itemCount} item${preview.itemCount === 1 ? "" : "s"} found${fetching.state === "done" ? " in the fetched sample" : ""}; the previews above show the first. It stays in this browser and is never sent.`
                  : isList
                    ? "Paste part of what the address returns. It stays in this browser and is never sent."
                    : "Copy one request body the other service sends. It stays in this browser and is never sent."}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
