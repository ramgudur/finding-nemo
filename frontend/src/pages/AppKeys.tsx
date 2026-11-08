// /apps/:appId/keys: the success moment after activation. Shows the ingest and authority keys once
// (from this tab's ActivateResponse; the platform can't show them again), then how to send the first
// event to each push input and what each pull input polls. Never echoes or logs a key elsewhere.
import { ArrowRight, KeyRound, Link2, Send, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { loadKey, saveKey } from "../api/authorityKey";
import { api } from "../api/client";
import type { AppSpec, FieldType, InputSpec } from "../api/types";
import { AppShell, Breadcrumb, CopyButton, StatusChip, primaryBtn } from "../components/app/AppShell";
import { useBuilderStore } from "../store/useBuilderStore";

const INGEST_PLACEHOLDER = "<your ingest key>";

function sampleValue(type: FieldType): unknown {
  return type === "number" ? 1.0 : type === "integer" ? 1 : type === "boolean" ? true : "example";
}

function curlFor(origin: string, spec: AppSpec, input: InputSpec, ingestKey: string): string {
  const b = spec.grid.bbox;
  const body: Record<string, unknown> = {
    user_id: "device-1",
    lat: Number(((b.min_lat + b.max_lat) / 2).toFixed(6)),
    lon: Number(((b.min_lon + b.max_lon) / 2).toFixed(6)),
    ts: new Date().toISOString().replace(/\.\d+Z$/, "Z"),
  };
  for (const f of input.fields ?? []) body[f.name] = sampleValue(f.type);
  const url = `${origin}/v1/apps/${encodeURIComponent(spec.app_id)}/inputs/${encodeURIComponent(input.id)}/events`;
  return [
    `curl -X POST "${url}" \\`,
    `  -H "Content-Type: application/json" \\`,
    `  -H "X-Ingest-Key: ${ingestKey}" \\`,
    `  -d '${JSON.stringify(body)}'`,
  ].join("\n");
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

  const origin = window.location.origin;
  const consoleHref = `/apps/${encodeURIComponent(appId)}`;
  const push = (spec?.inputs ?? []).filter((i) => i.kind === "push");
  const pull = (spec?.inputs ?? []).filter((i) => i.kind === "pull");
  const publicLink = spec?.routing?.enabled ? `${origin}/apps/${encodeURIComponent(appId)}/go` : null;
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
              Each push input has its own endpoint. The example posts one event at the centre of your grid; swap in real values.
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
              <CodeBlock code={curlFor(origin, spec, i, ingestKey)} label={`curl example for ${i.id}`} />
              {i.webhook && (
                <div className="space-y-1">
                  <p className="text-[13px] text-slate">
                    <span className="font-semibold">Webhook URL</span>{" "}
                    <span className="text-ink">for another service's JSON, mapped by your field paths (ingest key as X-Ingest-Key or ?key=)</span>
                  </p>
                  <div className="flex items-center gap-2">
                    <code className="min-w-0 flex-1 break-all rounded-lg bg-white/70 px-2.5 py-1.5 font-mono text-[12.5px] text-slate">
                      {origin}/v1/apps/{appId}/webhooks/{i.id}
                    </code>
                    <CopyButton value={`${origin}/v1/apps/${encodeURIComponent(appId)}/webhooks/${encodeURIComponent(i.id)}`} />
                  </div>
                  <p className="font-mono text-[12px] text-ink">
                    lat ← {i.webhook.lat} · lon ← {i.webhook.lon}
                    {i.webhook.items_path ? ` · items ← ${i.webhook.items_path}` : ""}
                    {i.webhook.ts ? ` · ts ← ${i.webhook.ts}` : ""}
                  </p>
                </div>
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

      {publicLink && (
        <section aria-labelledby="k-public" className="space-y-2 rounded-2xl bg-white/45 p-4">
          <div className="flex items-center gap-2">
            <Link2 aria-hidden className="h-4 w-4 text-mutedTeal" />
            <h2 id="k-public" className="text-[15px] font-bold text-slate">
              Public end-user link
            </h2>
          </div>
          <p className="text-[13.5px] text-ink">Share it with end users: they get route instructions only, never your data.</p>
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 break-all rounded-lg bg-white/70 px-2.5 py-1.5 font-mono text-[12.5px] text-slate">{publicLink}</code>
            <CopyButton value={publicLink} />
          </div>
        </section>
      )}

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
