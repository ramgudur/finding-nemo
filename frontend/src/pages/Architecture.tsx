// Architecture / layer inspector. Two entry points:
//  - /architecture         fresh: the spec the Sources page just compiled (store.compiledSpec).
//  - /architecture/:appId  revisit (Past Projects, bookmarks): the server spec with the remembered
//                          authority key; a pending app (which has no key yet) reopens from this
//                          browser's pending-spec snapshot. An active app without a key asks for one
//                          (rule 8: the full spec is authority-only).
import { KeyRound, Sprout } from "lucide-react";
import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  dropSpecSnapshot,
  listProjects,
  loadKey,
  loadSpecSnapshot,
  recordProject,
  saveKey,
  saveSpecSnapshot,
} from "../api/authorityKey";
import { ApiError, archApi } from "../api/client";
import type { ActivateResponse, AppSpec } from "../api/types";
import ArchitectureFooter from "../components/architecture/ArchitectureFooter";
import ArchitectureLayout from "../components/architecture/ArchitectureLayout";
import ConnectorDiagram from "../components/architecture/ConnectorDiagram";
import DebugDrawer from "../components/architecture/DebugDrawer";
import HeatmapPanel from "../components/architecture/HeatmapPanel";
import LayerStackPanel from "../components/architecture/LayerStackPanel";
import { useBuilderStore } from "../store/useBuilderStore";

type Load =
  | { state: "loading" }
  | { state: "ready"; spec: AppSpec; key: string | null }
  | { state: "needKey"; rejected: boolean; maybePending: boolean }
  | { state: "error"; message: string };

const isAuthError = (e: unknown) => e instanceof ApiError && (e.status === 401 || e.status === 403);

/** Is the app still pending? true / false, or null when the server can't tell (route not deployed). */
async function stillPending(appId: string): Promise<boolean | null> {
  try {
    await archApi.compileStatus(appId);
    return true;
  } catch (e) {
    // FastAPI's own "Not Found" means the route is missing; the route's 404 means "active or unknown"
    if (e instanceof ApiError && e.status === 404) return e.message === "Not Found" ? null : false;
    return null;
  }
}

async function resolveSpec(appId: string): Promise<Load> {
  const { compiledSpec, activation } = useBuilderStore.getState();
  const storeKey = activation?.app_id === appId ? activation.authority_key : "";
  const key = storeKey || loadKey(appId).key;

  // the draft this tab just compiled (or activated, with its key)
  if (compiledSpec?.app_id === appId && (compiledSpec.status !== "active" || key)) {
    return { state: "ready", spec: compiledSpec, key: key || null };
  }
  try {
    return { state: "ready", spec: await archApi.getAppMaybeKey(appId, key), key: key || null };
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return { state: "error", message: `No app called “${appId}” exists on this server.` };
    if (!isAuthError(e)) return { state: "error", message: e instanceof Error ? e.message : String(e) };
  }
  // 401: a wrong key, or no key. A pending app has no key at all, so reopen it from the local snapshot.
  const snap = loadSpecSnapshot(appId);
  if (!key && snap) {
    if ((await stillPending(appId)) !== false) return { state: "ready", spec: snap, key: null };
    dropSpecSnapshot(appId); // confirmed since: the server copy (behind the key) is the truth now
  }
  const recorded = listProjects().find((p) => p.appId === appId);
  return { state: "needKey", rejected: Boolean(key), maybePending: !recorded || recorded.status !== "active" };
}

export function Architecture() {
  const { appId: routeAppId } = useParams<{ appId: string }>();
  const compiledSpec = useBuilderStore((s) => s.compiledSpec);
  const fresh = routeAppId === undefined;
  const [load, setLoad] = useState<Load>({ state: "loading" });

  const run = useCallback((appId: string, signal: { alive: boolean }) => {
    setLoad({ state: "loading" });
    void resolveSpec(appId).then((l) => signal.alive && setLoad(l));
  }, []);

  useEffect(() => {
    const signal = { alive: true };
    if (fresh) {
      // read the store once per visit; later edits arrive through onSpecSaved / onActivated
      const spec = useBuilderStore.getState().compiledSpec;
      if (spec) {
        const act = useBuilderStore.getState().activation;
        const k = (act?.app_id === spec.app_id ? act.authority_key : "") || loadKey(spec.app_id).key;
        setLoad({ state: "ready", spec, key: k || null });
      }
    } else run(routeAppId, signal);
    return () => {
      signal.alive = false;
    };
  }, [fresh, routeAppId, run]);

  // a pending spec on screen stays reopenable from Past Projects
  useEffect(() => {
    if (load.state === "ready" && load.spec.status !== "active") saveSpecSnapshot(load.spec);
  }, [load]);

  if (fresh && !compiledSpec && load.state !== "ready") return <EmptyState />;
  if (load.state === "loading")
    return (
      <Centered>
        <p className="font-mono text-sm text-inkSoft">loading the app spec…</p>
      </Centered>
    );
  if (load.state === "error")
    return (
      <Centered>
        <p className="text-slate">{load.message}</p>
        <Link to="/projects" className="mt-3 text-sm text-mutedTeal underline underline-offset-2">
          Back to Past Projects
        </Link>
      </Centered>
    );
  if (load.state === "needKey")
    return (
      <KeyGate
        appId={routeAppId ?? ""}
        rejected={load.rejected}
        maybePending={load.maybePending}
        onSubmit={(k, remember) => {
          saveKey(routeAppId ?? "", k, remember);
          run(routeAppId ?? "", { alive: true });
        }}
      />
    );

  return <Ready spec={load.spec} authorityKey={load.key} onChange={(spec, key) => setLoad({ state: "ready", spec, key })} />;
}

// ---------- the console ----------

function Ready({
  spec,
  authorityKey,
  onChange,
}: {
  spec: AppSpec;
  authorityKey: string | null;
  onChange: (spec: AppSpec, key: string | null) => void;
}) {
  const selectedLayerId = useBuilderStore((s) => s.selectedLayerId);
  const setSelectedLayerId = useBuilderStore((s) => s.setSelectedLayerId);
  const [params] = useSearchParams();
  const active = spec.status === "active";
  const appId = spec.app_id;

  // ?layer=<id> preselects a layer (shareable deep link); read once per app
  useEffect(() => {
    const want = params.get("layer");
    if (want) setSelectedLayerId(want);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appId]);

  // a selection left over from another app (or a layer removed by an edit) is cleared
  useEffect(() => {
    if (!selectedLayerId) return;
    const ids = (spec.inputs ?? []).flatMap((i) => (i.operators ?? []).map((o) => o.layer_id ?? `${i.id}.${o.op}`));
    if (!ids.includes(selectedLayerId)) setSelectedLayerId(null);
  }, [spec, selectedLayerId, setSelectedLayerId]);

  const onSpecSaved = (next: AppSpec) => {
    const st = useBuilderStore.getState();
    if (st.compiledSpec?.app_id === next.app_id) st.setCompiledSpec(next);
    if (next.status !== "active") saveSpecSnapshot(next);
    onChange(next, authorityKey);
  };

  const onActivated = (resp: ActivateResponse) => {
    const next: AppSpec = { ...spec, status: "active" };
    const st = useBuilderStore.getState();
    if (st.compiledSpec?.app_id === resp.app_id) {
      st.setCompiledSpec(next);
      st.setActivation(resp);
      st.setAppStatus("active");
    }
    saveKey(resp.app_id, resp.authority_key, false); // this session only; "remember" is the dev's choice
    dropSpecSnapshot(resp.app_id);
    recordProject({ appId: resp.app_id, intent: spec.intent, status: "active", updatedAt: new Date().toISOString() });
    onChange(next, resp.authority_key);
  };

  const nInputs = spec.inputs?.length ?? 0;
  const nLayers = (spec.inputs ?? []).reduce((a, i) => a + (i.operators?.length ?? 0), 0);

  return (
    <ArchitectureLayout
      header={
        <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
          <div className="min-w-0">
            <p className="text-[11px] font-semibold uppercase tracking-[0.16em] text-inkSoft">Architecture</p>
            <div className="mt-0.5 flex flex-wrap items-center gap-2">
              <h1 className="truncate font-mono text-xl font-semibold text-slate">{appId}</h1>
              <span
                className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                  active ? "bg-seaGlass/40 text-slate" : "bg-cream text-ink ring-1 ring-softStone"
                }`}
              >
                {active ? "active" : "pending confirmation"}
              </span>
              <span
                className={`flex items-center gap-1 rounded-full px-2.5 py-0.5 font-mono text-[11px] ${
                  authorityKey ? "bg-slate/85 text-cream" : "bg-washedSky text-ink"
                }`}
                title={authorityKey ? "requests carry this app's authority key" : "public view"}
              >
                <KeyRound size={11} /> {authorityKey ? "authority view" : active ? "public view" : "no key yet"}
              </span>
            </div>
            <p className="mt-1 max-w-3xl text-sm text-ink">{spec.intent}</p>
          </div>
          <p className="shrink-0 font-mono text-[11px] text-inkSoft">
            {nInputs} input{nInputs === 1 ? "" : "s"} · {nLayers} layer{nLayers === 1 ? "" : "s"} · {spec.grid.cell_m} m cells
          </p>
        </div>
      }
      left={<HeatmapPanel spec={spec} appId={appId} authorityKey={authorityKey} active={active} selectedLayerId={selectedLayerId} />}
      right={
        <LayerStackPanel
          spec={spec}
          appId={appId}
          authorityKey={authorityKey}
          active={active}
          selectedLayerId={selectedLayerId}
          onSelectLayer={setSelectedLayerId}
          onSpecSaved={onSpecSaved}
          onActivated={onActivated}
        />
      }
      diagram={
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <h2 className="text-sm font-semibold text-slate">How data flows into the score</h2>
              <p className="text-xs text-inkSoft">Operators are the experts; weights decide how much each one counts, within locked rules.</p>
            </div>
            <p className="font-mono text-[12px] text-slate">
              score = clamp(Σ wᵢ · norm(layerᵢ), 0, 1) <span className="text-[#C97F69]">→ authority overrides applied after</span>
            </p>
          </div>
          <ConnectorDiagram spec={spec} selectedLayerId={selectedLayerId} onSelectLayer={setSelectedLayerId} />
        </div>
      }
      footer={<ArchitectureFooter spec={spec} active={active} />}
      overlay={<DebugDrawer appId={appId} authorityKey={authorityKey} active={active} />}
    />
  );
}

// ---------- small states ----------

function Centered({ children }: { children: ReactNode }) {
  return <main className="watercolor-grain flex min-h-screen flex-col items-center justify-center px-6 pt-20 text-center">{children}</main>;
}

function EmptyState() {
  return (
    <Centered>
      <div className="flex max-w-md flex-col items-center gap-3 rounded-3xl border border-dashed border-softAqua bg-washedSky/50 px-8 py-12">
        <Sprout size={28} className="text-mutedTeal" />
        <h1 className="text-lg font-semibold text-slate">Nothing compiled yet</h1>
        <p className="text-sm text-ink">Describe your app and its sources first; the proposed architecture shows up here for you to review.</p>
        <div className="mt-2 flex flex-wrap justify-center gap-2">
          <Link to="/sources" className="rounded-full bg-[#4F80B0] px-4 py-2 text-sm font-semibold text-cream shadow-sm transition hover:-translate-y-px">
            Start with sources
          </Link>
          <Link to="/projects" className="rounded-full bg-cream/90 px-4 py-2 text-sm text-slate transition hover:-translate-y-px">
            Past Projects
          </Link>
        </div>
      </div>
    </Centered>
  );
}

function KeyGate({
  appId,
  rejected,
  maybePending,
  onSubmit,
}: {
  appId: string;
  rejected: boolean;
  maybePending: boolean;
  onSubmit: (key: string, remember: boolean) => void;
}) {
  const [draft, setDraft] = useState("");
  const [remember, setRemember] = useState(false);
  return (
    <Centered>
      <form
        className="flex w-full max-w-sm flex-col gap-3 rounded-3xl border border-softAqua/70 bg-cream/70 p-6 text-left shadow-[0_6px_30px_rgba(53,89,107,0.08)]"
        onSubmit={(e) => {
          e.preventDefault();
          const k = draft.trim();
          if (k) onSubmit(k, remember);
        }}
      >
        <div className="flex items-center gap-2 text-slate">
          <KeyRound size={16} />
          <h1 className="text-base font-semibold">Authority key needed</h1>
        </div>
        <p className="text-sm text-ink">
          The full spec of <span className="font-mono text-slate">{appId}</span> is shown only with its authority key (shown once, at activation).
        </p>
        {rejected && <p className="text-sm text-coral">That key was not accepted.</p>}
        <input
          type="password"
          autoComplete="off"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="authority key"
          aria-label="authority key"
          className="rounded-xl border border-softAqua bg-mist px-3 py-2 font-mono text-sm text-slate outline-none focus:border-mutedTeal"
        />
        <label className="flex items-center gap-2 text-xs text-ink">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> remember on this device
        </label>
        <button type="submit" className="rounded-full bg-[#4F80B0] px-4 py-2 text-sm font-semibold text-cream shadow-sm transition hover:-translate-y-px">
          Open
        </button>
        {maybePending && (
          <p className="text-xs text-inkSoft">
            Still pending confirmation? A pending app has no key yet: reopen it in the browser that compiled it, or compile it again from Sources.
          </p>
        )}
        <Link to="/projects" className="text-xs text-mutedTeal underline underline-offset-2">
          Back to Past Projects
        </Link>
      </form>
    </Centered>
  );
}
