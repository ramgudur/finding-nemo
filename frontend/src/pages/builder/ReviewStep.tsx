// Step 4: a read-only summary of steps 1–3 (each with an Edit link), then "Compile with Nemotron". While the
// Compiler runs, this page narrates it from GET /compile/status (every 1.5 s). On success the proposed spec
// opens at /apps/<id>/review for the dev to check and confirm. This page never activates the app.
import { LoaderCircle, Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { recordProject, saveSpecSnapshot } from "../../api/authorityKey";
import { ApiError, api } from "../../api/client";
import type { CompileRequest, CompileStatus } from "../../api/types";
import { FailurePanel, toFailure } from "../../components/builder/Failure";
import { StepIntro } from "../../components/builder/Help";
import { StepFooter } from "../../components/builder/StepFooter";
import type { Failure } from "../../components/builder/Failure";
import { KindBadge } from "../../components/sources/SourceSuggestionCard";
import { WebhookSummary } from "../../components/sources/WebhookSummary";
import { useActiveIds, useDraft } from "../../components/builder/useDraft";
import { areaSize } from "../../components/sources/AreaPicker";
import {
  SHAPE_INFO,
  connectorForCompile,
  gridCells,
  listMappingOf,
  localFileLabel,
  localFileOf,
  stepProblems,
  webhookForCompile,
} from "../../components/sources/sourceUtils";
import { useBuilderStore } from "../../store/useBuilderStore";

function Section({ title, edit, children }: { title: string; edit: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2.5 border-t border-[rgba(99,143,145,0.25)] pt-5 first:border-t-0 first:pt-0">
      <div className="flex items-baseline gap-3">
        <h2 className="bld-h3">{title}</h2>
        <Link to={edit} className="bld-link hit relative ml-auto rounded-md px-1 text-sm">
          Edit<span className="sr-only"> {title.toLowerCase()}</span>
        </Link>
      </div>
      {children}
    </section>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-0.5 sm:grid-cols-[9rem_1fr] sm:gap-3">
      <dt className="text-[13px] text-ink">{label}</dt>
      <dd className="min-w-0 text-[14px] text-slate">{children}</dd>
    </div>
  );
}

/** Plain-language narration of the Compiler from the public status (no trace text is exposed there). */
function narrate(st: CompileStatus | null, elapsed: number): { line: string; detail: string } {
  const errs = st?.last_validate?.errors.length ?? 0;
  const warns = st?.last_validate?.warnings.length ?? 0;
  if (!st || st.step === 0) {
    return {
      line: elapsed < 4 ? "Sending your draft to the Compiler…" : "Nemotron is reading your intent and sources…",
      detail: "It picks how each source becomes a map layer, proposes how much each one counts, and what to verify.",
    };
  }
  if (errs > 0) {
    return {
      line: `Fixing ${errs} problem${errs === 1 ? "" : "s"} the platform's checker found…`,
      detail: "The Compiler only proposes; the platform's own rules check every proposal before it's accepted.",
    };
  }
  if (st.last_validate) {
    return {
      line: "The plan passed the platform's checks. Finishing the proposal…",
      detail: warns
        ? `${warns} note${warns === 1 ? "" : "s"} for you to review on the next page.`
        : "You'll review every proposed setting before anything goes live.",
    };
  }
  return {
    line: `Drafting the plan (step ${st.step})…`,
    detail: "Choosing operators and weights for each source, then checking them against the platform's rules.",
  };
}

export function ReviewStep() {
  const navigate = useNavigate();
  const draft = useDraft();
  const activeIds = useActiveIds();
  const compiling = useBuilderStore((s) => s.compiling);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [status, setStatus] = useState<CompileStatus | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [cooldownS, setCooldownS] = useState(0);
  const alive = useRef(true);
  const progressRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const problems = stepProblems("review", draft, activeIds);
  const { appId, bbox, cellM } = draft;
  const dims = bbox && cellM > 0 ? gridCells(bbox, cellM) : null;

  // Retry-After countdown (503 / 429).
  useEffect(() => {
    if (cooldownS <= 0) return;
    const t = setTimeout(() => setCooldownS((c) => Math.max(0, c - 1)), 1000);
    return () => clearTimeout(t);
  }, [cooldownS]);

  // While compiling: a local timer, plus the public status every 1.5 s.
  useEffect(() => {
    if (!compiling) return;
    const t0 = Date.now();
    const tick = setInterval(() => setElapsed(Math.round((Date.now() - t0) / 1000)), 1000);
    let on = true;
    const poll = setInterval(() => {
      api
        .compileStatus(appId)
        .then((st) => on && setStatus(st))
        .catch(() => undefined); // 404 before the run registers: keep the local timer
    }, 1500);
    // The panel renders below the summary, often under the fixed bottom bar: bring it to the middle of the
    // viewport (instantly under reduced motion), then move focus there without a second jump.
    const panel = progressRef.current;
    if (panel) {
      const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
      panel.scrollIntoView({ block: "center", behavior: reduce ? "auto" : "smooth" });
      panel.focus({ preventScroll: true });
    }
    return () => {
      on = false;
      clearInterval(tick);
      clearInterval(poll);
    };
  }, [compiling, appId]);

  const compile = async () => {
    if (problems.length > 0 || !bbox || compiling || cooldownS > 0) return;
    const st = useBuilderStore.getState();
    const body: CompileRequest = {
      intent: draft.intent.trim(),
      grid: { cell_m: cellM, bbox },
      inputs: draft.sources.map((x) => ({
        id: x.id,
        kind: x.kind,
        shape: x.shape,
        meaning: x.meaning,
        fields: x.fields ?? [],
        ...(x.kind === "pull" && x.connector ? { connector: connectorForCompile(x.connector, x.fields) } : {}),
        ...(x.kind === "push" && x.webhook ? { webhook: webhookForCompile(x.webhook, x.fields) } : {}),
      })),
      destination_points: draft.destinationPoints.map((p) => ({ ...p, label: p.label.trim() })),
    };
    setFailure(null);
    setStatus(null);
    setElapsed(0);
    st.setCompileError("");
    st.setCompiling(true);
    st.setAppStatus("compiling");
    try {
      const spec = await api.compile(appId, body);
      const now = new Date().toISOString();
      const s2 = useBuilderStore.getState();
      s2.setCompiledSpec(spec);
      s2.setCompiledAt(now);
      s2.setActivation(null);
      s2.setAppStatus("proposed");
      saveSpecSnapshot(spec); // lets the review page survive a refresh (pending specs only)
      recordProject({ appId, name: draft.name.trim(), intent: draft.intent.trim(), status: "proposed", updatedAt: now });
      // Navigate even if the dev wandered to another step meanwhile: the result is what they waited for.
      navigate(`/apps/${encodeURIComponent(appId)}/review`);
    } catch (e) {
      const f = toFailure(e, "compile", appId);
      if (e instanceof ApiError && e.status === 409) {
        recordProject({ appId, name: draft.name.trim(), intent: draft.intent.trim(), status: "active", updatedAt: new Date().toISOString() });
      }
      if (alive.current) setFailure(f);
      const s2 = useBuilderStore.getState();
      s2.setCompileError(f.reason);
      s2.setAppStatus(s2.compiledSpec ? "proposed" : "idle");
      if (e instanceof ApiError && e.retryAfterS != null && alive.current) setCooldownS(Math.ceil(e.retryAfterS));
    } finally {
      useBuilderStore.getState().setCompiling(false);
    }
  };

  const shownElapsed = status?.running ? Math.round(status.elapsed_s) : elapsed;
  const story = narrate(status, shownElapsed);
  const blocked = problems.length > 0 || cooldownS > 0;

  return (
    <>
      <StepIntro
        step={4}
        title="Review & compile"
        lead="Check your draft, then compile. Nemotron proposes a plan: how each source becomes map layers, how much each counts, and how changes are checked. Nothing goes live until you confirm it on the next page."
      />

      <div className="bld-card flex flex-col gap-5 px-5 py-5 sm:px-6">
        <Section title="About" edit="/new/about">
          <dl className="flex flex-col gap-2">
            <Row label="Name">{draft.name || "—"}</Row>
            <Row label="App id">
              <span className="font-mono text-[13px]">{appId}</span>
            </Row>
            <Row label="What it should do">
              <span className="whitespace-pre-line">{draft.intent}</span>
            </Row>
          </dl>
        </Section>

        <Section title="Area" edit="/new/area">
          <dl className="flex flex-col gap-2">
            <Row label="Area">{bbox ? areaSize(bbox) : "not set"}</Row>
            <Row label="Grid">
              {dims ? `${(dims[0] * dims[1]).toLocaleString()} cells of ${cellM} m` : "—"}
            </Row>
            <Row label="Destinations">
              {draft.destinationPoints.length === 0
                ? "None (optional)"
                : draft.destinationPoints.map((p) => p.label.trim() || "(no label)").join(", ")}
            </Row>
          </dl>
        </Section>

        <Section title={`Sources (${draft.sources.length})`} edit="/new/sources">
          {draft.sources.length === 0 ? (
            <p className="text-sm text-ink">None yet.</p>
          ) : (
            <ul className="flex flex-col gap-2.5">
              {draft.sources.map((x, i) => (
                <li key={`${x.id}-${i}`} className="flex flex-col gap-0.5">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[13px] font-semibold text-slate">{x.id}</span>
                    <KindBadge kind={x.kind} small />
                    <span className="text-xs text-ink">{SHAPE_INFO[x.shape].label.toLowerCase()}</span>
                  </span>
                  <span className="text-[14px] text-slate">“{x.meaning}”</span>
                  {(x.fields?.length ?? 0) > 0 && (
                    <span className="font-mono text-[11px] text-ink">
                      + {x.fields!.map((f) => `${f.name}:${f.type}${f.required === false ? "?" : ""}`).join(", ")}
                    </span>
                  )}
                  {x.kind === "push" && x.webhook && <WebhookSummary w={webhookForCompile(x.webhook, x.fields)} />}
                  {x.connector?.type === "local_file" && (
                    <span className="text-[13px] text-ink">
                      {localFileLabel(x.connector)} · <span className="font-mono">{localFileOf(x.connector)}</span>
                    </span>
                  )}
                  {x.connector?.type === "json_list" && (
                    <WebhookSummary
                      title={`Fetches a JSON list from ${x.connector.url ?? ""}`}
                      w={webhookForCompile(listMappingOf(x.connector), x.fields)}
                    />
                  )}
                </li>
              ))}
            </ul>
          )}
        </Section>
      </div>

      {compiling ? (
        <div
          ref={progressRef}
          tabIndex={-1}
          role="status"
          aria-live="polite"
          className="bld-card flex scroll-mb-32 scroll-mt-28 flex-col gap-3 px-5 py-5 sm:px-6"
        >
          <div className="flex items-center gap-3">
            <LoaderCircle size={20} className="shrink-0 animate-spin text-primary motion-reduce:animate-none" aria-hidden />
            <p className="text-[16px] font-semibold text-slate">{story.line}</p>
            <span className="ml-auto font-mono text-sm tabular-nums text-ink">{shownElapsed}s</span>
          </div>
          <p className="text-sm text-ink">{story.detail}</p>
          <p className="text-xs text-ink">A compile usually takes 30 s to 2 minutes. You can stay on this page.</p>
        </div>
      ) : (
        <div className="flex flex-col items-start gap-2">
          <button
            type="button"
            onClick={() => void compile()}
            disabled={blocked}
            className="bld-btn bld-btn-primary tap px-6 py-3 text-[15px]"
          >
            <Sparkles size={17} aria-hidden /> Compile with Nemotron
          </button>
          <p className="bld-hint">
            {cooldownS > 0
              ? `The model is busy. You can try again in ${cooldownS} s.`
              : problems.length
                ? problems[0]
                : "This only proposes a plan, usually in 30 s to 2 minutes. The app is not activated."}
          </p>
        </div>
      )}

      {failure && !compiling && <FailurePanel failure={failure} onClose={() => setFailure(null)} />}

      <StepFooter
        back={{ to: "/new/sources", label: "Sources" }}
        // While compiling, the bar mirrors the narrated step (the panel above is the live region).
        live={!compiling}
        problems={compiling ? [story.line] : blocked ? [problems[0] ?? "The model is busy; try again shortly."] : []}
        readyText="Ready to compile."
      />
    </>
  );
}
