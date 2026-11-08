// The answer to POST /v1/apps/{app}/sources/suggest, as ONE card: the top-ranked way to connect the source
// with a primary "Add", "Other ways" for the rest of the ranked list (plus "JSON list / GeoJSON feed", always
// offered), and "Details" for what each item is and the option's reasoning. A push option adds at once (a
// webhook option adds with an empty mapping, which its row then asks for); a pull option shows the settings
// it can't be added without (its address, a JSON list's item mapping) right in the card.
import { ChevronDown, Plus, X } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useId, useMemo, useState } from "react";
import type { BBox, CompileInput, InputShape, SourceOption, SourceSuggestion } from "../../api/types";
import { useBuilderStore } from "../../store/useBuilderStore";
import { ConnectorConfigFields } from "./ConnectorConfigFields";
import { KindBadge } from "./SourceSuggestionCard";
import {
  JSON_LIST_OPTION,
  LOCAL_FILE_OPTION,
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

const EASE_OUT = [0.23, 1, 0.32, 1] as const;

export function SourceSuggestions({ suggestion, bbox, takenIds, onAdd, onDismiss }: Props) {
  const reduce = useReducedMotion();
  const uid = useId();
  const unranked = new Set(suggestion.options.map((o) => OPTION_KIND[o.type])).size > 1;
  // Ranked best first; the JSON list and local file options are always available, last unless the suggester ranked them.
  const all = useMemo(
    () => [
      ...suggestion.options,
      ...[JSON_LIST_OPTION, LOCAL_FILE_OPTION].filter((x) => !suggestion.options.some((o) => o.type === x.type)),
    ],
    [suggestion.options],
  );
  const first = all[0] ?? JSON_LIST_OPTION;
  const [chosen, setChosen] = useState<SourceOption>(first);
  const [draft, setDraft] = useState<ConnectorDraft>(() => connectorDraftFor(first, bbox, suggestion.meaning));
  const [shape, setShape] = useState<InputShape>(suggestion.shape);
  const [open, setOpen] = useState<"details" | "others" | null>(unranked ? "details" : null);

  const kind = OPTION_KIND[chosen.type];
  const problem = kind === "pull" ? connectorProblem(chosen, draft) : null;
  const others = all.filter((o) => optionKey(o) !== optionKey(chosen));

  const add = () => {
    if (problem) return;
    const input: CompileInput = {
      id: slugFromDescription(suggestion.meaning, takenIds),
      kind,
      shape,
      meaning: suggestion.meaning, // the dev's words, verbatim
      fields: [],
      connector: connectorOf(chosen, draft),
      ...(chosen.type === "webhook" ? { webhook: emptyWebhook() } : {}),
    };
    // a sample fetched or pasted while setting up a JSON list follows the source to its row
    if (chosen.type === "json_list" && draft.sample?.trim()) useBuilderStore.getState().setWebhookSample(input.id, draft.sample);
    onAdd(input);
  };

  const choose = (o: SourceOption) => {
    setChosen(o);
    setDraft(connectorDraftFor(o, bbox, suggestion.meaning));
    setOpen(null);
  };

  const toggle = (which: "details" | "others") => setOpen((cur) => (cur === which ? null : which));
  const panel = {
    initial: reduce ? { opacity: 0 } : { opacity: 0, y: -4 },
    animate: { opacity: 1, y: 0, transition: { duration: reduce ? 0 : 0.2, ease: EASE_OUT } },
    exit: { opacity: 0, transition: { duration: reduce ? 0 : 0.12 } },
  };

  return (
    <motion.section
      initial={reduce ? { opacity: 0 } : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0, transition: { duration: reduce ? 0 : 0.22, ease: EASE_OUT } }}
      exit={{ opacity: 0, transition: { duration: reduce ? 0 : 0.14 } }}
      className="bld-card flex flex-col gap-3.5 px-4 py-4 sm:px-5"
      aria-label="Suggested way to connect it"
    >
      <div className="flex min-w-0 items-center gap-2.5">
        <KindBadge kind={kind} />
        <span className="min-w-0 flex-1 truncate text-[15px] font-bold text-slate" title={chosen.label}>
          {chosen.label}
        </span>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss suggestion"
          className="tap hit relative flex shrink-0 items-center justify-center rounded-full p-1.5 text-ink transition-colors duration-150 hover:bg-white/60 hover:text-slate"
        >
          <X size={16} aria-hidden />
        </button>
      </div>

      {unranked && <p className="bld-hint">We couldn't rank these. Check what each item is, and look through Other ways.</p>}

      {kind === "pull" && (
        <ConnectorConfigFields key={optionKey(chosen)} option={chosen} draft={draft} onChange={setDraft} part="main" />
      )}

      <div className="flex flex-wrap items-center gap-x-2 gap-y-2">
        <button type="button" disabled={!!problem} onClick={add} className="bld-btn bld-btn-primary tap">
          <Plus size={15} aria-hidden /> Add
        </button>
        {problem && <span className="bld-hint">{problem}</span>}
        <span className="ml-auto flex items-center gap-1">
          <Disclosure label="Details" open={open === "details"} controls={`${uid}-details`} onClick={() => toggle("details")} />
          {others.length > 0 && (
            <Disclosure label="Other ways" open={open === "others"} controls={`${uid}-others`} onClick={() => toggle("others")} />
          )}
        </span>
      </div>

      <AnimatePresence initial={false} mode="wait">
        {open === "details" && (
          <motion.div key="details" id={`${uid}-details`} {...panel} className="bld-card-quiet flex flex-col gap-3 px-4 py-3">
            <p className="bld-hint">{chosen.why}</p>
            <label className="flex flex-col gap-1.5">
              <span className="bld-label">What each item is</span>
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
              <span className="bld-hint">{SHAPE_INFO[shape].hint}.</span>
            </label>
            {kind === "pull" && (
              <ConnectorConfigFields key={optionKey(chosen)} option={chosen} draft={draft} onChange={setDraft} part="more" />
            )}
          </motion.div>
        )}
        {open === "others" && (
          <motion.ul key="others" id={`${uid}-others`} {...panel} className="flex flex-col gap-1" aria-label="Other ways to connect it">
            {others.map((o) => (
              <li key={optionKey(o)}>
                <button
                  type="button"
                  onClick={() => choose(o)}
                  title={o.why}
                  className="bld-press flex w-full min-w-0 items-center gap-2.5 rounded-xl px-3 py-2 text-left text-[14px] text-slate transition-colors duration-150 hover:bg-white/60"
                >
                  <span className="min-w-0 flex-1 truncate">{o.label}</span>
                  <KindBadge kind={OPTION_KIND[o.type]} small />
                </button>
              </li>
            ))}
          </motion.ul>
        )}
      </AnimatePresence>
    </motion.section>
  );
}

function Disclosure({ label, open, controls, onClick }: { label: string; open: boolean; controls: string; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-expanded={open}
      aria-controls={controls}
      onClick={onClick}
      className="bld-btn bld-btn-quiet bld-btn-sm gap-1 font-semibold"
    >
      {label}
      <ChevronDown size={13} aria-hidden className={`bld-chevron ${open ? "is-open" : ""}`} />
    </button>
  );
}
