// Results of the real POST /v1/apps/{app}/sources/suggest, split into push (the dev's app sends events to
// a generated endpoint with its ingest key) and pull (the platform polls a connector). Push options add
// a source on click (a webhook option adds it with an empty mapping to fill on its row); pull options open
// their connector settings first. "JSON list / GeoJSON feed" is always offered under "We fetch it".
import { ArrowDownToLine, ChevronDown, Plus, RefreshCw, X } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useState } from "react";
import type { BBox, CompileInput, InputShape, SourceOption, SourceSuggestion } from "../../api/types";
import { useBuilderStore } from "../../store/useBuilderStore";
import { ConnectorConfigFields } from "./ConnectorConfigFields";
import { SmsCardDisabled, SourceSuggestionCard } from "./SourceSuggestionCard";
import {
  JSON_LIST_OPTION,
  OPTION_KIND,
  SHAPES,
  SHAPE_INFO,
  connectorDraftFor,
  connectorOf,
  connectorProblem,
  emptyWebhook,
  optionKey,
  slugFromDescription,
} from "./sourceUtils";
import type { ConnectorDraft } from "./sourceUtils";

interface Props {
  suggestion: SourceSuggestion;
  bbox: BBox | null;
  takenIds: string[];
  onAdd: (input: CompileInput) => void;
  onDismiss: () => void;
}

export function SourceSuggestions({ suggestion, bbox, takenIds, onAdd, onDismiss }: Props) {
  const reduce = useReducedMotion();
  const unranked = new Set(suggestion.options.map((o) => OPTION_KIND[o.type])).size > 1;
  const [shape, setShape] = useState<InputShape>(suggestion.shape);
  const [picked, setPicked] = useState<SourceOption | null>(null);
  const [draft, setDraft] = useState<ConnectorDraft>({ url: "", configText: "" });
  const [showShape, setShowShape] = useState(unranked);

  const push = suggestion.options.filter((o) => OPTION_KIND[o.type] === "push");
  const suggestedPull = suggestion.options.filter((o) => OPTION_KIND[o.type] === "pull");
  const pull = suggestedPull.some((o) => o.type === "json_list") ? suggestedPull : [...suggestedPull, JSON_LIST_OPTION];
  const showSms = unranked || suggestion.kind === "push";

  const build = (o: SourceOption, d: ConnectorDraft): CompileInput => ({
    id: slugFromDescription(suggestion.meaning, takenIds),
    kind: OPTION_KIND[o.type],
    shape,
    meaning: suggestion.meaning, // the dev's words, verbatim
    fields: [],
    connector: connectorOf(o, d),
    ...(o.type === "webhook" ? { webhook: emptyWebhook() } : {}),
  });

  const add = (o: SourceOption, d: ConnectorDraft) => {
    const input = build(o, d);
    // a sample fetched or pasted while setting up a JSON list follows the source to its row
    if (o.type === "json_list" && d.sample?.trim()) useBuilderStore.getState().setWebhookSample(input.id, d.sample);
    onAdd(input);
  };

  const pick = (o: SourceOption) => {
    if (OPTION_KIND[o.type] === "push") {
      add(o, { url: "", configText: "" });
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

  const column = (kind: "push" | "pull", opts: SourceOption[]) => (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-start gap-2 px-1">
        {kind === "push" ? (
          <ArrowDownToLine size={15} className="mt-0.5 shrink-0 text-primary" />
        ) : (
          <RefreshCw size={15} className="mt-0.5 shrink-0 text-mutedTeal" />
        )}
        <div className="flex flex-col">
          <span className="bld-h3">{kind === "push" ? "Your app sends it" : "We fetch it"}</span>
          <span className="bld-hint">
            {kind === "push"
              ? "Your app (or another service) sends each item to an address we give you, with a key you get when the app goes live."
              : "The platform checks a feed or web address on a schedule. You don't run anything."}
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
                transition={{ duration: reduce ? 0 : 0.22, ease: [0.22, 1, 0.36, 1] }}
                className="overflow-hidden"
              >
                <div className="flex flex-col gap-4 rounded-[14px] bg-white/45 px-4 py-4">
                  <ConnectorConfigFields option={o} draft={draft} onChange={setDraft} />
                  <div className="flex flex-wrap items-center gap-3">
                    <button
                      type="button"
                      disabled={!!problem}
                      onClick={() => add(o, draft)}
                      className="bld-btn bld-btn-primary tap"
                    >
                      <Plus size={15} aria-hidden /> Add this source
                    </button>
                    {problem && <span className="bld-hint">{problem}</span>}
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      ))}
      {kind === "push" && showSms && <SmsCardDisabled />}
      {opts.length === 0 && !(kind === "push" && showSms) && (
        <p className="px-1 text-xs italic text-ink">Nothing suggested of this kind.</p>
      )}
    </div>
  );

  return (
    <motion.section
      initial={reduce ? { opacity: 0 } : { opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={reduce ? { opacity: 0 } : { opacity: 0, y: -6 }}
      transition={{ duration: reduce ? 0 : 0.22, ease: [0.22, 1, 0.36, 1] }}
      className="bld-card flex flex-col gap-5 px-5 py-5 sm:px-6"
      aria-label="source suggestions"
    >
      <div className="flex items-start gap-3">
        <div className="flex min-w-0 flex-1 flex-col gap-1.5">
          <h2 className="bld-h2">Pick how it connects</h2>
          <p className="truncate text-[15px] text-ink" title={suggestion.meaning}>
            “{suggestion.meaning}”
          </p>
          <div className="flex flex-col gap-1 text-[13px] text-ink">
            {unranked ? (
              <span className="text-danger">
                The model couldn't rank these, so every option is listed. Pick one and check what each item is.
              </span>
            ) : (
              <span>
                {suggestion.kind === "push" ? "Looks like something your app sends." : "Looks like something we can fetch."}
              </span>
            )}
            <span>
              Each item: <strong className="font-semibold text-slate">{SHAPE_INFO[shape].label.toLowerCase()}</strong>
              <span> · {SHAPE_INFO[shape].hint}</span>
              {!showShape && (
                <button
                  type="button"
                  onClick={() => setShowShape(true)}
                  className="ml-2 inline-flex items-center gap-0.5 rounded-md px-1 text-ink underline underline-offset-[3px] hover:text-slate"
                >
                  change <ChevronDown size={12} aria-hidden />
                </button>
              )}
            </span>
            {showShape && (
              <label className="flex flex-wrap items-center gap-2">
                <span>What each item is:</span>
                <select
                  value={shape}
                  onChange={(e) => setShape(e.target.value as InputShape)}
                  className="bld-field is-sm w-fit"
                >
                  {SHAPES.map((x) => (
                    <option key={x} value={x}>
                      {SHAPE_INFO[x].label}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
        </div>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss suggestions"
          className="tap flex items-center justify-center rounded-full p-1.5 text-ink transition-colors duration-150 hover:bg-white/60 hover:text-slate"
        >
          <X size={16} aria-hidden />
        </button>
      </div>
      <div className="grid gap-5 md:grid-cols-2">
        {column("push", push)}
        {column("pull", pull)}
      </div>
    </motion.section>
  );
}
