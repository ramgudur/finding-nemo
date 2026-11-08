// One push input on the keys page: its endpoint and header, the body schema built from its declared
// fields and one tabbed block of ready-to-run examples. Behind "More detail": the responses ingest can
// return and (when the input has a webhook mapping) the mapping, a sample payload, its example, receipt
// and limits. The webhook URL itself stays up front, next to the events endpoint.
// Response texts mirror backend/app/api/ingest.py and webhooks.py exactly.
import { Send, Webhook } from "lucide-react";
import type { AppSpec, InputSpec, WebhookMapping } from "../../api/types";
import { webhookMappingParts } from "../sources/WebhookSummary";
import { displayJson, sampleEventBody, sampleWebhookBody, snippet, type Lang } from "./snippets";
import { CodeBlock, CodeTabs, CopyLine, Disclosure, Label, RefTable, mono } from "./ui";

export function eventsUrl(apiBase: string, appId: string, inputId: string): string {
  return `${apiBase}/v1/apps/${encodeURIComponent(appId)}/inputs/${encodeURIComponent(inputId)}/events`;
}

export function webhookUrl(apiBase: string, appId: string, inputId: string): string {
  return `${apiBase}/v1/apps/${encodeURIComponent(appId)}/webhooks/${encodeURIComponent(inputId)}`;
}

const fmtDeg = (v: number) => String(Number(v.toFixed(6)));

const code = (s: string) => <code className={mono}>{s}</code>;

function SchemaTable({ spec, input }: { spec: AppSpec; input: InputSpec }) {
  const b = spec.grid.bbox;
  const type = (t: string, r: boolean) => (
    <span className="whitespace-nowrap">
      {t} · {r ? <span className="font-bold text-slate">required</span> : "optional"}
    </span>
  );
  const rows = [
    ["user_id", type("string", false), "An id per person or device. Trust and the spoof checks are kept per id; leave it out for anonymous data."],
    ["lat", type("number", true), `Latitude, WGS84 degrees, from ${fmtDeg(b.min_lat)} to ${fmtDeg(b.max_lat)} (your app's area).`],
    ["lon", type("number", true), `Longitude, WGS84 degrees, from ${fmtDeg(b.min_lon)} to ${fmtDeg(b.max_lon)}.`],
    ["ts", type("string", true), "When it happened: ISO-8601 in UTC, e.g. 2026-01-01T12:00:00Z."],
    ...(input.fields ?? []).map((f) => [f.name, type(f.type, f.required ?? true), f.description || "Declared by you in the Builder."]),
  ];
  return (
    <div className="space-y-1.5">
      <RefTable label={`${input.id} request body`} head={["Field", "Type", "What it is"]} rows={rows} />
      <p className="text-[13.5px] text-ink">Send JSON. Any key not listed here is rejected.</p>
    </div>
  );
}

const EVENT_RESPONSES: string[][] = [
  ["202", `{"status": "accepted"}`, "Stored. It reaches the map at the next tick."],
  ["401", "invalid ingest key", "X-Ingest-Key is missing or wrong. The key is checked first."],
  ["403", "app is not active", "The app isn't confirmed and activated."],
  ["404", "app or input not found", "Check the app and input ids in the URL (this input must be push)."],
  ["422", "position outside the app's area", "lat/lon fall outside the area above."],
  ["422", "undeclared field(s): ['…'] · missing required field '…' · field '…' must be <type>", "The body doesn't match the schema."],
];

function Responses({ rows, label }: { rows: string[][]; label: string }) {
  return (
    <RefTable
      label={label}
      head={["Status", "detail", "Meaning"]}
      rows={rows.map(([s, d, m]) => [s, <code className={`${mono} break-words`}>{d}</code>, m])}
    />
  );
}

const WEBHOOK_RESPONSES: string[][] = [
  ["202", `{"received": n, "accepted": n, "rejected": [{"index", "reason"}]}`, "Each item is checked like an event; bad items are listed (first 20) and the rest are kept."],
  ["401", "invalid ingest key", "Missing or wrong key (header or ?key=)."],
  ["403", "app is not active", "The app isn't activated."],
  ["404", "app or input not found", "Check the ids in the URL."],
  ["409", "input is not a push input with a webhook mapping", "This input takes /events only."],
  ["413", "body over 1000000 bytes · over 500 items in one call", "Split the batch."],
  ["422", "body is not valid JSON: … · items_path: …", "The body can't be read with your mapping."],
];

/** Up front: where another service's webhook posts. */
function WebhookEntry({ url }: { url: string }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Webhook aria-hidden className="h-4 w-4 text-deep" />
        <Label>Or point another service's webhook here</Label>
      </div>
      <p className="max-w-[68ch] text-[13.5px] text-ink">
        It sends its own JSON; your mapping picks out each value. Send the key as {code("X-Ingest-Key")}, or append{" "}
        {code("?key=…")} for senders that can't set headers (a query-string key can end up in their logs).
      </p>
      <CopyLine value={url}>
        <span className="font-semibold">POST</span> {url}
      </CopyLine>
    </div>
  );
}

/** Behind "More detail": the webhook's mapping, sample payload, example, responses and limits. */
function WebhookDetail(props: { spec: AppSpec; input: InputSpec; w: WebhookMapping; url: string; ingestKey: string; lang: Lang; setLang: (l: Lang) => void }) {
  const { spec, input, w, url, ingestKey, lang, setLang } = props;
  const body = sampleWebhookBody(spec, input, w);
  const fmt = w.ts_format ?? "iso";
  return (
    <div className="space-y-3">
      <Label>Webhook mapping</Label>
      <RefTable
        label={`${input.id} webhook mapping`}
        head={["Reads", "From path"]}
        rows={webhookMappingParts(w).map(([k, v]) => [k, <code className={`${mono} break-all`}>{v}</code>])}
      />
      <Label>Sample webhook payload</Label>
      <CodeBlock code={displayJson(body, fmt)} label={`${input.id} sample webhook payload`} />
      <Label>Send it</Label>
      <CodeTabs lang={lang} onChange={setLang} idPrefix={`wh-${input.id}`} code={snippet(lang, url, body, fmt, ingestKey)} label={`${lang} webhook example for ${input.id}`} />
      <Label>Webhook responses</Label>
      <Responses rows={WEBHOOK_RESPONSES} label={`${input.id} webhook responses`} />
      <p className="text-[13.5px] text-ink">Limits: 1 MB per request, 500 items per call.</p>
    </div>
  );
}

export function PushInputGuide(props: {
  spec: AppSpec;
  input: InputSpec;
  apiBase: string;
  ingestKey: string;
  lang: Lang;
  setLang: (l: Lang) => void;
  defaultOpen: boolean;
}) {
  const { spec, input, apiBase, ingestKey, lang, setLang, defaultOpen } = props;
  const url = eventsUrl(apiBase, spec.app_id, input.id);
  const body = sampleEventBody(spec, input);
  const nFields = (input.fields ?? []).length;
  return (
    <Disclosure
      defaultOpen={defaultOpen}
      summary={
        <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          <Send aria-hidden className="h-4 w-4 text-deep" />
          <span className="font-mono text-[14px] font-semibold text-slate">{input.id}</span>
          {input.meaning && <span className="line-clamp-1 text-[13.5px] italic text-ink">“{input.meaning}”</span>}
        </span>
      }
      meta={`${input.shape} · ${nFields} field${nFields === 1 ? "" : "s"}${input.webhook ? " · webhook" : ""}`}
    >
      <div className="space-y-2">
        <Label>Endpoint</Label>
        <CopyLine value={url}>
          <span className="font-semibold">POST</span> {url}
        </CopyLine>
        <CopyLine value={`X-Ingest-Key: ${ingestKey}`}>
          <span className="text-ink">Header</span> X-Ingest-Key: {ingestKey}
        </CopyLine>
      </div>
      <div className="space-y-2">
        <Label>Body</Label>
        <SchemaTable spec={spec} input={input} />
      </div>
      <div className="space-y-2">
        <Label>Example</Label>
        <CodeTabs lang={lang} onChange={setLang} idPrefix={`ev-${input.id}`} code={snippet(lang, url, body, "iso", ingestKey)} label={`${lang} example for ${input.id}`} />
        <p className="text-[13.5px] text-ink">One event at the centre of your area, stamped with the time you run it. Swap in real values.</p>
      </div>
      {input.webhook && <WebhookEntry url={webhookUrl(apiBase, spec.app_id, input.id)} />}
      <Disclosure quiet summary="More detail">
        <div className="space-y-2">
          <Label>Responses</Label>
          <Responses rows={EVENT_RESPONSES} label={`${input.id} responses`} />
        </div>
        {input.webhook && (
          <WebhookDetail
            spec={spec}
            input={input}
            w={input.webhook}
            url={webhookUrl(apiBase, spec.app_id, input.id)}
            ingestKey={ingestKey}
            lang={lang}
            setLang={setLang}
          />
        )}
      </Disclosure>
    </Disclosure>
  );
}
