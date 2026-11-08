// Results of the real POST /v1/apps/{app}/sources/suggest, split into push (the dev's app sends events to
// a generated endpoint with its ingest key) and pull (the platform polls a connector). Push options add
// a source on click; pull options open their connector settings first.
import { ArrowDownToLine, Plus, RefreshCw, X } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useState } from "react";
import type { BBox, CompileInput, InputShape, SourceOption, SourceSuggestion } from "../../api/types";
import { ConnectorConfigFields } from "./ConnectorConfigFields";
import { SmsCardDisabled, SourceSuggestionCard } from "./SourceSuggestionCard";
import {
  OPTION_KIND,
  connectorDraftFor,
  connectorOf,
  connectorProblem,
  optionKey,
  slugFromDescription,
} from "./sourceUtils";
import type { ConnectorDraft } from "./sourceUtils";

const SHAPES: InputShape[] = ["point", "track", "fixed_point", "field", "document"];
const SHAPE_HINT: Record<InputShape, string> = {
  point: "one item is a point event",
  track: "items form moving tracks",
  fixed_point: "one item is a fixed point",
  field: "items are gridded / station values",
  document: "one item is a document",
};

interface Props {
  appId: string;
  suggestion: SourceSuggestion;
  bbox: BBox | null;
  takenIds: string[];
  onAdd: (input: CompileInput) => void;
  onDismiss: () => void;
}

export function SourceSuggestions({ appId, suggestion, bbox, takenIds, onAdd, onDismiss }: Props) {
  const reduce = useReducedMotion();
  const unranked = new Set(suggestion.options.map((o) => OPTION_KIND[o.type])).size > 1;
  const [shape, setShape] = useState<InputShape>(suggestion.shape);
  const [picked, setPicked] = useState<SourceOption | null>(null);
  const [draft, setDraft] = useState<ConnectorDraft>({ url: "", configText: "" });

  const push = suggestion.options.filter((o) => OPTION_KIND[o.type] === "push");
  const pull = suggestion.options.filter((o) => OPTION_KIND[o.type] === "pull");
  const showSms = unranked || suggestion.kind === "push";

  const build = (o: SourceOption, d: ConnectorDraft): CompileInput => ({
    id: slugFromDescription(suggestion.meaning, takenIds),
    kind: OPTION_KIND[o.type],
    shape,
    meaning: suggestion.meaning, // the dev's words, verbatim
    fields: [],
    connector: connectorOf(o, d),
  });

  const pick = (o: SourceOption) => {
    if (OPTION_KIND[o.type] === "push") {
      onAdd(build(o, { url: "", configText: "" }));
      return;
    }
    if (picked && optionKey(picked) === optionKey(o)) {
      setPicked(null);
      return;
    }
    setPicked(o);
    setDraft(connectorDraftFor(o, bbox));
  };

  const problem = picked ? connectorProblem(picked, draft) : null;
  const endpoint = `/v1/apps/${appId}/inputs/<id>/events`;

  const column = (kind: "push" | "pull", opts: SourceOption[]) => (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-start gap-2 px-1">
        {kind === "push" ? (
          <ArrowDownToLine size={15} className="mt-0.5 shrink-0 text-[#4F80B0]" />
        ) : (
          <RefreshCw size={15} className="mt-0.5 shrink-0 text-mutedTeal" />
        )}
        <div className="flex flex-col">
          <span className="text-sm font-semibold text-slate">{kind === "push" ? "Push" : "Pull"}</span>
          <span className="text-xs text-ink">
            {kind === "push"
              ? "your app sends events to an endpoint (ingest key)"
              : "the platform polls a connector"}
          </span>
        </div>
      </div>
      {opts.map((o, i) => (
        <div key={optionKey(o)} className="flex flex-col gap-2">
          <SourceSuggestionCard
            option={o}
            index={i + (kind === "pull" ? push.length : 0)}
            selected={!!picked && optionKey(picked) === optionKey(o)}
            onPick={() => pick(o)}
          />
          <AnimatePresence initial={false}>
            {picked && optionKey(picked) === optionKey(o) && (
              <motion.div
                initial={reduce ? { opacity: 0 } : { opacity: 0, height: 0 }}
                animate={reduce ? { opacity: 1 } : { opacity: 1, height: "auto" }}
                exit={reduce ? { opacity: 0 } : { opacity: 0, height: 0 }}
                transition={{ duration: reduce ? 0 : 0.28, ease: [0.22, 1, 0.36, 1] }}
                className="overflow-hidden"
              >
                <div className="flex flex-col gap-3 rounded-2xl border border-white/60 bg-white/35 px-4 py-3">
                  <ConnectorConfigFields option={o} draft={draft} onChange={setDraft} />
                  <div className="flex items-center gap-3">
                    <button
                      type="button"
                      disabled={!!problem}
                      onClick={() => onAdd(build(o, draft))}
                      className="pill-btn pill-primary"
                    >
                      <Plus size={15} /> Add source
                    </button>
                    {problem && <span className="text-xs text-coral">{problem}</span>}
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      ))}
      {kind === "push" && showSms && <SmsCardDisabled />}
      {opts.length === 0 && !(kind === "push" && showSms) && (
        <p className="px-1 text-xs italic text-inkSoft">No {kind} option suggested for this source.</p>
      )}
    </div>
  );

  return (
    <motion.section
      initial={reduce ? { opacity: 0 } : { opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={reduce ? { opacity: 0 } : { opacity: 0, y: -6 }}
      transition={{ duration: reduce ? 0 : 0.35, ease: [0.22, 1, 0.36, 1] }}
      className="flex flex-col gap-4 rounded-3xl border border-white/55 bg-washedSky/50 px-5 py-5 shadow-[0_14px_50px_-30px_rgba(53,89,107,0.55)] backdrop-blur-[2px] sm:px-6"
      aria-label="source suggestions"
    >
      <div className="flex items-start gap-3">
        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <span className="text-xs font-semibold uppercase tracking-[0.14em] text-inkSoft">Ways to connect it</span>
          <p className="truncate text-[15px] text-slate" title={suggestion.meaning}>
            “{suggestion.meaning}”
          </p>
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-ink">
            {unranked ? (
              <span className="text-[#9A6A3A]">
                The model couldn't rank these, so every option is listed. Pick one and check the shape.
              </span>
            ) : (
              <span>
                Looks like a <strong className="font-semibold text-slate">{suggestion.kind}</strong> source;
              </span>
            )}
            <label className="flex items-center gap-1.5">
              <span className="sr-only">shape</span>
              <select
                value={shape}
                onChange={(e) => setShape(e.target.value as InputShape)}
                className="rounded-lg border border-white/70 bg-white/50 px-1.5 py-0.5 font-mono text-xs text-slate outline-none"
              >
                {SHAPES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
              <span className="text-inkSoft">{SHAPE_HINT[shape]}</span>
            </label>
          </div>
        </div>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="dismiss suggestions"
          className="rounded-full p-1.5 text-ink transition hover:bg-white/50"
        >
          <X size={16} />
        </button>
      </div>
      <div className="grid gap-5 md:grid-cols-2">
        {column("push", push)}
        {column("pull", pull)}
      </div>
      {push.length > 0 && (
        <p className="font-mono text-[11px] text-inkSoft">
          push endpoint: POST {endpoint} · header X-Ingest-Key (shown once at activation)
        </p>
      )}
    </motion.section>
  );
}
