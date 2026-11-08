// One added source on the Sources step, as a compact row: its id (click to rename), who moves the data, the
// dev's own description on one line, Options, and remove (undo lives in the toast). Options holds the rest:
// what each item is, where it's fetched from, extra values per item ("level:number, note:string?"), for a push
// source an optional mapping for another service's webhook JSON, and for a JSON list its item mapping. When
// something in Options blocks Next, the button carries a dot, and a row added in that state opens it.
import { ChevronDown, Pencil, X } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { forwardRef, useEffect, useId, useRef, useState } from "react";
import type { CompileInput, ConnectorConfig, InputField, WebhookMapping } from "../../api/types";
import { KindBadge } from "../sources/SourceSuggestionCard";
import { WebhookEditor } from "../sources/WebhookEditor";
import {
  LOCAL_FILE_NAME,
  POLL_CHOICES,
  SHAPE_INFO,
  SLUG,
  emptyWebhook,
  fieldsToText,
  listConfigOf,
  listMappingOf,
  localFileLabel,
  localFileOf,
  parseFields,
  webhookProblems,
} from "../sources/sourceUtils";
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

const EASE_OUT = [0.23, 1, 0.32, 1] as const;
/** The one Options problem with no inline control to show it (the address is set when the source is added). */
const NEEDS_URL = "This source needs its web address.";

/** What in this row's Options blocks Next (the same rules as sourcesProblems), or null. */
function optionsProblem(source: CompileInput, fieldsProblem: string | null): string | null {
  if (fieldsProblem) return fieldsProblem;
  const c = source.connector;
  if (c?.type === "json_api" && !c.preset && !c.url) return NEEDS_URL;
  if (c?.type === "local_file" && !LOCAL_FILE_NAME.test(localFileOf(c))) return "This source needs a bare file name ending in .txt.";
  if (c?.type === "json_list") {
    if (!c.url) return NEEDS_URL;
    const p = webhookProblems(listMappingOf(c), source.fields)[0];
    if (p) return `Item mapping: ${p}.`;
  }
  if (source.kind === "push" && source.webhook) {
    const p = webhookProblems(source.webhook, source.fields)[0];
    if (p) return `Webhook: ${p}.`;
  }
  return null;
}

export const SourceRow = forwardRef<HTMLLIElement, Props>(function SourceRow(
  { source, duplicate, onRename, onFields, onWebhook, onConnector, onFieldsProblem, onRemove },
  ref,
) {
  const reduce = useReducedMotion();
  const uid = useId();
  const [editing, setEditing] = useState(false);
  const [idText, setIdText] = useState(source.id);
  const [fieldsText, setFieldsText] = useState(fieldsToText(source.fields));
  const idInput = useRef<HTMLInputElement>(null);
  const idBad = !SLUG.test(source.id) || duplicate;
  const parsed = parseFields(fieldsText);
  const attention = optionsProblem(source, parsed.problems[0] ?? null);
  // A row that arrives needing something (a webhook option's empty mapping) opens straight to it.
  const [open, setOpen] = useState(attention !== null);
  const fieldsId = `fields-${uid}`;
  const webhookId = `webhook-${uid}`;
  const panelId = `options-${uid}`;
  // Keep what was typed when the dev switches the webhook off and back on.
  const [lastWebhook, setLastWebhook] = useState<WebhookMapping | null>(source.webhook ?? null);
  const fieldsHelp = useHelp(
    "extra values",
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
      ? `Ready-made setup: ${source.connector.preset}`
      : list
        ? `${source.connector.url ?? ""} · ${every(list.poll_s)}`
        : source.connector.type === "local_file"
          ? `${localFileLabel(source.connector)} · backend/data/feeds/${localFileOf(source.connector)}`
        : (source.connector.url ?? null)
    : source.kind === "pull"
      ? "No feed yet; we'll look for one when you compile."
      : null;

  return (
    <motion.li
      ref={ref}
      layout={reduce ? false : "position"}
      initial={reduce ? { opacity: 0 } : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, transition: { duration: 0.15 } }}
      transition={{ duration: reduce ? 0 : 0.22, ease: EASE_OUT }}
      className={`flex flex-col gap-2 px-4 py-3.5 sm:px-5 ${
        idBad ? "bg-danger/[0.07]" : attention ? "bg-gradient-to-r from-attnBg/75 to-attnBg/25" : ""
      }`}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1 sm:flex-nowrap">
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
            className="bld-field is-mono is-sm w-44 shrink-0"
          />
        ) : (
          <button
            type="button"
            onClick={() => setEditing(true)}
            title="Rename: this id appears in the source's address"
            className="hit wp-press group relative flex min-w-0 max-w-[14rem] shrink-0 items-center gap-1.5 rounded-md px-1 font-mono text-[14.5px] font-semibold text-slate hover:bg-slate/[0.06]"
          >
            <span className="truncate">{source.id}</span>
            <Pencil size={11} className="shrink-0 text-ink opacity-60 transition-opacity duration-150 group-hover:opacity-100" aria-hidden />
            <span className="sr-only">rename source</span>
          </button>
        )}
        <KindBadge kind={source.kind} small />
        <p
          className="order-last min-w-0 basis-full truncate text-[14px] text-ink sm:order-none sm:basis-auto sm:flex-1"
          title={source.meaning}
        >
          {source.meaning}
        </p>
        <span className="ml-auto flex shrink-0 items-center gap-0.5 sm:ml-0">
          <button
            type="button"
            aria-expanded={open}
            aria-controls={panelId}
            onClick={() => setOpen((o) => !o)}
            title={attention ?? undefined}
            className={`bld-btn bld-btn-sm gap-1.5 font-semibold ${attention ? "bld-btn-secondary" : "bld-btn-quiet"}`}
          >
            {attention && <span className="wp-dot is-attn !h-1.5 !w-1.5" aria-hidden />}
            Options
            {attention && <span className="sr-only"> (needs attention)</span>}
            <ChevronDown size={13} aria-hidden className={`bld-chevron ${open ? "is-open" : ""}`} />
          </button>
          <button
            type="button"
            onClick={onRemove}
            title="Remove"
            className="bld-press hit relative flex items-center justify-center rounded-full p-1.5 text-ink hover:bg-slate/[0.07] hover:text-slate"
          >
            <X size={14} aria-hidden />
            <span className="sr-only">Remove {source.id}</span>
          </button>
        </span>
      </div>
      {idBad && (
        <p className="text-xs text-danger">
          {duplicate ? "Another source already uses this id." : "Use lowercase letters, digits, - or _."}
        </p>
      )}

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            id={panelId}
            initial={reduce ? { opacity: 0 } : { opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0, transition: { duration: reduce ? 0 : 0.2, ease: EASE_OUT } }}
            exit={{ opacity: 0, transition: { duration: reduce ? 0 : 0.12 } }}
            className="flex flex-col gap-4 border-t border-[rgba(99,143,145,0.22)] pt-3"
          >
            {attention === NEEDS_URL && <p className="bld-warn">{attention}</p>}
            <p className="flex flex-col gap-0.5 text-[13px] text-ink">
              <span>
                Each item: <span className="font-semibold text-slate">{SHAPE_INFO[source.shape].label.toLowerCase()}</span>
              </span>
              {connector && (
                <span className="truncate font-mono text-[11.5px]" title={connector}>
                  {connector}
                </span>
              )}
            </p>

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
                aria-describedby={parsed.problems.length || parsed.fields.length ? `${fieldsId}-hint` : undefined}
                aria-invalid={parsed.problems.length > 0}
                className="bld-field is-mono w-full"
              />
              {(parsed.problems.length > 0 || parsed.fields.length > 0) && (
                <p id={`${fieldsId}-hint`} className={`bld-hint ${parsed.problems.length ? "!text-danger" : ""}`}>
                  {parsed.problems.length
                    ? parsed.problems[0]
                    : `Each item also carries: ${parsed.fields
                        .map((f) => `${f.name} (${TYPE_WORD[f.type]}${f.required === false ? ", optional" : ""})`)
                        .join(", ")}.`}
                </p>
              )}
            </div>

            {source.kind === "push" && (
              <div className="flex flex-col gap-2">
                <label
                  htmlFor={webhookId}
                  className="flex w-fit cursor-pointer items-center gap-2 text-[13.5px] font-semibold text-slate max-sm:min-h-11"
                >
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
                  Arrives as another service's webhook
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
          </motion.div>
        )}
      </AnimatePresence>
    </motion.li>
  );
});
