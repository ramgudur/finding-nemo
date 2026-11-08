// /apps/:appId/keys: the success moment after activation. Shows the ingest and authority keys once
// (from this tab's ActivateResponse; the platform can't show them again), then how to send the first
// event to each push input and what each pull input polls. Never echoes or logs a key elsewhere.
import { ArrowRight, KeyRound, Link2, Send, TriangleAlert } from "lucide-react";
import { useEffect, useState, type KeyboardEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { loadKey, saveKey } from "../api/authorityKey";
import { api } from "../api/client";
import type { AppSpec, FieldType, InputSpec, WebhookMapping } from "../api/types";
import { AppShell, Breadcrumb, CopyButton, StatusChip, primaryBtn } from "../components/app/AppShell";
import { webhookMappingParts } from "../components/sources/WebhookSummary";
import { useBuilderStore } from "../store/useBuilderStore";

const INGEST_PLACEHOLDER = "<your ingest key>";

function sampleValue(type: FieldType): unknown {
  return type === "number" ? 1.0 : type === "integer" ? 1 : type === "boolean" ? true : "example";
}

/** Where the examples send events: VITE_API_BASE when the frontend is built for a separate API host,
 * else this page's origin (the dev server, or a reverse proxy, forwards /v1 to the backend). */
const API_BASE_ENV = ((import.meta.env.VITE_API_BASE as string | undefined) ?? "").trim().replace(/\/+$/, "");
/** Where end users open the /go page (a public host or tunnel); else this page's origin. */
const PUBLIC_BASE_ENV = ((import.meta.env.VITE_PUBLIC_BASE as string | undefined) ?? "").trim().replace(/\/+$/, "");

type Shell = "bash" | "powershell";
const SHELLS: Array<{ id: Shell; label: string }> = [
  { id: "bash", label: "bash / zsh" },
  { id: "powershell", label: "PowerShell" },
];

function sampleBody(spec: AppSpec, input: InputSpec): Record<string, unknown> {
  const b = spec.grid.bbox;
  const body: Record<string, unknown> = {
    user_id: "device-1",
    lat: Number(((b.min_lat + b.max_lat) / 2).toFixed(6)),
    lon: Number(((b.min_lon + b.max_lon) / 2).toFixed(6)),
  };
  for (const f of input.fields ?? []) body[f.name] = sampleValue(f.type);
  return body;
}

const TS_TOKEN = "__TS_NOW__";

/** bash: the body is single-quoted, and ts is spliced in from `date -u` when the command runs. */
function curlBash(url: string, body: Record<string, unknown>, ingestKey: string): string {
  const json = JSON.stringify({ ...body, ts: TS_TOKEN })
    .replace(/'/g, `'\\''`)
    .replace(`"${TS_TOKEN}"`, `"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'"`);
  return [
    `curl -X POST "${url}" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -H "X-Ingest-Key: ${ingestKey}" \\`,
    `  -d '${json}'`,
  ].join("\n");
}

const psStr = (v: string) => `'${v.replace(/'/g, "''")}'`;
const psVal = (v: unknown) =>
  typeof v === "boolean" ? (v ? "$true" : "$false") : typeof v === "number" ? String(v) : psStr(String(v));

/** PowerShell (5.1 and 7): a hashtable through ConvertTo-Json, ts taken from the clock when it runs. */
function irmPowerShell(url: string, body: Record<string, unknown>, ingestKey: string): string {
  const lines = Object.entries(body).map(([k, v]) => `  ${psStr(k)} = ${psVal(v)}`);
  lines.push(`  'ts' = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')`);
  return [
    `$body = @{`,
    ...lines,
    `} | ConvertTo-Json`,
    `Invoke-RestMethod -Method Post -Uri ${psStr(url)} \``,
    `  -ContentType 'application/json' \``,
    `  -Headers @{ 'X-Ingest-Key' = ${psStr(ingestKey)} } \``,
    `  -Body $body`,
  ].join("\n");
}

function exampleFor(shell: Shell, base: string, spec: AppSpec, input: InputSpec, ingestKey: string): string {
  const url = `${base}/v1/apps/${encodeURIComponent(spec.app_id)}/inputs/${encodeURIComponent(input.id)}/events`;
  const body = sampleBody(spec, input);
  return shell === "bash" ? curlBash(url, body, ingestKey) : irmPowerShell(url, body, ingestKey);
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

/** A minimal body the mapping reads: one item at the grid centre, wrapped at items_path when set. */
function sampleWebhookBody(spec: AppSpec, input: InputSpec, w: WebhookMapping): unknown {
  const b = spec.grid.bbox;
  const item: Record<string, unknown> = {};
  setPath(item, w.lat, Number(((b.min_lat + b.max_lat) / 2).toFixed(6)));
  setPath(item, w.lon, Number(((b.min_lon + b.max_lon) / 2).toFixed(6)));
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

type TsFmt = WebhookMapping["ts_format"];

/** bash: the pretty JSON body, single-quoted; the time is spliced in from `date` when the command runs. */
function webhookBash(url: string, body: unknown, fmt: TsFmt, ingestKey: string): string {
  const now =
    fmt === "epoch_s"
      ? `'"$(date +%s)"'`
      : fmt === "epoch_ms"
        ? `'"$(date +%s)000"'`
        : `"'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'"`;
  const json = JSON.stringify(body, null, 2).replace(/'/g, `'\\''`).replace(`"${TS_TOKEN}"`, now);
  return [
    `curl -X POST "${url}" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -H "X-Ingest-Key: ${ingestKey}" \\`,
    `  -d '${json}'`,
  ].join("\n");
}

/** A PowerShell literal for a JSON value: hashtables and @() lists, the time as a clock expression. */
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
    return `@{\n${rows.join("\n")}\n${pad}}`;
  }
  return v === null ? "$null" : psVal(v);
}

/** PowerShell (5.1 and 7): the nested body through ConvertTo-Json -Depth, so lists stay lists. */
function webhookPowerShell(url: string, body: unknown, fmt: TsFmt, ingestKey: string): string {
  return [
    `$body = ${psLiteral(body, fmt, "")}`,
    `Invoke-RestMethod -Method Post -Uri ${psStr(url)} \``,
    `  -ContentType 'application/json' \``,
    `  -Headers @{ 'X-Ingest-Key' = ${psStr(ingestKey)} } \``,
    `  -Body (ConvertTo-Json -InputObject $body -Depth 20)`,
  ].join("\n");
}

function webhookExampleFor(shell: Shell, url: string, spec: AppSpec, input: InputSpec, w: WebhookMapping, key: string): string {
  const body = sampleWebhookBody(spec, input, w);
  const fmt = w.ts_format ?? "iso";
  return shell === "bash" ? webhookBash(url, body, fmt, key) : webhookPowerShell(url, body, fmt, key);
}

/** The webhook URL for one push input, what its mapping reads, and an example that sends a matching body. */
function WebhookBlock(props: { spec: AppSpec; input: InputSpec; w: WebhookMapping; shell: Shell; url: string; ingestKey: string }) {
  const { spec, input, w, shell, url, ingestKey } = props;
  return (
    <div className="space-y-2 border-t border-inkSoft/25 pt-3">
      <p className="text-[13px] text-slate">
        <span className="font-semibold">Webhook URL</span>{" "}
        <span className="text-ink">for another service's JSON, read with your mapping (ingest key as X-Ingest-Key or ?key=)</span>
      </p>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 break-all rounded-lg bg-white/70 px-2.5 py-1.5 font-mono text-[12.5px] text-slate">{url}</code>
        <CopyButton value={url} />
      </div>
      <dl aria-label={`${input.id} webhook mapping`} className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[12.5px]">
        {webhookMappingParts(w).map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-ink">{k}</dt>
            <dd className="min-w-0 break-all font-mono text-slate">← {v}</dd>
          </div>
        ))}
      </dl>
      <p className="max-w-[70ch] text-[12.5px] text-ink">
        The example sends one item shaped like your mapping, at the centre of your grid, stamped with the time you run it.
      </p>
      <CodeBlock
        code={webhookExampleFor(shell, url, spec, input, w, ingestKey)}
        label={`${shell === "bash" ? "curl" : "PowerShell"} webhook example for ${input.id}`}
      />
    </div>
  );
}

function ShellTabs({ shell, onChange }: { shell: Shell; onChange: (s: Shell) => void }) {
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const next = shell === "bash" ? "powershell" : "bash";
    onChange(next);
    document.getElementById(`shell-tab-${next}`)?.focus();
  };
  return (
    <div role="tablist" aria-label="Example shell" onKeyDown={onKey} className="inline-flex gap-1 rounded-full bg-white/60 p-1">
      {SHELLS.map(({ id, label }) => {
        const on = shell === id;
        return (
          <button
            key={id}
            id={`shell-tab-${id}`}
            role="tab"
            type="button"
            aria-selected={on}
            tabIndex={on ? 0 : -1}
            onClick={() => onChange(id)}
            className={`tap rounded-full px-3 py-1 text-[12.5px] font-semibold transition-colors duration-150 ${
              on ? "bg-cream text-slate shadow-sm ring-1 ring-paleWater" : "text-ink hover:text-slate"
            }`}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}

function KeyField({ label, hint, value }: { label: string; hint: string; value: string }) {
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <span className="text-[14px] font-semibold text-slate">{label}</span>
        <span className="text-[12.5px] text-ink">{hint}</span>
      </div>
      <div className="flex items-stretch gap-2">
        <input
          readOnly
          value={value}
          aria-label={label}
          onFocus={(e) => e.currentTarget.select()}
          className="min-w-0 flex-1 rounded-xl border border-paleWater bg-white/80 px-3 py-2 font-mono text-[13px] text-slate"
        />
        <CopyButton value={value} />
      </div>
    </div>
  );
}

function CodeBlock({ code, label }: { code: string; label: string }) {
  return (
    <div className="relative">
      <pre aria-label={label} className="overflow-x-auto rounded-xl bg-slate/90 p-3 pr-20 font-mono text-[12px] leading-relaxed text-cream">
        {code}
      </pre>
      <CopyButton value={code} className="absolute right-2 top-2" />
    </div>
  );
}

export default function AppKeys() {
  const { appId = "" } = useParams<{ appId: string }>();
  const navigate = useNavigate();
  const activation = useBuilderStore((s) => (s.activation?.app_id === appId ? s.activation : null));
  const compiledSpec = useBuilderStore((s) => (s.compiledSpec?.app_id === appId ? s.compiledSpec : null));
  const [spec, setSpec] = useState<AppSpec | null>(compiledSpec);
  const [remember, setRemember] = useState(() => loadKey(appId).remembered);
  const authorityKey = activation?.authority_key ?? loadKey(appId).key;

  // the inputs (for the endpoints) come from this tab's spec, else the server with the authority key
  useEffect(() => {
    if (spec || !authorityKey) return;
    let alive = true;
    api.getApp(appId, authorityKey).then(
      (s) => alive && setSpec(s),
      () => undefined, // a wrong or forgotten key: the endpoints stay hidden, never guessed
    );
    return () => {
      alive = false;
    };
  }, [appId, authorityKey, spec]);

  const [shell, setShell] = useState<Shell>(() => (/Windows/i.test(navigator.userAgent) ? "powershell" : "bash"));
  const origin = window.location.origin;
  const apiBase = API_BASE_ENV || origin;
  const consoleHref = `/apps/${encodeURIComponent(appId)}`;
  const push = (spec?.inputs ?? []).filter((i) => i.kind === "push");
  const pull = (spec?.inputs ?? []).filter((i) => i.kind === "pull");
  // Always shown (it doesn't need the spec, which is fetched with the key); the /go page handles routing-off.
  const publicBase = PUBLIC_BASE_ENV || origin;
  const publicLink = `${publicBase}/apps/${encodeURIComponent(appId)}/go`;
  const localPublic = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:|\/|$)/i.test(publicBase);
  const ingestKey = activation?.ingest_key ?? INGEST_PLACEHOLDER;

  const openConsole = () => {
    // the keys were shown; this tab no longer holds the ingest key (the authority key stays as chosen)
    useBuilderStore.getState().setActivation(null);
    navigate(consoleHref);
  };

  return (
    <AppShell>
      <header className="space-y-2">
        <Breadcrumb appId={appId} />
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="text-[28px] font-bold leading-tight text-slate">
            <span className="font-mono">{appId}</span> is live
          </h1>
          <StatusChip live />
        </div>
        <p role="status" aria-live="polite" className="max-w-[70ch] text-[15px] text-ink">
          {activation
            ? "Your tiers, locked weights and verification order are now locked. Save both keys below: they are shown only this once."
            : "Keys are shown only once, right after activation, and this page no longer has them."}
        </p>
      </header>

      {activation ? (
        <section aria-labelledby="k-keys" className="space-y-4 rounded-3xl bg-cream/80 p-5 shadow-[0_10px_30px_-16px_rgba(53,89,107,0.45)] sm:p-6">
          <div className="flex items-center gap-2">
            <KeyRound aria-hidden className="h-4 w-4 text-mutedTeal" />
            <h2 id="k-keys" className="text-[17px] font-bold text-slate">
              Your keys
            </h2>
          </div>
          <p className="flex items-start gap-2 rounded-xl bg-coral/20 px-3 py-2 text-[13.5px] text-[#7A4232]">
            <TriangleAlert aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
            Copy them now. The platform can't show these keys again, and there is no reset.
          </p>
          <KeyField label="Ingest key" hint="your app sends it as X-Ingest-Key" value={activation.ingest_key} />
          <KeyField label="Authority key" hint="opens the console's authority view" value={activation.authority_key} />
          <label className="flex items-center gap-2 text-[13.5px] text-slate">
            <input
              type="checkbox"
              className="h-4 w-4 accent-mutedTeal"
              checked={remember}
              onChange={(e) => {
                setRemember(e.target.checked);
                saveKey(appId, activation.authority_key, e.target.checked);
              }}
            />
            Remember the authority key on this device
            <span className="text-ink">{remember ? "(kept in this browser until you forget it)" : "(this tab only)"}</span>
          </label>
        </section>
      ) : (
        <section className="space-y-3 rounded-3xl bg-cream/70 p-5 sm:p-6">
          <h2 className="text-[17px] font-bold text-slate">Lost the keys?</h2>
          <p className="max-w-[70ch] text-[14px] text-ink">
            If you saved the authority key, paste it in the console to unlock the authority view. Without it, the console shows the public view only.
          </p>
          <Link to={consoleHref} className={primaryBtn}>
            Open the console <ArrowRight aria-hidden className="h-4 w-4" />
          </Link>
        </section>
      )}

      {spec && (push.length > 0 || pull.length > 0) && (
        <section aria-labelledby="k-first" className="space-y-4">
          <div>
            <h2 id="k-first" className="text-[17px] font-bold text-slate">
              Send your first event
            </h2>
            <p className="max-w-[70ch] text-[14px] text-ink">
              Each push input has its own endpoint. The example posts one event at the centre of your grid, stamped with the time you run it; swap in real values.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <ShellTabs shell={shell} onChange={setShell} />
            <p className="font-mono text-[12px] text-ink">
              API base <span className="break-all text-slate">{apiBase}</span>
              {!API_BASE_ENV && import.meta.env.DEV && " · this dev server proxies /v1 to the backend"}
            </p>
          </div>
          {push.map((i) => (
            <article key={i.id} className="space-y-3 rounded-2xl bg-white/45 p-4">
              <div className="flex flex-wrap items-center gap-2">
                <Send aria-hidden className="h-4 w-4 text-mutedTeal" />
                <h3 className="font-mono text-[14px] font-semibold text-slate">{i.id}</h3>
                <span className="text-[12.5px] text-ink">push · {i.shape}</span>
              </div>
              {i.meaning && <p className="max-w-[70ch] text-[13px] italic text-ink">“{i.meaning}”</p>}
              <p className="break-all font-mono text-[12.5px] text-slate">
                POST /v1/apps/{appId}/inputs/{i.id}/events
              </p>
              <CodeBlock
                code={exampleFor(shell, apiBase, spec, i, ingestKey)}
                label={`${shell === "bash" ? "curl" : "PowerShell"} example for ${i.id}`}
              />
              {i.webhook && (
                <WebhookBlock
                  spec={spec}
                  input={i}
                  w={i.webhook}
                  shell={shell}
                  url={`${apiBase}/v1/apps/${encodeURIComponent(appId)}/webhooks/${encodeURIComponent(i.id)}`}
                  ingestKey={ingestKey}
                />
              )}
            </article>
          ))}
          {pull.map((i) => (
            <article key={i.id} className="space-y-1.5 rounded-2xl bg-white/45 p-4">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="font-mono text-[14px] font-semibold text-slate">{i.id}</h3>
                <span className="text-[12.5px] text-ink">pull · {i.shape} · nothing to send</span>
              </div>
              <p className="text-[13.5px] text-slate">
                {i.connector ? (
                  <>
                    The platform polls{" "}
                    <span className="font-mono">
                      {i.connector.type}
                      {i.connector.preset ? ` (${i.connector.preset})` : ""}
                    </span>
                    {i.connector.url && (
                      <>
                        {" "}
                        at <span className="break-all font-mono text-[12.5px]">{i.connector.url}</span>
                      </>
                    )}{" "}
                    every {i.connector.poll_s ?? 300} s.
                  </>
                ) : (
                  "No connector is set yet, so nothing is polled."
                )}
              </p>
            </article>
          ))}
        </section>
      )}

      <section aria-labelledby="k-public" className="space-y-2 rounded-2xl bg-white/45 p-4">
        <div className="flex items-center gap-2">
          <Link2 aria-hidden className="h-4 w-4 text-mutedTeal" />
          <h2 id="k-public" className="text-[15px] font-bold text-slate">
            Public end-user link
          </h2>
        </div>
        <p className="max-w-[70ch] text-[13.5px] text-ink">
          Share it with end users: they get route instructions only, never your data. If routing is off for this app, the page says so.
        </p>
        <div className="flex items-center gap-2">
          <a
            href={publicLink}
            target="_blank"
            rel="noreferrer"
            className="min-w-0 flex-1 break-all rounded-lg bg-white/70 px-2.5 py-1.5 font-mono text-[12.5px] text-slate underline decoration-mutedTeal/50 underline-offset-2 transition-colors hover:decoration-slate"
          >
            {publicLink}
          </a>
          <CopyButton value={publicLink} />
        </div>
        {localPublic && (
          <p className="max-w-[70ch] text-[12.5px] text-ink">
            Share it via your public host or a tunnel (e.g. cloudflared): a localhost link only works on this computer. Set VITE_PUBLIC_BASE to put the public address here.
          </p>
        )}
      </section>

      {activation && (
        <div className="flex flex-wrap items-center justify-end gap-3 pt-2">
          <p className="text-[13px] text-ink">Leaving this page hides the keys for good.</p>
          <button type="button" onClick={openConsole} className={primaryBtn}>
            I've saved my keys, open the console <ArrowRight aria-hidden className="h-4 w-4" />
          </button>
        </div>
      )}
    </AppShell>
  );
}
