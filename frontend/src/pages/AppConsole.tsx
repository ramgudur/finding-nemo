// /apps/:appId: the live console for an active app. The map is the hero; one tab set on the right.
// Without the authority key this is the PUBLIC view (public layers and hotspots only) with an inline
// unlock field, never a full-page gate. Rule 8: the spec, weights, verification, trace and changes are
// requested only with a key, and a rejected key drops back to public data at once. A proposed app
// redirects to its review.
import { Activity, Bot, FlaskConical, History, KeyRound, Layers, Lock, Radio, Scale, type LucideIcon } from "lucide-react";
import { motion } from "motion/react";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type RefObject } from "react";
import { Link, Navigate, useParams, useSearchParams } from "react-router-dom";
import { forgetKey, listProjects, loadKey, recordProject, saveKey } from "../api/authorityKey";
import { ApiError, archApi } from "../api/client";
import type { AppSpec } from "../api/types";
import { AgentTrace } from "../components/AgentTrace/AgentTrace";
import { AppShell, Breadcrumb, Centered, StatusChip, ghostBtn } from "../components/app/AppShell";
import { PublicLinkButtons, publicGoLink } from "../components/app/PublicLink";
import { SourcesPanel } from "../components/app/SourcesPanel";
import { TestControls, centreOf, inArea, type TestPoint } from "../components/app/TestControls";
import { useSourceStatus } from "../hooks/useSourceStatus";
import { ChangesList } from "../components/architecture/ChangesList";
import DebugDrawer from "../components/architecture/DebugDrawer";
import HeatmapPanel, { shownLayer, type ColourMode } from "../components/architecture/HeatmapPanel";
import { LayerList } from "../components/architecture/LayerList";
import { specLayers } from "../components/architecture/specLayers";
import { VerificationScores } from "../components/VerificationScores/VerificationScores";
import { WeightsPanel } from "../components/WeightsPanel/WeightsPanel";
import { useAuthorityConsoleData } from "../hooks/useAuthorityConsoleData";
import { useLiveAppData } from "../hooks/useLiveAppData";
import { useMapLearning } from "../hooks/useMapLearning";
import { useVectorReadings } from "../hooks/useVectorReadings";
import { useBuilderStore } from "../store/useBuilderStore";
import { isProposed } from "../components/app/appState";
import { LabelProposals } from "../components/app/LabelProposals";
import { ScoutRequestForm } from "../components/app/ScoutRequestForm";

type Status = "loading" | "live" | "proposed" | "notFound" | "error";
type Tab = "layers" | "sources" | "weights" | "verification" | "agents" | "trace" | "changes";
const TABS: { id: Tab; label: string; Icon: LucideIcon; authority: boolean; desc: string }[] = [
  { id: "layers", label: "Layers", Icon: Layers, authority: false, desc: "Each input's layer and how much it counts. Pick one to draw it on the map." },
  { id: "sources", label: "Sources", Icon: Radio, authority: true, desc: "Whether each input is receiving data, and when it was last polled." },
  { id: "weights", label: "Weights", Icon: Scale, authority: true, desc: "The live layer weights. Pin one so the agents can't move it." },
  { id: "verification", label: "Verification", Icon: FlaskConical, authority: true, desc: "How well the map anticipates new events, scored per method." },
  { id: "agents", label: "Agents", Icon: Bot, authority: true, desc: "New place labels the POI Labeler suggests, and requests to the Scout." },
  { id: "trace", label: "Trace", Icon: Activity, authority: true, desc: "Every agent step: what it saw, what it tested and what it did." },
  { id: "changes", label: "Changes", Icon: History, authority: true, desc: "Every change to weights, trust and labels, with before and after. Revert any." },
];

export default function AppConsole() {
  const { appId = "" } = useParams<{ appId: string }>();
  const [params] = useSearchParams();
  const selectedLayerId = useBuilderStore((s) => s.selectedLayerId);
  const setSelectedLayerId = useBuilderStore((s) => s.setSelectedLayerId);

  const [key, setKey] = useState<string | null>(() => loadKey(appId).key || null);
  const [remembered, setRemembered] = useState(() => loadKey(appId).remembered);
  const [rejected, setRejected] = useState(false);
  const [spec, setSpec] = useState<AppSpec | null>(null);
  const [status, setStatus] = useState<Status>("loading");
  const [error, setError] = useState("");
  const [tab, setTab] = useState<Tab>("layers");
  const keyInput = useRef<HTMLInputElement>(null);

  // a different app starts from its own stored key
  useEffect(() => {
    const k = loadKey(appId);
    setKey(k.key || null);
    setRemembered(k.remembered);
    setRejected(false);
    setSpec(null);
    setStatus("loading");
  }, [appId]);

  /** The key was refused: forget it and fall back to the public view at once (rule 8). */
  const reject = useCallback(() => {
    forgetKey(appId);
    setKey(null);
    setSpec(null);
    setRejected(true);
  }, [appId]);

  // the spec: keyless answers 200 while proposed (redirect to review), 401 once live
  useEffect(() => {
    let alive = true;
    archApi.getAppMaybeKey(appId, key ?? "").then(
      (s) => {
        if (!alive) return;
        if (s.status !== "active") return setStatus("proposed");
        setSpec(s);
        setStatus("live");
        const rec = listProjects().find((p) => p.appId === appId);
        if (!rec || rec.status !== "active") recordProject({ appId, intent: s.intent, status: "active", updatedAt: new Date().toISOString() });
      },
      (e: unknown) => {
        if (!alive) return;
        if (e instanceof ApiError && e.status === 401) {
          if (key) reject();
          // keyless 401: live (public view), unless an older server is keying a proposed spec
          else void isProposed(appId).then((proposed) => alive && setStatus(proposed ? "proposed" : "live"));
        } else if (e instanceof ApiError && e.status === 404) setStatus("notFound");
        else {
          setError(e instanceof Error ? e.message : String(e));
          setStatus("error");
        }
      },
    );
    return () => {
      alive = false;
    };
  }, [appId, key, reject]);

  const isLive = status === "live";
  const authority = isLive && !!key && !!spec;
  const live = useLiveAppData(isLive ? appId : "", isLive ? key : null, { onAuthError: reject });
  const data = useAuthorityConsoleData(appId, authority ? key : null);
  useEffect(() => {
    if (data.rejected) reject();
  }, [data.rejected, reject]);
  const sources = useSourceStatus(appId, authority ? key : null, reject);
  const vectors = useVectorReadings(appId, authority ? key : null, authority ? spec : null, reject);
  // The map shows what the agents did (authority only): colour by source by default, score otherwise.
  const [colourMode, setColourMode] = useState<ColourMode>("source");
  const learning = useMapLearning({
    appId,
    authorityKey: authority ? key : null,
    spec: authority ? spec : null,
    map: live.map,
    pois: live.pois,
    weights: data.weights,
    changes: data.changes,
    changesLoaded: data.loaded,
    bySource: colourMode === "source",
    onRejected: reject,
  });

  // Test your app: where simulated events land (the area's centre until the dev picks a point)
  const [testPoint, setTestPoint] = useState<TestPoint | null>(null);
  const [picking, setPicking] = useState(false);
  useEffect(() => {
    setTestPoint(null);
    setPicking(false);
  }, [appId]);
  const point = useMemo<TestPoint | null>(() => testPoint ?? (spec ? { ...centreOf(spec), radius_m: 200 } : null), [testPoint, spec]);
  const openTab = (t: Tab) => {
    setTab(t);
    requestAnimationFrame(() => document.getElementById(`tab-${t}`)?.focus());
  };

  // ?layer=<id> preselects a layer (shareable deep link); read once per app
  useEffect(() => {
    const want = params.get("layer");
    if (want) setSelectedLayerId(want);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appId]);

  // a selection left over from another app is cleared
  useEffect(() => {
    if (spec && selectedLayerId && !specLayers(spec).some((l) => l.layerId === selectedLayerId)) setSelectedLayerId(null);
  }, [spec, selectedLayerId, setSelectedLayerId]);

  if (!appId) return <Navigate to="/projects" replace />;
  if (status === "proposed") return <Navigate to={`/apps/${encodeURIComponent(appId)}/review`} replace />;
  if (status === "notFound" || status === "error")
    return (
      <Centered>
        <p className="max-w-md text-slate">{status === "notFound" ? `No app called “${appId}” exists on this server.` : `Couldn't load ${appId}: ${error}`}</p>
        <Link to="/projects" className="mt-3 text-sm font-semibold text-mutedTeal underline underline-offset-2">
          Back to Projects
        </Link>
      </Centered>
    );
  if (status === "loading")
    return (
      <Centered>
        <p className="font-mono text-sm text-ink">Loading {appId}…</p>
      </Centered>
    );

  const conn = live.streamState === "open" ? "Live stream" : live.streamState === "connecting" ? "Connecting" : "Polling every 2 s";
  const updated = live.lastUpdate ? new Date(live.lastUpdate).toLocaleTimeString() : null;
  const publicLink = publicGoLink(appId);
  const tabInfo = TABS.find((t) => t.id === tab)!;
  const visibleIds = live.map ? Object.keys(live.map.layers) : [];

  const onTabKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const i = TABS.findIndex((t) => t.id === tab);
    const next = TABS[(i + (e.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length]!;
    setTab(next.id);
    document.getElementById(`tab-${next.id}`)?.focus();
  };

  return (
    <AppShell wide>
      <header className="space-y-3">
        <Breadcrumb appId={appId} />
        <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
          <div className="min-w-0 space-y-1.5">
            <div className="flex flex-wrap items-center gap-2.5">
              <h1 className="truncate font-mono text-[28px] font-semibold tracking-[-0.02em] text-slate max-sm:text-[22px]">{appId}</h1>
              <StatusChip live />
              <span
                className={`inline-flex h-7 items-center gap-1.5 rounded-full px-3 text-[13px] font-bold ${authority ? "bg-deep text-cream" : "bg-sheet text-slate shadow-[inset_0_0_0_1px_rgb(var(--c-slate)/0.24)]"}`}
              >
                {authority ? <KeyRound aria-hidden className="h-3.5 w-3.5" /> : null}
                {authority ? "Authority view" : "Public view"}
              </span>
            </div>
            {spec?.intent && <p className="max-w-[72ch] text-[15px] text-ink">{spec.intent}</p>}
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2 md:justify-end">
            <span className="inline-flex h-8 items-center gap-2.5 rounded-full bg-sheet px-3 font-mono text-[12px] text-slate shadow-[var(--shadow-1)]" title={live.lastUpdate ?? undefined}>
              <span aria-hidden className={`wp-dot ${live.connected ? "is-live" : "is-off"}`} />
              {conn}
              {updated && <span className="text-ink">· {updated}</span>}
            </span>
            <PublicLinkButtons link={publicLink} />
          </div>
        </div>

        {authority ? (
          <div className="flex flex-wrap items-center gap-2 text-[13px] text-ink">
            <span>Authority key {remembered ? "remembered on this device" : "kept for this tab only"}.</span>
            <button
              type="button"
              onClick={() => {
                forgetKey(appId);
                setKey(null);
                setSpec(null);
                setRemembered(false);
                setRejected(false);
              }}
              className="hit relative rounded font-semibold text-slate underline underline-offset-2 hover:text-mutedTeal"
            >
              Forget key
            </button>
          </div>
        ) : (
          <UnlockForm
            inputRef={keyInput}
            rejected={rejected}
            onKey={(k, remember) => {
              saveKey(appId, k, remember);
              setRemembered(remember);
              setRejected(false);
              setKey(k);
            }}
          />
        )}
      </header>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(400px,480px)]">
        <div className="flex min-w-0 flex-col gap-5">
          <HeatmapPanel
            spec={spec}
            live={live}
            selectedLayerId={selectedLayerId}
            onPick={
              authority && picking && spec
                ? (lat, lon) => {
                    if (!inArea(spec, lat, lon)) return;
                    setTestPoint({ lat, lon, radius_m: point?.radius_m ?? 200 });
                    setPicking(false);
                  }
                : null
            }
            marker={authority && (picking || testPoint) ? point : null}
            vectors={authority ? vectors : []}
            learning={authority ? learning : null}
            weights={authority ? data.weights : null}
            colourMode={colourMode}
            onColourMode={setColourMode}
            emptyHint={
              authority ? (
                <>
                  Cells appear when events land inside the area. Send one from the{" "}
                  <Link to={`/apps/${encodeURIComponent(appId)}/keys`} className="pointer-events-auto font-semibold text-primary underline underline-offset-2">
                    keys page
                  </Link>
                  , or use Test your app below.
                </>
              ) : (
                "Nothing is happening in this area right now. Cells appear as events arrive."
              )
            }
          />
          {authority && spec && key && point && (
            <TestControls
              appId={appId}
              authorityKey={key}
              spec={spec}
              point={point}
              onPointChange={setTestPoint}
              picking={picking}
              onPickingChange={setPicking}
              onRejected={reject}
              onOpenTab={openTab}
            />
          )}
        </div>

        <section aria-label="Console" className="wp-sheet flex min-w-0 flex-col gap-4 self-start overflow-hidden pb-5">
          <div role="tablist" aria-label="Console" onKeyDown={onTabKey} className="flex overflow-x-auto border-b border-slate/[0.13] px-2.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
            {TABS.map(({ id, label, Icon, authority: needsKey }) => {
              const on = tab === id;
              const lockedTab = needsKey && !authority;
              return (
                <button
                  key={id}
                  id={`tab-${id}`}
                  role="tab"
                  type="button"
                  aria-selected={on}
                  aria-controls="console-panel"
                  tabIndex={on ? 0 : -1}
                  onClick={(e) => {
                    setTab(id);
                    // on phones the tab strip scrolls sideways: bring the chosen tab fully into view
                    e.currentTarget.scrollIntoView({ block: "nearest", inline: "nearest" });
                  }}
                  className={`tap relative flex h-12 shrink-0 items-center justify-center gap-1.5 whitespace-nowrap px-2 text-[12.5px] font-bold transition-colors duration-150 ease-wp ${
                    on ? "text-slate" : "text-ink hover:text-slate"
                  }`}
                >
                  {on && (
                    <motion.span
                      layoutId="console-tab"
                      aria-hidden
                      className="absolute inset-x-2 -bottom-px h-[2.5px] rounded-full bg-deep"
                      transition={{ duration: 0.2, ease: [0.23, 1, 0.32, 1] }}
                    />
                  )}
                  {lockedTab ? <Lock aria-hidden className="relative h-3 w-3" /> : <Icon aria-hidden className="relative h-3.5 w-3.5 lg:max-[1700px]:hidden" />}
                  <span className="relative">{label}</span>
                  {id === "changes" && authority && data.changes.length > 0 && (
                    <span className="relative rounded-[5px] bg-slate/[0.08] px-1 py-0.5 font-mono text-[11px] leading-none text-ink">{data.changes.length}</span>
                  )}
                </button>
              );
            })}
          </div>

          <motion.div
            key={tab + String(authority)}
            id="console-panel"
            role="tabpanel"
            aria-labelledby={`tab-${tab}`}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.15 }}
            className="min-w-0 space-y-3 px-4 sm:px-5"
          >
            <div>
              <h2 className="text-[18px] font-bold text-slate">{tabInfo.label}</h2>
              <p className="mt-1 text-[13.5px] text-ink">{tabInfo.desc}</p>
            </div>
            {tab === "layers" ? (
              <LayerList
                spec={authority ? spec : null}
                visibleIds={visibleIds}
                weights={data.weights}
                trace={data.trace}
                changes={data.changes}
                selectedLayerId={selectedLayerId}
                shownId={shownLayer(live.map, selectedLayerId)}
                onSelectLayer={setSelectedLayerId}
                sources={authority ? sources.rows : null}
                vectors={authority ? vectors : []}
              />
            ) : !authority ? (
              <div className="space-y-2 rounded-2xl bg-white/40 p-4 text-[13.5px] text-slate">
                <p className="flex items-center gap-1.5 font-semibold">
                  <Lock aria-hidden className="h-3.5 w-3.5" /> Authority view only
                </p>
                <p className="text-ink">Source status, weights, verification, agent requests, the trace and the change log need this app's authority key.</p>
                <button type="button" onClick={() => keyInput.current?.focus()} className={`${ghostBtn} -ml-3`}>
                  <KeyRound aria-hidden className="h-3.5 w-3.5" /> Enter the key
                </button>
              </div>
            ) : tab === "sources" && spec ? (
              <SourcesPanel appId={appId} inputs={spec.inputs ?? []} status={sources} />
            ) : !data.loaded ? (
              <div aria-busy className="space-y-2">
                {[0, 1, 2].map((i) => (
                  <div key={i} className="h-10 rounded-xl bg-white/50 motion-safe:animate-pulse" />
                ))}
              </div>
            ) : tab === "weights" ? (
              data.weights ? (
                <>
                  <WeightsPanel view={data.weights} busy={data.pinBusy} onPin={(l, p) => void data.pin(l, p)} />
                  {data.pinError && <p className="mt-1 text-[12.5px] font-semibold text-danger">{data.pinError}</p>}
                </>
              ) : (
                <p className="text-[13px] text-ink">Weights appear after the first engine tick.</p>
              )
            ) : tab === "verification" ? (
              data.verification ? (
                <VerificationScores view={data.verification} />
              ) : (
                <p className="text-[13px] text-ink">Verification scores appear once inputs have events.</p>
              )
            ) : tab === "agents" && spec && key ? (
              <div className="space-y-5">
                <LabelProposals
                  appId={appId}
                  authorityKey={key}
                  labelSet={spec.outputs?.poi_labels?.label_set ?? []}
                  onRejected={reject}
                />
                <div aria-hidden className="h-px bg-paleWater/80" />
                <ScoutRequestForm
                  appId={appId}
                  authorityKey={key}
                  inputs={spec.inputs ?? []}
                  onRejected={reject}
                  onOpenTrace={() => openTab("trace")}
                />
              </div>
            ) : tab === "trace" ? (
              <AgentTrace entries={data.trace} />
            ) : (
              <ChangesList changes={data.changes} onRevert={data.revert} />
            )}
          </motion.div>
        </section>
      </div>

      <DebugDrawer appId={appId} authorityKey={authority ? key : null} active />
    </AppShell>
  );
}

function UnlockForm({
  inputRef,
  rejected,
  onKey,
}: {
  inputRef: RefObject<HTMLInputElement>;
  rejected: boolean;
  onKey: (key: string, remember: boolean) => void;
}) {
  const [value, setValue] = useState("");
  const [remember, setRemember] = useState(false);
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        const k = value.trim();
        if (!k) return;
        setValue("");
        onKey(k, remember);
      }}
      className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-2xl bg-white/45 px-3 py-2.5"
    >
      <label htmlFor="unlock-key" className="flex items-center gap-1.5 text-[13.5px] font-semibold text-slate">
        <KeyRound aria-hidden className="h-4 w-4 text-mutedTeal" /> Unlock authority view
      </label>
      <input
        id="unlock-key"
        ref={inputRef}
        type="password"
        autoComplete="off"
        placeholder="authority key, shown once at activation"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        aria-describedby={rejected ? "unlock-rejected" : undefined}
        className="phone-16 min-w-0 flex-1 basis-[220px] rounded-lg border border-paleWater bg-white/80 px-2.5 py-1.5 font-mono text-[13px] max-sm:min-h-11 text-slate placeholder:text-ink/70"
      />
      <button
        type="submit"
        disabled={!value.trim()}
        className="tap wp-press rounded-full bg-deep px-4 py-1.5 text-[13px] font-bold text-cream hover:bg-primaryHover disabled:opacity-45"
      >
        Unlock
      </button>
      <label className="flex items-center gap-1.5 text-[13px] text-ink max-sm:min-h-11">
        <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} className="accent-mutedTeal" />
        Remember on this device
      </label>
      {rejected && (
        <p id="unlock-rejected" role="alert" className="w-full text-[13px] font-semibold text-danger">
          Key rejected, showing the public view.
        </p>
      )}
    </form>
  );
}
