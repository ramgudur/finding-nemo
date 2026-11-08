// /apps/:appId/keys: the success moment after activation. The first step sits at the top: the ingest and
// authority keys, shown once (from this tab's ActivateResponse; the platform can't show them again), each
// with one line on what it's for. Then a numbered setup guide, one step open at a time: per push input the
// endpoint, body schema and one tabbed example block (responses and webhook detail behind "More detail"),
// a live test send, what each pull input polls, the public link, and what happens next. Never echoes or
// logs a key outside its own field.
import { ArrowRight, KeyRound, Radio, TriangleAlert } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { loadKey, saveKey } from "../api/authorityKey";
import { api } from "../api/client";
import type { AppSpec, InputSpec } from "../api/types";
import { AppShell, Breadcrumb, CopyButton, StatusChip, primaryBtn } from "../components/app/AppShell";
import { PublicLinkRow, isLocalBase, publicGoLink } from "../components/app/PublicLink";
import { appDisplayName } from "../components/app/appName";
import { PushInputGuide } from "../components/keys/PushInputGuide";
import { TryIt } from "../components/keys/TryIt";
import { LANGS, type Lang } from "../components/keys/snippets";
import { GuideStep, RefTable } from "../components/keys/ui";
import { useBuilderStore } from "../store/useBuilderStore";

const INGEST_PLACEHOLDER = "<your ingest key>";

/** Where the examples send events: VITE_API_BASE when the frontend is built for a separate API host,
 * else this page's origin (the dev server, or a reverse proxy, forwards /v1 to the backend). */
const API_BASE_ENV = ((import.meta.env.VITE_API_BASE as string | undefined) ?? "").trim().replace(/\/+$/, "");

const LANG_KEY = "keys-guide:lang";

/** The example language is a per-viewer convenience: remembered when storage works, else guessed. */
function initialLang(): Lang {
  try {
    const v = localStorage.getItem(LANG_KEY);
    if (v && LANGS.some((l) => l.id === v)) return v as Lang;
  } catch {
    // storage unavailable: fall through to the guess
  }
  return /Windows/i.test(navigator.userAgent) ? "powershell" : "bash";
}

function KeyField({ label, hint, value }: { label: string; hint: string; value: string }) {
  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-baseline gap-x-3">
        <span className="text-[15px] font-bold text-slate">{label}</span>
        <span className="text-[13.5px] text-ink">{hint}</span>
      </div>
      <div className="flex items-stretch gap-2">
        <input
          readOnly
          value={value}
          aria-label={label}
          onFocus={(e) => e.currentTarget.select()}
          className="min-w-0 flex-1 rounded-[10px] border border-slate/20 bg-sheet px-3 py-2.5 font-mono text-[13px] text-slate"
        />
        <CopyButton value={value} />
      </div>
    </div>
  );
}

const every = (s: number) => (s < 120 ? `${s} s` : s % 3600 === 0 ? `${s / 3600} h` : `${Math.round(s / 60)} min`);

function connectorWhat(i: InputSpec) {
  const c = i.connector;
  if (!c) return "No source is set, so nothing is fetched yet.";
  const kind = c.type === "rss" ? "RSS feed" : c.type === "json_list" ? "JSON list" : c.preset ? `JSON API (${c.preset} preset)` : "JSON API";
  return (
    <>
      {kind}
      {c.url && (
        <>
          {" "}
          <span className="break-all font-mono text-[13px] text-slate">{c.url}</span>
        </>
      )}
    </>
  );
}

function Bullet({ children }: { children: ReactNode }) {
  return (
    <li className="flex gap-2">
      <span aria-hidden className="text-deep">
        •
      </span>
      <span>{children}</span>
    </li>
  );
}

export default function AppKeys() {
  const { appId = "" } = useParams<{ appId: string }>();
  const navigate = useNavigate();
  const activation = useBuilderStore((s) => (s.activation?.app_id === appId ? s.activation : null));
  const compiledSpec = useBuilderStore((s) => (s.compiledSpec?.app_id === appId ? s.compiledSpec : null));
  const [fetchedSpec, setSpec] = useState<AppSpec | null>(null);
  // this tab's compiled spec wins (it can arrive after mount); else the one fetched with the key
  const spec = compiledSpec ?? fetchedSpec;
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

  const [lang, setLangState] = useState<Lang>(initialLang);
  const setLang = (l: Lang) => {
    setLangState(l);
    try {
      localStorage.setItem(LANG_KEY, l);
    } catch {
      // not remembered; fine
    }
  };

  const apiBase = API_BASE_ENV || window.location.origin;
  const consoleHref = `/apps/${encodeURIComponent(appId)}`;
  const push = (spec?.inputs ?? []).filter((i) => i.kind === "push");
  const pull = (spec?.inputs ?? []).filter((i) => i.kind === "pull");
  // VITE_PUBLIC_BASE (a public host or tunnel) when set, else this page's origin
  const publicLink = publicGoLink(appId);
  const localPublic = isLocalBase();
  const ingestKey = activation?.ingest_key ?? INGEST_PLACEHOLDER;

  const openConsole = () => {
    // the keys were shown; this tab no longer holds the ingest key (the authority key stays as chosen)
    useBuilderStore.getState().setActivation(null);
    navigate(consoleHref);
  };

  // The setup guide: numbered steps, one open at a time (the first by default). A step with nothing to say
  // is left out, and the numbers follow what is shown.
  const steps: Array<{ id: string; title: string; summary: ReactNode; body: ReactNode }> = [];
  if (spec && push.length > 0) {
    steps.push({
      id: "k-connect",
      title: "Connect your app",
      summary: "Each push input has its own endpoint. POST one JSON event per request, from your server.",
      body: (
        <>
          <p className="font-mono text-[13px] text-ink">
            API base <span className="break-all text-slate">{apiBase}</span>
            {!API_BASE_ENV && import.meta.env.DEV && " · this dev server proxies /v1 to the backend"}
          </p>
          <div className="space-y-2">
            {push.map((i, idx) => (
              <PushInputGuide key={i.id} spec={spec} input={i} apiBase={apiBase} ingestKey={ingestKey} lang={lang} setLang={setLang} defaultOpen={idx === 0} />
            ))}
          </div>
        </>
      ),
    });
    steps.push({
      id: "k-try",
      title: "Try it now",
      summary: "Send one real event at the centre of your area with the key this page still holds.",
      body: <TryIt spec={spec} inputs={push} ingestKey={activation?.ingest_key ?? null} onOpenConsole={openConsole} />,
    });
  }
  if (spec && pull.length > 0) {
    steps.push({
      id: "k-pull",
      title: "Data we fetch",
      summary: "The platform polls these sources on its own. Nothing is needed from you.",
      body: (
        <>
          <RefTable
            label="Pull inputs"
            head={["Input", "What is polled", "How often"]}
            rows={pull.map((i) => [i.id, connectorWhat(i), i.connector ? `every ${every(i.connector.poll_s ?? 300)}` : "—"])}
          />
          <p className="flex items-center gap-1.5 text-[13.5px] text-ink">
            <Radio aria-hidden className="h-4 w-4 text-deep" />
            Whether each one is receiving data shows in the console's Sources tab.
          </p>
        </>
      ),
    });
  }
  steps.push({
    id: "k-public",
    title: "Share with the public",
    summary: "End users get route directions only: never your data, your inputs or anyone else's position.",
    body: (
      <>
        <PublicLinkRow link={publicLink} />
        {localPublic && (
          <p className="max-w-[68ch] text-[13px] text-ink">
            Tip: a localhost link opens only on this computer. To share it, serve the app from a public host or a tunnel and set{" "}
            <span className="font-mono text-[12px]">VITE_PUBLIC_BASE</span> to that address.
          </p>
        )}
      </>
    ),
  });
  steps.push({
    id: "k-next",
    title: "What happens next",
    summary: "The map updates about every 2 s as events arrive.",
    body: (
      <ul className="space-y-2 text-[15px] text-slate">
        <Bullet>The agents start tuning weights and naming places once data flows.</Bullet>
        <Bullet>Watch it all in the console, unlocked with your authority key.</Bullet>
      </ul>
    ),
  });
  const [openStep, setOpenStep] = useState<string | null>(null);
  const shownStep = openStep ?? steps[0]?.id ?? null;

  return (
    <AppShell>
      <header className="space-y-3">
        <Breadcrumb appId={appId} />
        <div>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="font-display text-[32px] font-semibold leading-tight tracking-[-0.015em] text-deep max-sm:text-[27px]">
              {appDisplayName(appId) || appId} is live
            </h1>
            <StatusChip live />
          </div>
          <p className="mt-0.5 font-mono text-[13px] text-ink">{appId}</p>
        </div>
        <p role="status" aria-live="polite" className="max-w-[68ch] text-[16px] text-ink">
          {activation
            ? "Your tiers, locked weights and verification order are locked. Save your keys, connect your app, then send a test event."
            : spec
              ? "Share the public link, and connect your app with the setup guide below."
              : "Share the public link below. Open the console to watch the map and the agents."}
        </p>
      </header>

      {activation && (
        <section aria-labelledby="k-keys" className="wp-sheet space-y-5 p-5 sm:p-6">
          <h2 id="k-keys" className="text-[18px] font-bold text-slate">
            First, save your keys
          </h2>
          <p role="note" className="flex items-start gap-2.5 rounded-xl bg-attnBg px-4 py-3 text-[15px] font-semibold leading-snug text-attn">
            <TriangleAlert aria-hidden className="mt-0.5 h-5 w-5 shrink-0" />
            <span>
              Shown once. Copy both keys somewhere safe now: the platform can't show them again, and there is no reset.
            </span>
          </p>
          <KeyField label="Ingest key" hint="Your app and services send data with it. Keep it server-side." value={activation.ingest_key} />
          <KeyField label="Authority key" hint="Opens your console. Never put it in a public app." value={activation.authority_key} />
          <label className="flex flex-wrap items-center gap-2 text-[15px] text-slate">
            <input
              type="checkbox"
              className="h-4 w-4 accent-deep"
              checked={remember}
              onChange={(e) => {
                setRemember(e.target.checked);
                saveKey(appId, activation.authority_key, e.target.checked);
              }}
            />
            Remember the authority key on this device
            <span className="text-[13.5px] text-ink">{remember ? "(kept in this browser until you forget it)" : "(this tab only)"}</span>
          </label>
        </section>
      )}

      <section aria-label="Setup guide" className="wp-sheet px-3 pb-1 pt-4 sm:px-5">
        <ol>
          {steps.map((st, i) => (
            <GuideStep
              key={st.id}
              n={i + 1}
              id={st.id}
              title={st.title}
              summary={shownStep === st.id ? undefined : st.summary}
              open={shownStep === st.id}
              onToggle={() => setOpenStep(shownStep === st.id ? "" : st.id)}
              last={i === steps.length - 1}
            >
              {st.body}
            </GuideStep>
          ))}
        </ol>
      </section>

      {activation ? (
        <div className="flex flex-wrap items-center justify-end gap-3 pt-2">
          <p className="text-[13.5px] text-ink">Leaving this page hides the keys for good.</p>
          <button type="button" onClick={openConsole} className={primaryBtn}>
            <KeyRound aria-hidden className="h-4 w-4" /> I've saved my keys, open the console <ArrowRight aria-hidden className="h-4 w-4" />
          </button>
        </div>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 pt-2">
          <p className="max-w-[60ch] text-[13.5px] text-ink">
            Keys are shown only once, at activation. If you saved the authority key, paste it in the console to unlock the authority view
            {spec ? "; the guide above shows a placeholder where your ingest key goes." : ". The setup guide needs your app's inputs, which only that key can read."}
          </p>
          <Link to={consoleHref} className={primaryBtn}>
            Open the console <ArrowRight aria-hidden className="h-4 w-4" />
          </Link>
        </div>
      )}
    </AppShell>
  );
}
