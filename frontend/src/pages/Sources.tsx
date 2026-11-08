// Page 2: Sources. App id gates the page; then the area, the app's intent and its sources, each source
// described in plain words and connected through the real /sources/suggest. Continue runs the real
// compile (progress from /compile/status) and lands on Architecture with the proposed spec. It never
// activates the app: activation is the dev's explicit confirmation on the Architecture page.
import { ArrowLeft, Pencil } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { recordProject } from "../api/authorityKey";
import { ApiError, api } from "../api/client";
import type { CompileFailure, CompileInput, CompileRequest, CompileStatus, SourceSuggestion } from "../api/types";
import { AppIdField } from "../components/sources/AppIdField";
import { AreaPicker } from "../components/sources/AreaPicker";
import { IntentInput } from "../components/sources/IntentInput";
import { SourceChipBar } from "../components/sources/SourceChipBar";
import { SourceSuggestions } from "../components/sources/SourceSuggestions";
import { SLUG, draftProblems } from "../components/sources/sourceUtils";
import { useBuilderStore } from "../store/useBuilderStore";

/** A compile or suggest failure, already in the dev's words. */
interface Failure {
  title: string;
  reason: string;
  errors: string[];
  warnings: string[];
}

function isCompileFailure(d: unknown): d is CompileFailure {
  return !!d && typeof d === "object" && typeof (d as CompileFailure).reason === "string";
}

function toFailure(e: unknown, what: "compile" | "suggest"): Failure {
  if (!(e instanceof ApiError)) return { title: "Couldn't reach the platform", reason: String(e), errors: [], warnings: [] };
  if (e.status === 409) {
    return {
      title: "This app is already active",
      reason: "An active app is never re-compiled. Use a new app id to build another one.",
      errors: [],
      warnings: [],
    };
  }
  if (e.status === 503 || e.status === 429) {
    const msg = e.message.replace(/[.\s]+$/, "");
    // The server's message usually says when to retry already; add it only when it doesn't.
    const wait = /try again/i.test(msg)
      ? ""
      : e.retryAfterS != null
        ? ` Try again in about ${Math.ceil(e.retryAfterS)} s.`
        : " Try again in a minute.";
    return { title: "The model budget is busy", reason: `${msg}.${wait}`, errors: [], warnings: [] };
  }
  if (e.status === 422 && isCompileFailure(e.detail)) {
    return {
      title: "The Compiler couldn't produce a valid spec",
      reason: e.detail.reason,
      errors: e.detail.errors ?? [],
      warnings: e.detail.warnings ?? [],
    };
  }
  if (e.status === 422) {
    return {
      title: what === "compile" ? "The compile was rejected" : "That description was rejected",
      reason: e.details.length ? "Fix these and try again:" : e.message,
      errors: e.details,
      warnings: [],
    };
  }
  return { title: what === "compile" ? "Compile failed" : "Suggestion failed", reason: e.message, errors: [], warnings: [] };
}

function FailurePanel({ failure, onClose }: { failure: Failure; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion();
  useEffect(() => {
    // The bottom bar covers the end of the page: bring the error into view.
    ref.current?.scrollIntoView({ block: "center", behavior: reduce ? "auto" : "smooth" });
  }, [failure, reduce]);
  return (
    <motion.div
      ref={ref}
      role="alert"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 6 }}
      className="flex flex-col gap-2 rounded-2xl border border-coral/40 bg-[#F6E6DD]/85 px-5 py-4 text-sm text-slate shadow-[0_10px_30px_-20px_rgba(150,80,60,0.6)]"
    >
      <div className="flex items-start gap-3">
        <strong className="font-semibold text-[#9C5A45]">{failure.title}</strong>
        <button type="button" onClick={onClose} className="ml-auto text-xs text-ink underline-offset-2 hover:underline">
          dismiss
        </button>
      </div>
      <p className="text-ink">{failure.reason}</p>
      {failure.errors.length > 0 && (
        <div>
          <span className="text-xs font-semibold uppercase tracking-wider text-[#9C5A45]">errors</span>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 font-mono text-[12px] text-slate">
            {failure.errors.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </div>
      )}
      {failure.warnings.length > 0 && (
        <div>
          <span className="text-xs font-semibold uppercase tracking-wider text-inkSoft">warnings</span>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 font-mono text-[12px] text-ink">
            {failure.warnings.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </div>
      )}
    </motion.div>
  );
}

/** The intent, shown small above the big input once set; click to edit in place. */
function IntentSummary({ intent, onSave, disabled }: { intent: string; onSave: (v: string) => void; disabled?: boolean }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(intent);
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => setText(intent), [intent]);
  useLayoutEffect(() => {
    const t = ref.current;
    if (!t) return;
    t.style.height = "auto";
    t.style.height = `${t.scrollHeight}px`;
  }, [text, editing]);
  useEffect(() => {
    if (editing) ref.current?.focus();
  }, [editing]);
  const commit = () => {
    setEditing(false);
    if (text.trim()) onSave(text.trim());
    else setText(intent);
  };
  return (
    <div className="flex flex-col gap-1 px-2">
      <span className="text-xs font-semibold uppercase tracking-[0.14em] text-inkSoft">Intent</span>
      {editing ? (
        <textarea
          ref={ref}
          rows={1}
          value={text}
          aria-label="intent"
          onChange={(e) => setText(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              commit();
            }
            if (e.key === "Escape") {
              setText(intent);
              setEditing(false);
            }
          }}
          className="w-full resize-none overflow-hidden rounded-xl border border-white/70 bg-cream/70 px-3 py-1.5 text-[15px] leading-snug text-slate outline-none"
        />
      ) : (
        <button
          type="button"
          disabled={disabled}
          onClick={() => setEditing(true)}
          className="group flex items-start gap-2 rounded-xl py-0.5 text-left text-[15px] leading-snug text-slate"
          aria-label="edit intent"
        >
          <span>{intent}</span>
          <Pencil size={13} className="mt-1 shrink-0 text-inkSoft opacity-50 transition group-hover:opacity-100" />
        </button>
      )}
    </div>
  );
}

export function Sources() {
  const navigate = useNavigate();
  const reduce = useReducedMotion();
  const s = useBuilderStore();
  const { appId, intent, bbox, cellM, destinationPoints, sources, compiling } = s;

  const [text, setText] = useState("");
  const [suggestion, setSuggestion] = useState<SourceSuggestion | null>(null);
  const [suggesting, setSuggesting] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [status, setStatus] = useState<CompileStatus | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [cooldownS, setCooldownS] = useState(0);
  const suggestSeq = useRef(0);

  const idOk = SLUG.test(appId);
  const mode: "intent" | "source" = intent.trim() ? "source" : "intent";
  const problems = useMemo(
    () => draftProblems({ appId, intent, bbox, cellM, destinationPoints, sources }),
    [appId, intent, bbox, cellM, destinationPoints, sources],
  );

  // Retry-After countdown (503 / 429): Continue stays disabled until it runs out.
  useEffect(() => {
    if (cooldownS <= 0) return;
    const t = setTimeout(() => setCooldownS((c) => Math.max(0, c - 1)), 1000);
    return () => clearTimeout(t);
  }, [cooldownS]);

  // Progress while compiling: a local timer plus /compile/status every ~1.5 s.
  useEffect(() => {
    if (!compiling) return;
    const t0 = Date.now();
    const tick = setInterval(() => setElapsed(Math.round((Date.now() - t0) / 1000)), 1000);
    let alive = true;
    const poll = setInterval(() => {
      api
        .compileStatus(appId)
        .then((st) => alive && setStatus(st))
        .catch(() => undefined); // 404 before the run registers: keep the local timer
    }, 1500);
    return () => {
      alive = false;
      clearInterval(tick);
      clearInterval(poll);
    };
  }, [compiling, appId]);

  const submit = async () => {
    const v = text.trim();
    if (!v || !idOk) return;
    if (mode === "intent") {
      s.setIntent(v);
      setText("");
      return;
    }
    const seq = ++suggestSeq.current;
    setSuggesting(true);
    setFailure(null);
    try {
      const sug = await api.suggestSource(appId, { intent: intent.trim(), description: v });
      if (seq === suggestSeq.current) setSuggestion(sug);
    } catch (e) {
      if (seq !== suggestSeq.current) return;
      setFailure(toFailure(e, "suggest"));
    } finally {
      if (seq === suggestSeq.current) setSuggesting(false);
    }
  };

  const add = (input: CompileInput) => {
    s.addSource(input);
    setSuggestion(null);
    setText("");
  };

  const compile = async () => {
    if (problems.length > 0 || !bbox || compiling) return;
    const body: CompileRequest = {
      intent: intent.trim(),
      grid: { cell_m: cellM, bbox },
      inputs: sources.map((x) => ({
        id: x.id,
        kind: x.kind,
        shape: x.shape,
        meaning: x.meaning,
        fields: x.fields ?? [],
        ...(x.kind === "pull" && x.connector ? { connector: x.connector } : {}),
      })),
      destination_points: destinationPoints.map((p) => ({ ...p, label: p.label.trim() })),
    };
    setFailure(null);
    setStatus(null);
    setElapsed(0);
    s.setCompileError("");
    s.setCompiling(true);
    s.setAppStatus("compiling");
    try {
      const spec = await api.compile(appId, body);
      s.setCompiledSpec(spec);
      s.setAppStatus("proposed");
      recordProject({ appId, intent: intent.trim(), status: "proposed", updatedAt: new Date().toISOString() });
      navigate("/architecture");
    } catch (e) {
      const f = toFailure(e, "compile");
      setFailure(f);
      s.setCompileError(f.reason);
      s.setAppStatus(useBuilderStore.getState().compiledSpec ? "proposed" : "idle");
      if (e instanceof ApiError && e.retryAfterS != null) setCooldownS(Math.ceil(e.retryAfterS));
    } finally {
      s.setCompiling(false);
    }
  };

  const shownElapsed = status?.running ? Math.round(status.elapsed_s) : elapsed;
  const lastErrors = status?.last_validate?.errors.length ?? 0;
  const progress = `compiling · step ${status?.step ?? 0} · ${shownElapsed}s${
    lastErrors ? ` · fixing ${lastErrors} spec error${lastErrors > 1 ? "s" : ""}` : ""
  }`;

  return (
    <main className="sky-wash relative min-h-screen overflow-x-hidden">
      <div aria-hidden className="sky-mist pointer-events-none fixed inset-0 overflow-hidden">
        <span className="mist-a" />
        <span className="mist-b" />
        <span className="mist-c" />
      </div>
      <div aria-hidden className="paper-grain pointer-events-none fixed inset-0" />
      {/* Soft mist band so content scrolling under the fixed header fades instead of colliding with the logo. */}
      <div
        aria-hidden
        className="pointer-events-none fixed inset-x-0 top-0 z-10 h-20 bg-gradient-to-b from-[#d2e6ec] via-[#d2e6ec]/85 to-transparent"
      />

      <motion.div
        initial={reduce ? { opacity: 0 } : { opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: reduce ? 0.01 : 0.55, ease: [0.22, 1, 0.36, 1] }}
        className="relative mx-auto flex max-w-3xl flex-col gap-7 px-4 pb-48 pt-20 sm:px-6 sm:pt-24"
      >
        <header className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => navigate("/")}
            aria-label="back to home"
            className="flex h-9 w-9 items-center justify-center rounded-full border border-white/70 bg-cream/70 text-slate shadow-sm transition hover:-translate-y-0.5 hover:bg-cream"
          >
            <ArrowLeft size={17} />
          </button>
          <h1 className="text-2xl font-semibold tracking-tight text-slate">Sources</h1>
        </header>

        <AppIdField value={appId} onChange={s.setAppId} />

        <div
          className={`flex flex-col gap-7 transition-opacity duration-300 ${idOk ? "" : "pointer-events-none select-none opacity-40"}`}
          aria-disabled={!idOk}
        >
          <AreaPicker
            bbox={bbox}
            onBbox={s.setBbox}
            cellM={cellM}
            onCellM={s.setCellM}
            points={destinationPoints}
            onAddPoint={s.addDestinationPoint}
            onUpdatePoint={s.updateDestinationPoint}
            onRemovePoint={s.removeDestinationPoint}
          />

          <div className="flex flex-col gap-3">
            <AnimatePresence initial={false}>
              {mode === "source" && (
                <motion.div
                  key="intent-summary"
                  initial={reduce ? { opacity: 0 } : { opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: reduce ? 0 : 0.4, ease: [0.22, 1, 0.36, 1] }}
                >
                  <IntentSummary intent={intent} onSave={s.setIntent} disabled={compiling} />
                </motion.div>
              )}
            </AnimatePresence>
            <IntentInput
              mode={mode}
              value={text}
              onChange={setText}
              onSubmit={() => void submit()}
              disabled={!idOk || compiling}
              busy={suggesting}
              autoFocus={idOk}
            />
          </div>

          <AnimatePresence mode="wait">
            {suggestion && (
              <SourceSuggestions
                key={suggestion.meaning}
                appId={appId}
                suggestion={suggestion}
                bbox={bbox}
                takenIds={sources.map((x) => x.id)}
                onAdd={add}
                onDismiss={() => setSuggestion(null)}
              />
            )}
          </AnimatePresence>

          <AnimatePresence>
            {failure && <FailurePanel key="failure" failure={failure} onClose={() => setFailure(null)} />}
          </AnimatePresence>
        </div>
      </motion.div>

      <SourceChipBar
        sources={sources}
        problems={problems}
        compiling={compiling}
        progress={progress}
        cooldownS={cooldownS}
        onRename={(i, id) => s.updateSource(i, { id })}
        onRemove={s.removeSourceAt}
        onContinue={() => void compile()}
      />
    </main>
  );
}
