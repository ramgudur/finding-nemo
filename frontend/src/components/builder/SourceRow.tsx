// One added source on the Sources step: who sends it, its editable id, what each item is, the dev's own
// description, optional extra fields ("level:number, note:string?"), for a push source an optional mapping
// for another service's webhook JSON, and remove (undo lives in the toast).
import { Pencil, X } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { forwardRef, useEffect, useRef, useState } from "react";
import type { CompileInput, InputField, WebhookMapping } from "../../api/types";
import { KindBadge } from "../sources/SourceSuggestionCard";
import { WebhookEditor } from "../sources/WebhookEditor";
import { SHAPE_INFO, SLUG, emptyWebhook, fieldsToText, parseFields } from "../sources/sourceUtils";

interface Props {
  source: CompileInput;
  duplicate: boolean;
  onRename: (id: string) => void;
  onFields: (fields: InputField[]) => void;
  /** Push only: the webhook mapping, or null to receive plain events from the dev's own app. */
  onWebhook: (webhook: WebhookMapping | null) => void;
  /** The first problem in the extra-fields text, or null when it parses (the step blocks Next on it). */
  onFieldsProblem: (problem: string | null) => void;
  onRemove: () => void;
}

const TYPE_WORD: Record<InputField["type"], string> = {
  number: "number",
  integer: "whole number",
  string: "text",
  boolean: "yes / no",
};

export const SourceRow = forwardRef<HTMLLIElement, Props>(function SourceRow(
  { source, duplicate, onRename, onFields, onWebhook, onFieldsProblem, onRemove },
  ref,
) {
  const reduce = useReducedMotion();
  const [editing, setEditing] = useState(false);
  const [idText, setIdText] = useState(source.id);
  const [fieldsText, setFieldsText] = useState(fieldsToText(source.fields));
  const idInput = useRef<HTMLInputElement>(null);
  const idBad = !SLUG.test(source.id) || duplicate;
  const parsed = parseFields(fieldsText);
  const fieldsId = `fields-${source.id}`;
  const webhookId = `webhook-${source.id}`;
  // Keep what was typed when the dev switches the webhook off and back on.
  const [lastWebhook, setLastWebhook] = useState<WebhookMapping | null>(source.webhook ?? null);

  useEffect(() => {
    if (editing) idInput.current?.select();
  }, [editing]);
  useEffect(() => setIdText(source.id), [source.id]);

  const commitId = () => {
    setEditing(false);
    const v = idText.trim().toLowerCase();
    if (v && v !== source.id) onRename(v);
    else setIdText(source.id);
  };

  const connector = source.connector
    ? source.connector.preset
      ? `ready-made setup: ${source.connector.preset}`
      : source.connector.url ?? source.connector.type
    : source.kind === "pull"
      ? "no feed yet: the Compiler can ask the Scout to find one"
      : null;

  return (
    <motion.li
      ref={ref}
      layout={!reduce}
      initial={reduce ? { opacity: 0 } : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, transition: { duration: 0.15 } }}
      transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
      className={`flex flex-col gap-3 rounded-2xl border px-4 py-3.5 ${
        idBad ? "border-danger/40 bg-[#F6E6DD]/80" : "border-white/70 bg-cream/70"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <KindBadge kind={source.kind} small />
        {editing ? (
          <input
            ref={idInput}
            value={idText}
            aria-label="Source id"
            onChange={(e) => setIdText(e.target.value.toLowerCase().replace(/\s+/g, "_"))}
            onBlur={commitId}
            onKeyDown={(e) => {
              if (e.key === "Enter") commitId();
              if (e.key === "Escape") {
                setIdText(source.id);
                setEditing(false);
              }
            }}
            spellCheck={false}
            className="w-48 rounded-lg border border-white/80 bg-white/70 px-2 py-0.5 font-mono text-[13px] text-slate"
          />
        ) : (
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="group flex min-w-0 items-center gap-1 rounded-md px-1 font-mono text-[13px] font-semibold text-slate transition-colors duration-150 hover:bg-white/60"
          >
            <span className="truncate">{source.id}</span>
            <Pencil size={11} className="shrink-0 text-ink" aria-hidden />
            <span className="sr-only">edit source id</span>
          </button>
        )}
        <span className="text-xs text-ink">· {SHAPE_INFO[source.shape].label.toLowerCase()}</span>
        <button
          type="button"
          onClick={onRemove}
          className="ml-auto flex items-center gap-1 rounded-full px-2 py-1 text-xs font-semibold text-ink transition-colors duration-150 hover:bg-white/60 hover:text-slate"
        >
          <X size={13} aria-hidden /> Remove<span className="sr-only"> {source.id}</span>
        </button>
      </div>
      {idBad && (
        <p className="text-xs text-danger">
          {duplicate ? "Another source already uses this id." : "Use lowercase letters, digits, - or _."}
        </p>
      )}
      <p className="text-[14px] leading-snug text-slate">“{source.meaning}”</p>
      {connector && <p className="truncate font-mono text-[11px] text-ink">{connector}</p>}

      <div className="flex flex-col gap-1">
        <label htmlFor={fieldsId} className="text-xs font-semibold text-slate">
          Extra fields <span className="font-normal text-ink">(optional)</span>
        </label>
        <input
          id={fieldsId}
          value={fieldsText}
          onChange={(e) => {
            setFieldsText(e.target.value);
            const next = parseFields(e.target.value);
            onFieldsProblem(next.problems[0] ?? null);
            if (next.problems.length === 0) onFields(next.fields);
          }}
          placeholder="e.g. level:number, note:string?"
          spellCheck={false}
          aria-describedby={`${fieldsId}-hint`}
          aria-invalid={parsed.problems.length > 0}
          className={`rounded-xl border bg-white/55 px-3 py-1.5 font-mono text-[13px] text-slate placeholder:text-inkSoft focus:bg-white/85 ${
            parsed.problems.length ? "border-danger/50" : "border-white/70"
          }`}
        />
        <p id={`${fieldsId}-hint`} className={`text-[11px] ${parsed.problems.length ? "text-danger" : "text-ink"}`}>
          {parsed.problems.length
            ? parsed.problems[0]
            : parsed.fields.length
              ? `Each item also carries: ${parsed.fields
                  .map((f) => `${f.name} (${TYPE_WORD[f.type]}${f.required === false ? ", optional" : ""})`)
                  .join(", ")}.`
              : "Values each item carries beyond its place and time. Write name:type; add ? for optional. Types: number, integer, string, boolean."}
        </p>
      </div>

      {source.kind === "push" && (
        <div className="flex flex-col gap-2">
          <label htmlFor={webhookId} className="flex w-fit cursor-pointer items-center gap-2 text-xs font-semibold text-slate">
            <input
              id={webhookId}
              type="checkbox"
              checked={!!source.webhook}
              onChange={(e) => {
                if (e.target.checked) onWebhook(lastWebhook ?? emptyWebhook());
                else {
                  setLastWebhook(source.webhook ?? null);
                  onWebhook(null);
                }
              }}
              className="accent-mutedTeal"
            />
            <span>
              Receives another service's webhook{" "}
              <span className="font-normal text-ink">(their JSON, mapped to place, time and fields)</span>
            </span>
          </label>
          {source.webhook && (
            <WebhookEditor
              sourceId={source.id}
              mapping={source.webhook}
              fields={source.fields}
              onChange={(m) => {
                setLastWebhook(m);
                onWebhook(m);
              }}
            />
          )}
        </div>
      )}
    </motion.li>
  );
});
