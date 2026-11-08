// One added source on the Sources step: who sends it, its editable id, what each item is, the dev's own
// description, optional extra fields ("level:number, note:string?"), for a push source an optional mapping
// for another service's webhook JSON, for a JSON list we fetch its item mapping, and remove (undo lives in
// the toast).
import { Pencil, X } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { forwardRef, useEffect, useRef, useState } from "react";
import type { CompileInput, ConnectorConfig, InputField, WebhookMapping } from "../../api/types";
import { KindBadge } from "../sources/SourceSuggestionCard";
import { WebhookEditor } from "../sources/WebhookEditor";
import { POLL_CHOICES, SHAPE_INFO, SLUG, emptyWebhook, fieldsToText, listConfigOf, listMappingOf, parseFields } from "../sources/sourceUtils";
import { useHelp } from "./Help";

interface Props {
  source: CompileInput;
  duplicate: boolean;
  onRename: (id: string) => void;
  onFields: (fields: InputField[]) => void;
  /** Push only: the webhook mapping, or null to receive plain events from the dev's own app. */
  onWebhook: (webhook: WebhookMapping | null) => void;
  /** Pull only: replace the connector (a JSON list's item mapping is edited here). */
  onConnector: (connector: ConnectorConfig) => void;
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
  { source, duplicate, onRename, onFields, onWebhook, onConnector, onFieldsProblem, onRemove },
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
  const fieldsHelp = useHelp(
    "extra fields",
    <>
      Every item already has a place and a time. List anything else it carries, as <code>name:type</code>, separated by
      commas. Types: <code>number</code>, <code>integer</code> (whole number), <code>string</code> (text) and{" "}
      <code>boolean</code> (yes or no). Add <code>?</code> when a value may be missing. For example{" "}
      <code>level:number, note:string?</code>.
    </>,
  );
  const list = source.connector?.type === "json_list" ? source.connector : null;
  const every = (s: number | undefined) => POLL_CHOICES.find((c) => c.s === (s ?? 300))?.label ?? `every ${s} s`;

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
      : list
        ? `${source.connector.url ?? ""} · ${every(list.poll_s)}`
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
      className={`bld-card flex flex-col gap-3 px-4 py-4 sm:px-5 ${idBad ? "!bg-[#F6E6DD]/85" : ""}`}
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
            className="bld-field is-mono is-sm w-48"
          />
        ) : (
          <button
            type="button"
            onClick={() => setEditing(true)}
            title="Rename: this id appears in the source's address"
            className="hit group relative flex min-w-0 items-center gap-1 rounded-md px-1 font-mono text-[13px] font-semibold text-slate transition-colors duration-150 hover:bg-white/60"
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
          className="tap ml-auto flex items-center justify-center gap-1 rounded-full px-2 py-1 text-xs font-semibold text-ink transition-colors duration-150 hover:bg-white/60 hover:text-slate max-sm:-my-2 max-sm:-mr-2 max-sm:px-0"
        >
          <X size={13} aria-hidden /> <span className="max-sm:sr-only">Remove</span>
          <span className="sr-only"> {source.id}</span>
        </button>
      </div>
      {idBad && (
        <p className="text-xs text-danger">
          {duplicate ? "Another source already uses this id." : "Use lowercase letters, digits, - or _."}
        </p>
      )}
      <p className="text-[15px] leading-snug text-slate">“{source.meaning}”</p>
      {connector && <p className="truncate font-mono text-[11px] text-ink">{connector}</p>}

      <div className="flex flex-col gap-1.5">
        <div className="flex items-center gap-1.5">
          <label htmlFor={fieldsId} className="bld-label">
            Extra values per item <span className="bld-optional">optional</span>
          </label>
          {fieldsHelp.button}
        </div>
        {fieldsHelp.panel}
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
          className="bld-field is-mono w-full"
        />
        <p id={`${fieldsId}-hint`} className={`bld-hint ${parsed.problems.length ? "!text-danger" : ""}`}>
          {parsed.problems.length
            ? parsed.problems[0]
            : parsed.fields.length
              ? `Each item also carries: ${parsed.fields
                  .map((f) => `${f.name} (${TYPE_WORD[f.type]}${f.required === false ? ", optional" : ""})`)
                  .join(", ")}.`
              : "Anything each item carries besides its place and time. Leave empty if there is nothing else."}
        </p>
      </div>

      {source.kind === "push" && (
        <div className="flex flex-col gap-2">
          <label htmlFor={webhookId} className="flex w-fit cursor-pointer items-start gap-2 text-[13.5px] font-bold text-slate max-sm:min-h-11">
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
              className="mt-1 accent-mutedTeal"
            />
            <span>
              It comes from another service's webhook
              <span className="block text-[12.5px] font-normal text-ink">
                Tick this if the data arrives in their own JSON format; you then say where the place, time and values are.
              </span>
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

      {list && (
        <div className="flex flex-col gap-1.5">
          <span className="bld-label">What each item holds</span>
          <WebhookEditor
            sourceId={source.id}
            variant="list"
            mapping={listMappingOf(list)}
            fields={source.fields}
            onChange={(m) => onConnector({ ...list, config: listConfigOf(m) })}
            fetchUrl={list.url ?? ""}
          />
        </div>
      )}
    </motion.li>
  );
});
