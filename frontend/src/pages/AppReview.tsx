// /apps/:appId/review: review the Compiler's proposal for a proposed (pending_confirmation) app, edit
// it (PUT /spec), then confirm it (POST /activate, the only dev confirmation; rule 4). An app that is
// already live redirects to its console. Spec source: this tab's compile, else this browser's
// snapshot, confirmed against the server (GET /apps/{app} answers keyless while the app is proposed).
import { Check, Lock, ShieldCheck, Undo2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { dropSpecSnapshot, listProjects, loadSpecSnapshot, recordProject, saveKey, saveSpecSnapshot } from "../api/authorityKey";
import { ApiError, api, archApi } from "../api/client";
import type { AppSpec } from "../api/types";
import { AppShell, Breadcrumb, Centered, StatusChip, ghostBtn, primaryBtn, secondaryBtn } from "../components/app/AppShell";
import ConnectorDiagram from "../components/architecture/ConnectorDiagram";
import ProposedSchematic from "../components/architecture/ProposedSchematic";
import {
  InputsSection,
  ReviewMessages,
  RoutingSection,
  VerificationSection,
  VisibilityLabelsSection,
  useSpecDraft,
} from "../components/architecture/ReviewSections";
import { specLayers } from "../components/architecture/specLayers";
import { TierBadge } from "../components/architecture/TierBadge";
import { VERIFICATION_PLAIN } from "../components/builder/explain";
import { PublicViewBox } from "../components/review/PublicView";
import { useBuilderStore } from "../store/useBuilderStore";
import { isProposed } from "../components/app/appState";

type Load = { state: "loading" } | { state: "ready"; spec: AppSpec } | { state: "live" } | { state: "error"; message: string };

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** What confirmation makes permanent (rule 4): tiers, locked weights and the verification order. */
function locks(spec: AppSpec) {
  const inputs = spec.inputs ?? [];
  const locked = specLayers(spec).filter((l) => l.input.tier === "locked");
  const verification = [...(spec.verification ?? [])].sort((a, b) => a.priority - b.priority);
  return { inputs, locked, verification };
}

export default function AppReview() {
  const { appId: routeId } = useParams<{ appId: string }>();
  const appId = routeId ?? useBuilderStore.getState().compiledSpec?.app_id ?? "";
  const [load, setLoad] = useState<Load>({ state: "loading" });

  useEffect(() => {
    let alive = true;
    const { compiledSpec } = useBuilderStore.getState();
    const local = compiledSpec?.app_id === appId ? compiledSpec : loadSpecSnapshot(appId);
    if (local?.status === "active") {
      setLoad({ state: "live" });
      return;
    }
    if (local) setLoad({ state: "ready", spec: local });
    archApi.getAppMaybeKey(appId, "").then(
      (server) => {
        if (!alive) return;
        if (server.status === "active") setLoad({ state: "live" });
        else setLoad({ state: "ready", spec: server });
      },
      (e: unknown) => {
        if (!alive) return;
        // keyless 401: live (its spec is behind the authority key now, rule 8), or an older server
        // that keys proposed specs too; the public compile status tells them apart
        if (e instanceof ApiError && e.status === 401) {
          void isProposed(appId).then((proposed) => {
            if (!alive) return;
            if (proposed === false) {
              dropSpecSnapshot(appId);
              setLoad({ state: "live" });
            } else if (!local) {
              setLoad({
                state: "error",
                message: `This server shares the proposal for “${appId}” only with the browser that compiled it. Open it there, or compile it again.`,
              });
            }
          });
        } else if (e instanceof ApiError && e.status === 404) {
          setLoad({
            state: "error",
            message: local
              ? `“${appId}” is no longer on this server. Compile it again to review a new proposal.`
              : `No app called “${appId}” exists on this server.`,
          });
        } else if (!local) setLoad({ state: "error", message: e instanceof Error ? e.message : String(e) });
      },
    );
    return () => {
      alive = false;
    };
  }, [appId]);

  if (!appId) return <Navigate to="/projects" replace />;
  if (load.state === "live") return <Navigate to={`/apps/${encodeURIComponent(appId)}`} replace />;
  if (load.state === "loading")
    return (
      <Centered>
        <p className="font-mono text-sm text-ink">Loading the proposal…</p>
      </Centered>
    );
  if (load.state === "error")
    return (
      <Centered>
        <p className="max-w-md text-slate">{load.message}</p>
        <Link to="/projects" className="mt-3 text-sm font-semibold text-mutedTeal underline underline-offset-2">
          Back to Projects
        </Link>
      </Centered>
    );
  return <Review spec={load.spec} onSaved={(spec) => setLoad({ state: "ready", spec })} />;
}

function Review({ spec, onSaved }: { spec: AppSpec; onSaved: (spec: AppSpec) => void }) {
  const navigate = useNavigate();
  const appId = spec.app_id;
  const selectedLayerId = useBuilderStore((s) => s.selectedLayerId);
  const setSelectedLayerId = useBuilderStore((s) => s.setSelectedLayerId);
  const dialog = useRef<HTMLDialogElement>(null);
  const [activating, setActivating] = useState(false);
  const [activateError, setActivateError] = useState<{ text: string; live?: boolean } | null>(null);

  const persist = (next: AppSpec) => {
    const st = useBuilderStore.getState();
    if (st.compiledSpec?.app_id === next.app_id) st.setCompiledSpec(next);
    saveSpecSnapshot(next);
    onSaved(next);
  };
  const d = useSpecDraft(spec, appId, persist);

  // a proposal on screen stays reopenable from Projects (records are automatic; there is no Save Project)
  useEffect(() => {
    saveSpecSnapshot(spec);
    if (!listProjects().some((p) => p.appId === appId)) {
      recordProject({ appId, intent: spec.intent, status: "proposed", updatedAt: new Date().toISOString() });
    }
  }, [spec, appId]);

  // a selection left over from another app is cleared
  useEffect(() => {
    if (selectedLayerId && !specLayers(spec).some((l) => l.layerId === selectedLayerId)) setSelectedLayerId(null);
  }, [spec, selectedLayerId, setSelectedLayerId]);

  const { inputs, locked, verification } = locks(d.draft);
  const summary = `${plural(inputs.length, "input tier")}, ${plural(locked.length, "locked weight")}, the verification order`;

  const activate = async () => {
    setActivating(true);
    setActivateError(null);
    try {
      const resp = await api.activate(appId);
      const st = useBuilderStore.getState();
      st.setActivation(resp);
      if (st.compiledSpec?.app_id === appId) {
        st.setCompiledSpec({ ...spec, status: "active" });
        st.setAppStatus("active");
      }
      saveKey(appId, resp.authority_key, false); // this tab only; "remember" is the dev's choice on the next page
      dropSpecSnapshot(appId);
      recordProject({ appId, intent: spec.intent, status: "active", updatedAt: new Date().toISOString() });
      dialog.current?.close();
      navigate(`/apps/${encodeURIComponent(appId)}/keys`);
    } catch (e) {
      dialog.current?.close();
      if (e instanceof ApiError && e.status === 409) {
        recordProject({ appId, intent: spec.intent, status: "active", updatedAt: new Date().toISOString() });
        dropSpecSnapshot(appId);
        setActivateError({ text: "This app is already live. Its keys were shown when it was activated.", live: true });
      } else setActivateError({ text: `Activation failed: ${e instanceof ApiError ? e.message : String(e)}. Nothing was locked; try again.` });
    } finally {
      setActivating(false);
    }
  };

  const nLayers = specLayers(d.draft).length;
  // The builder draft (sessionStorage) can reopen this proposal's inputs only when it is for this same app.
  const draftHere = useBuilderStore((s) => s.appId === appId && s.sources.length > 0);

  return (
    <AppShell wide>
      <div className="bld flex flex-col gap-6">
      <header className="space-y-2">
        <Breadcrumb appId={appId} />
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="bld-title">Review the proposal</h1>
          <StatusChip live={false} />
        </div>
        <p className="bld-lead max-w-[70ch]">
          Nemotron read your intent and sources and proposed the plan below. Check each setting, change anything you
          like, save, then confirm. Nothing runs until you confirm.
        </p>
        <p className="max-w-[70ch] text-[14px] italic text-ink">“{spec.intent}”</p>
        <p className="font-mono text-[12px] text-ink">
          {plural(inputs.length, "input")} · {plural(nLayers, "layer")} · {spec.grid.cell_m} m cells
        </p>
        {draftHere && (
          <p className="text-[13px] text-ink">
            <Link
              to="/new/sources"
              className="rounded-md font-semibold text-primary underline underline-offset-[3px] hover:text-primaryHover"
            >
              Edit inputs in the builder
            </Link>{" "}
            (changing inputs means compiling again).
          </p>
        )}
      </header>

      <PublicViewBox spec={spec} dirty={d.dirty} onSaved={persist} />

      <section aria-labelledby="rv-how" className="bld-card p-4 sm:p-5">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <h2 id="rv-how" className="bld-h2">
              How your map's score is built
            </h2>
            <p className="bld-hint max-w-[70ch] text-[13px]">
              Each source is read in one or more ways, and each way makes a layer. The layers are added up by weight
              into a score from 0 to 1; confirmed zones always win on top. Select a layer to follow it.
            </p>
          </div>
          <p className="font-mono text-[12px] text-slate">
            score = clamp(Σ wᵢ · norm(layerᵢ), 0, 1) <span className="text-ink">→ then authority overrides</span>
          </p>
        </div>
        <ConnectorDiagram spec={d.draft} selectedLayerId={selectedLayerId} onSelectLayer={setSelectedLayerId} />
      </section>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="bld-card min-w-0 space-y-6 p-4 sm:p-6">
          <ReviewMessages d={d} />
          <InputsSection d={d} />
          <VerificationSection d={d} />
          <VisibilityLabelsSection d={d} />
          <RoutingSection d={d} />
        </div>
        <aside aria-labelledby="rv-grid" className="min-w-0 lg:sticky lg:top-24 lg:self-start">
          <h2 id="rv-grid" className="bld-h3 mb-1">
            Grid preview
          </h2>
          <p className="bld-hint mb-2">The area split into cells. It fills with real values once the app is live.</p>
          <ProposedSchematic spec={d.draft} selectedLayerId={selectedLayerId} />
        </aside>
      </div>

      <div className="sticky bottom-[max(0.75rem,env(safe-area-inset-bottom))] z-20 flex flex-col gap-2 rounded-2xl bg-sheet p-3 sm:bottom-4 sm:gap-3 shadow-[var(--shadow-2)] backdrop-blur-sm sm:flex-row sm:items-center sm:px-4">
        <p className="flex min-w-0 flex-1 items-start gap-2 text-[12.5px] text-slate sm:text-[13px]">
          <Lock aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 text-mutedTeal" />
          <span aria-live="polite">
            {d.dirty ? (
              <span className="font-semibold">Unsaved edits. Save or discard them before you confirm.</span>
            ) : d.justSaved ? (
              <span className="inline-flex items-center gap-1.5 font-semibold">
                <Check aria-hidden className="h-3.5 w-3.5 text-mutedTeal" /> Saved. Your edits are in the proposed spec.
              </span>
            ) : (
              <>
                <span className="font-semibold">Confirming makes permanent:</span> {summary}.
              </>
            )}
          </span>
        </p>
        <div className="flex items-center justify-end gap-2 sm:flex-wrap">
          <button type="button" disabled={!d.dirty || d.saving} onClick={d.discard} title="Discard edits" className={`${ghostBtn} max-sm:px-0`}>
            <Undo2 aria-hidden className="h-3.5 w-3.5" /> <span className="max-sm:sr-only">Discard</span>
          </button>
          <button type="button" disabled={!d.dirty || d.saving} onClick={() => void d.save()} className={secondaryBtn}>
            {d.saving ? (
              "Saving…"
            ) : (
              <>
                Save<span className="max-sm:hidden"> edits</span>
              </>
            )}
          </button>
          <button
            type="button"
            disabled={d.dirty || d.saving || activating}
            title={d.dirty ? "Save or discard your edits first" : undefined}
            onClick={() => {
              setActivateError(null);
              dialog.current?.showModal();
            }}
            className={`${primaryBtn} max-sm:flex-1 max-sm:px-3`}
          >
            <ShieldCheck aria-hidden className="h-4 w-4" /> Confirm &amp; activate
          </button>
        </div>
        {activateError && (
          <p role="alert" className="w-full text-[13px] font-semibold text-danger sm:order-last">
            {activateError.text}{" "}
            {activateError.live && (
              <Link to={`/apps/${encodeURIComponent(appId)}`} className="underline underline-offset-2">
                Open the console
              </Link>
            )}
          </p>
        )}
      </div>

      <dialog
        ref={dialog}
        aria-labelledby="rv-confirm-title"
        className="w-[min(560px,calc(100vw-32px))] rounded-[20px] bg-sheet p-0 text-left text-slate shadow-[var(--shadow-2)] backdrop:bg-slate/25"
      >
        <div className="space-y-4 p-5 sm:p-6">
          <div>
            <h2 id="rv-confirm-title" className="text-[18px] font-bold">
              Activate <span className="font-mono">{appId}</span>?
            </h2>
            <p className="mt-1 text-[13.5px] text-ink">
              The app starts accepting events. After this, no agent can change what's below, and you can't edit it from the review screen.
            </p>
          </div>
          <div className="space-y-1.5">
            <h3 className="text-[13px] font-semibold">How far the AI may change each source</h3>
            <ul className="space-y-1">
              {inputs.map((i) => (
                <li key={i.id} className="flex items-center justify-between gap-3 text-[13px]">
                  <span className="font-mono">{i.id}</span>
                  <TierBadge tier={i.tier} />
                </li>
              ))}
            </ul>
          </div>
          <div className="space-y-1.5">
            <h3 className="text-[13px] font-semibold">Locked weights</h3>
            {locked.length === 0 ? (
              <p className="text-[13px] text-ink">None.</p>
            ) : (
              <ul className="space-y-1">
                {locked.map((l) => (
                  <li key={l.layerId} className="flex items-center justify-between gap-3 font-mono text-[13px]">
                    <span className="break-all">{l.layerId}</span>
                    <span className="tabular-nums">{l.op.weight}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="space-y-1.5">
            <h3 className="text-[13px] font-semibold">How changes are checked, in order</h3>
            {verification.length === 0 ? (
              <p className="text-[13px] text-ink">None: the Critic will never be able to change a weight.</p>
            ) : (
              <ol className="list-decimal space-y-0.5 pl-5 font-mono text-[13px]">
                {verification.map((v) => (
                  <li key={v.method}>
                    <span className="font-sans font-semibold">{VERIFICATION_PLAIN[v.method]?.name ?? v.method}</span>{" "}
                    <span className="text-ink">({v.method})</span>
                  </li>
                ))}
              </ol>
            )}
          </div>
          <p className="rounded-xl bg-attnBg px-3 py-2 text-[13px] font-semibold text-attn">
            Your ingest and authority keys appear on the next screen, once. Have somewhere ready to save them.
          </p>
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" autoFocus onClick={() => dialog.current?.close()} className={ghostBtn} disabled={activating}>
              Cancel
            </button>
            <button type="button" onClick={() => void activate()} disabled={activating} className={primaryBtn}>
              {activating ? "Activating…" : "Activate now"}
            </button>
          </div>
        </div>
      </dialog>
      </div>
    </AppShell>
  );
}
