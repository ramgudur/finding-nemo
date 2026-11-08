// The mapping for a push source that receives ANOTHER service's JSON (POST /v1/apps/{app}/webhooks/{input}):
// where the list of items is, and where each item keeps its latitude, longitude, time, sender id and declared
// fields. A pasted sample previews what every path resolves to for the first item. Data only (rule 10): the
// sample goes through JSON.parse and a plain dotted-path lookup, never eval.
import { Check, ChevronDown, CircleAlert } from "lucide-react";
import { useId, useState } from "react";
import type { ReactNode } from "react";
import type { InputField, TsFormat, WebhookMapping } from "../../api/types";
import { useBuilderStore } from "../../store/useBuilderStore";
import { TS_FORMATS, fieldKey, previewWebhook } from "./sourceUtils";
import type { PreviewRow } from "./sourceUtils";

interface Props {
  sourceId: string;
  mapping: WebhookMapping;
  fields: InputField[] | undefined;
  onChange: (m: WebhookMapping) => void;
}

const pathInput =
  "min-w-0 w-full rounded-lg border bg-white/60 px-2.5 py-1 font-mono text-[13px] text-slate placeholder:text-inkSoft transition-colors duration-150 focus:bg-white/90";

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

export function WebhookEditor({ sourceId, mapping, fields, onChange }: Props) {
  const uid = useId();
  const sample = useBuilderStore((s) => s.webhookSamples[sourceId] ?? "");
  const setSample = useBuilderStore((s) => s.setWebhookSample);
  const [showSample, setShowSample] = useState(sample.trim().length > 0);
  const preview = previewWebhook(mapping, fields, sample);
  const rows = preview.state === "ok" ? preview.rows : {};
  const set = (patch: Partial<WebhookMapping>) => onChange({ ...mapping, ...patch });
  const setField = (name: string, path: string) => set({ fields: { ...(mapping.fields ?? {}), [name]: path } });

  const row = (
    key: string,
    label: ReactNode,
    value: string,
    onValue: (v: string) => void,
    opts: { placeholder?: string; required?: boolean; after?: ReactNode } = {},
  ) => {
    const id = `${uid}-${key}`;
    const missing = opts.required && !value.trim();
    const pv = rows[key];
    return (
      <div key={key} className="grid grid-cols-1 gap-x-3 gap-y-1 sm:grid-cols-[9.5rem_minmax(0,1fr)] sm:items-start">
        <label htmlFor={id} className="pt-1 text-xs text-slate">
          {label}
          {opts.required && <span className="text-danger"> *</span>}
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
              className={`${pathInput} ${missing || (pv && !pv.ok) ? "border-danger/50" : "border-white/70"}`}
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
    <div className="flex flex-col gap-3 rounded-xl border border-white/60 bg-white/35 px-3.5 py-3">
      <p className="text-[12px] leading-snug text-ink">
        Point at each value with a dotted path; numbers index into lists, so{" "}
        <code className="rounded bg-white/60 px-1 font-mono text-[11px] text-slate">geometry.coordinates.1</code> is
        the second coordinate.
      </p>

      <div className="flex flex-col gap-2">
        {row("items", "List of items", mapping.items_path ?? "", (v) => set({ items_path: v }), {
          placeholder: "blank: the body is one item or the list",
        })}
        {row("lat", "Latitude", mapping.lat, (v) => set({ lat: v }), { required: true, placeholder: "e.g. lat" })}
        {row("lon", "Longitude", mapping.lon, (v) => set({ lon: v }), { required: true, placeholder: "e.g. lon" })}
        {row("ts", "Time", mapping.ts ?? "", (v) => set({ ts: v }), {
          placeholder: "blank: the time it arrives",
          after: (
            <select
              aria-label="Time format"
              value={mapping.ts_format ?? "iso"}
              disabled={!(mapping.ts ?? "").trim()}
              onChange={(e) => set({ ts_format: e.target.value as TsFormat })}
              className="shrink-0 rounded-lg border border-white/70 bg-white/60 px-1.5 py-1 text-xs text-slate disabled:opacity-50"
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
          placeholder: "optional: which entity sent it",
        })}
        {extra.length > 0 && (
          <div className="mt-1 flex flex-col gap-2 border-t border-white/60 pt-2.5">
            {extra.map((f) =>
              row(
                fieldKey(f.name),
                <>
                  <span className="font-mono">{f.name}</span>
                  {f.required === false && <span className="text-ink"> · optional</span>}
                </>,
                mapping.fields?.[f.name] ?? "",
                (v) => setField(f.name, v),
                { required: f.required !== false, placeholder: `path to ${f.name}` },
              ),
            )}
          </div>
        )}
      </div>

      <div className="flex flex-col gap-1.5 border-t border-white/60 pt-2.5">
        <button
          type="button"
          aria-expanded={showSample}
          aria-controls={`${uid}-sample`}
          onClick={() => setShowSample((v) => !v)}
          className="flex w-fit items-center gap-1 rounded-md px-1 text-xs font-semibold text-slate transition-colors duration-150 hover:bg-white/60"
        >
          <ChevronDown
            size={13}
            aria-hidden
            className={`transition-transform duration-200 ${showSample ? "rotate-0" : "-rotate-90"}`}
          />
          Paste a sample payload to check the paths
        </button>
        {showSample && (
          <div id={`${uid}-sample`} className="flex flex-col gap-1">
            <label htmlFor={`${uid}-sample-text`} className="sr-only">
              Sample JSON payload
            </label>
            <textarea
              id={`${uid}-sample-text`}
              rows={5}
              value={sample}
              onChange={(e) => setSample(sourceId, e.target.value)}
              placeholder='{"items": [{"lat": 0.5, "lon": 0.5, "time": "2026-01-01T00:00:00Z"}]}'
              spellCheck={false}
              aria-describedby={`${uid}-sample-hint`}
              aria-invalid={preview.state === "bad"}
              className={`resize-y rounded-lg border bg-white/60 px-2.5 py-1.5 font-mono text-[12px] leading-relaxed text-slate placeholder:text-inkSoft focus:bg-white/90 ${
                preview.state === "bad" ? "border-danger/50" : "border-white/70"
              }`}
            />
            <p
              id={`${uid}-sample-hint`}
              aria-live="polite"
              className={`text-[11px] ${preview.state === "bad" ? "text-danger" : "text-ink"}`}
            >
              {preview.state === "bad"
                ? preview.error
                : preview.state === "ok"
                  ? `${preview.itemCount} item${preview.itemCount === 1 ? "" : "s"} found; the previews above show the first. It stays in this browser and is never sent.`
                  : "Copy one request body the other service sends. It stays in this browser and is never sent."}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
