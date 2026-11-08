// The editable proposal on the Review page: a draft of the AppSpec (useSpecDraft) and one section per
// concern (inputs & tiers, verification, visibility & labels, routing). Save goes through PUT /spec;
// the page owns Confirm & activate. Rule 4: the Compiler only proposed these values; they take effect
// when the dev confirms, and after that no agent can change tiers, locked weights or verification.
// Every setting carries plain words (components/builder/explain.ts) and, where it needs more, a "?" note.
import { Check, ChevronRight, Lock, Plus, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiError, api } from "../../api/client";
import type {
  AppSpec,
  InputSpec,
  OperatorConfig,
  Outputs,
  Plausibility,
  PoiLabel,
  PoiLabelConfig,
  PoiLabelMode,
  RoutingSpec,
  Tier,
  Visibility,
} from "../../api/types";
import { useHelp } from "../builder/Help";
import {
  ALPHA_DETAIL,
  HOTSPOT_DETAIL,
  IMPASSABLE_DETAIL,
  LABELS_DETAIL,
  LABEL_MODE_PLAIN,
  OPERATOR_DETAIL,
  PLAUSIBILITY_DETAIL,
  ROUTING_DETAIL,
  SHAPE_PLAIN,
  TIER_DETAIL,
  TIER_PLAIN,
  VERIFICATION_DETAIL,
  VERIFICATION_PLAIN,
  VISIBILITY_PLAIN,
  operatorPlain,
} from "../builder/explain";
import { WebhookSummary } from "../sources/WebhookSummary";
import { listMappingOf } from "../sources/sourceUtils";
import { TIER_SURFACE } from "./TierBadge";
import { TIER_HELP, TIERS, layerIdOf, operatorChoices, useOperatorCatalog } from "./specLayers";

const VISIBILITIES: Visibility[] = ["authority", "public"];
const LABEL_MODES: PoiLabelMode[] = ["auto", "on", "off"];
export const FALLBACK_TEXT_MAX = 360;
const numOrNull = (v: string) => (v.trim() === "" || !Number.isFinite(Number(v)) ? null : Number(v));
const numOrUndef = (v: string) => (v.trim() === "" || !Number.isFinite(Number(v)) ? undefined : Number(v));

/** Plausibility with empty values dropped; null when nothing is set (the platform's neutral defaults). */
function cleanPlausibility(p: Plausibility): Plausibility | null {
  const out: Plausibility = {};
  if (p.max_speed_mps != null) out.max_speed_mps = p.max_speed_mps;
  if (p.jitter_m != null) out.jitter_m = p.jitter_m;
  if (p.flag_constant_speed) out.flag_constant_speed = true;
  if (p.flag_straight_line) out.flag_straight_line = true;
  return Object.keys(out).length ? out : null;
}

/** Client-side mirror of validate_spec's safeguard warnings that matter at review time. */
function reviewWarnings(spec: AppSpec): string[] {
  const out: string[] = [];
  for (const i of spec.inputs ?? []) {
    if ((i.shape === "point" || i.shape === "track") && i.tier === "flexible" && !i.plausibility) {
      out.push(`${i.id}: no spoof-check limits (top speed, path shape), so only the basic checks run. Set them under More settings.`);
    }
  }
  // as validate_spec: only weighted layers count (an override applies after the sum); unlisted means private
  const layers = spec.outputs?.layers ?? [];
  const score = layers.find((l) => l.id === "score");
  const isPublic = (id: string) => layers.some((l) => l.id === id && l.visibility === "public");
  const weightedIds = (spec.inputs ?? []).flatMap((i) => (i.operators ?? []).filter((o) => o.op !== "override").map((o) => layerIdOf(i.id, o)));
  if (score?.visibility === "public" && weightedIds.some((id) => !isPublic(id))) {
    out.push("The combined score is public while some layers are private; the server will reject this.");
  }
  if ((spec.routing?.fallback_text ?? "").length > FALLBACK_TEXT_MAX) {
    out.push(`The no-route message is over ${FALLBACK_TEXT_MAX} characters.`);
  }
  return out;
}

// ---------- draft state ----------

const SAVED_MS = 3000;

export interface SpecDraft {
  draft: AppSpec;
  dirty: boolean;
  saving: boolean;
  warnings: string[];
  saveWarnings: string[] | null;
  saveErrors: string[];
  /** True for a few seconds after a save the server accepted with no warnings. */
  justSaved: boolean;
  change: (next: AppSpec) => void;
  discard: () => void;
  save: () => Promise<AppSpec | null>;
}

export function useSpecDraft(spec: AppSpec, appId: string, onSaved: (spec: AppSpec) => void): SpecDraft {
  const specJson = JSON.stringify(spec);
  const [draft, setDraft] = useState<AppSpec>(spec);
  const [saving, setSaving] = useState(false);
  const [saveWarnings, setSaveWarnings] = useState<string[] | null>(null);
  const [saveErrors, setSaveErrors] = useState<string[]>([]);
  const [justSaved, setJustSaved] = useState(false);

  useEffect(() => {
    if (!justSaved) return;
    const id = setTimeout(() => setJustSaved(false), SAVED_MS);
    return () => clearTimeout(id);
  }, [justSaved]);

  // a new spec from the parent (e.g. the server copy arriving) replaces the draft
  useEffect(() => {
    setDraft(JSON.parse(specJson) as AppSpec);
  }, [specJson]);

  const dirty = JSON.stringify(draft) !== specJson;
  const warnings = useMemo(() => reviewWarnings(draft), [draft]);

  return {
    draft,
    dirty,
    saving,
    warnings,
    saveWarnings,
    saveErrors,
    justSaved,
    change: (next) => {
      setDraft(next);
      setSaveErrors([]);
      setJustSaved(false);
    },
    discard: () => {
      setDraft(JSON.parse(specJson) as AppSpec);
      setSaveErrors([]);
    },
    save: async () => {
      setSaving(true);
      setSaveErrors([]);
      setJustSaved(false);
      try {
        const r = await api.updateSpec(appId, draft);
        setDraft(r.spec);
        setSaveWarnings(r.warnings);
        setJustSaved(r.warnings.length === 0);
        onSaved(r.spec);
        return r.spec;
      } catch (e) {
        setSaveWarnings(null);
        if (e instanceof ApiError && e.details.length > 0) setSaveErrors(e.details);
        else if (e instanceof ApiError && e.status === 409) setSaveErrors(["This app is no longer proposed: it was already activated."]);
        else setSaveErrors([e instanceof ApiError ? e.message : String(e)]);
        return null;
      } finally {
        setSaving(false);
      }
    },
  };
}

// ---------- shared bits ----------

const field = "bld-field is-sm";
const mono = `${field} is-mono`;
const smallBtn = "bld-btn bld-btn-secondary bld-btn-sm";
const label = "text-[12.5px] text-ink";

export function ReviewSection({
  id,
  title,
  aside,
  help,
  children,
}: {
  id: string;
  title: string;
  aside?: ReactNode;
  /** Longer plain-language note behind a "?" next to the title. */
  help?: ReactNode;
  children: ReactNode;
}) {
  const h = useHelp(title.toLowerCase(), help);
  return (
    <section aria-labelledby={id} className="space-y-3 border-t border-[rgba(99,143,145,0.25)] pt-6 first:border-t-0 first:pt-0">
      <div className="space-y-1">
        <div className="flex items-center gap-1.5">
          <h2 id={id} className="bld-h2">
            {title}
          </h2>
          {help != null && h.button}
        </div>
        {aside && <p className="bld-hint max-w-[70ch] text-[13px]">{aside}</p>}
      </div>
      {h.panel}
      {children}
    </section>
  );
}

/** Tier choice as three cards, each saying in plain words what it lets the AI do. */
function TierPicker({ inputId, value, onChange }: { inputId: string; value: Tier; onChange: (t: Tier) => void }) {
  return (
    <div role="radiogroup" aria-label={`${inputId}: how far the AI may change it`} className="grid gap-1.5 sm:grid-cols-3">
      {TIERS.map((t) => {
        const on = value === t;
        return (
          <button
            key={t}
            type="button"
            role="radio"
            aria-checked={on}
            title={TIER_HELP[t]}
            onClick={() => onChange(t)}
            className="bld-choice tap"
          >
            <span aria-hidden className="bld-choice-dot">
              {on && <Check className="h-3 w-3" strokeWidth={3} />}
            </span>
            <span className="flex min-w-0 flex-col gap-0.5">
              <span className="text-[13.5px] font-bold text-slate">{TIER_PLAIN[t].name}</span>
              <span className="text-[12px] leading-snug text-ink">{TIER_PLAIN[t].line}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

/** Public / private as two small cards. */
function VisibilityPicker({ name, value, onChange }: { name: string; value: Visibility; onChange: (v: Visibility) => void }) {
  return (
    <div role="radiogroup" aria-label={`${name}: who can see it`} className="grid gap-1.5 sm:grid-cols-2">
      {VISIBILITIES.map((v) => {
        const on = value === v;
        return (
          <button key={v} type="button" role="radio" aria-checked={on} onClick={() => onChange(v)} className="bld-choice tap">
            <span aria-hidden className="bld-choice-dot">
              {on && <Check className="h-3 w-3" strokeWidth={3} />}
            </span>
            <span className="flex min-w-0 flex-col gap-0.5">
              <span className="text-[13px] font-bold text-slate">{VISIBILITY_PLAIN[v].name}</span>
              <span className="text-[12px] leading-snug text-ink">{VISIBILITY_PLAIN[v].line}</span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

const hasPlausibility = (i: InputSpec) => i.shape === "point" || i.shape === "track";
const fmt = (n: number) => String(Math.round(n * 1000) / 1000);

/** An operator's params beyond op / weight / layer_id, as [name, value] pairs (noise floor first). */
function operatorParams(o: OperatorConfig): [string, string][] {
  const out: [string, string][] = [["noise floor", o.noise_floor == null ? "default" : fmt(o.noise_floor)]];
  for (const [k, v] of Object.entries(o)) {
    if (k === "op" || k === "weight" || k === "noise_floor" || k === "layer_id" || v == null) continue;
    const shown = typeof v === "number" ? fmt(v) : typeof v === "string" || typeof v === "boolean" ? String(v) : JSON.stringify(v);
    out.push([k.replace(/_/g, " "), shown]);
  }
  return out;
}

/** One line naming the values "More settings" holds, so closing it hides nothing silently. */
function advancedSummary(input: InputSpec): string {
  const parts = [VISIBILITY_PLAIN[input.visibility ?? "authority"].name.toLowerCase()];
  if (hasPlausibility(input)) {
    const p = input.plausibility;
    if (!p) parts.push("no spoof-check limits");
    else {
      parts.push(p.max_speed_mps != null ? `top speed ${fmt(p.max_speed_mps)} m/s` : "no speed limit");
      if (p.jitter_m != null) parts.push(`wobble ${fmt(p.jitter_m)} m`);
      const flags = [p.flag_constant_speed && "constant speed", p.flag_straight_line && "straight paths"].filter(Boolean);
      parts.push(flags.length ? `flags ${flags.join(", ")}` : "no shape flags");
    }
  }
  const ops = input.operators ?? [];
  if (ops.length > 0) {
    const tuned = ops.filter((o) => operatorParams(o).length > 1 || o.noise_floor != null).length;
    parts.push(tuned ? `technical settings on ${tuned} of ${ops.length} layer${ops.length === 1 ? "" : "s"}` : "technical settings at defaults");
  }
  return parts.join(" · ");
}

const escapeRe = (x: string) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** The review notes that name one input (local warnings, save warnings, save errors), shown on its card. */
function notesFor(input: InputSpec, index: number, d: SpecDraft): CardNotes {
  const byId = new RegExp(`(^|[^\\w-])${escapeRe(input.id)}([^\\w-]|$)`);
  const mentions = (x: string) => byId.test(x) || x.includes(`inputs[${index}]`) || x.includes(`inputs.${index}.`);
  return { warnings: [...d.warnings, ...(d.saveWarnings ?? [])].filter(mentions), errors: d.saveErrors.filter(mentions) };
}

interface CardNotes {
  warnings: string[];
  errors: string[];
}

/** How a source arrives, in plain words. */
function arrival(input: InputSpec): string {
  const c = input.connector;
  if (input.kind === "push") return input.webhook ? "Sent by another service's webhook" : "Sent by your app";
  if (!c) return "Fetched by the platform";
  const every = c.poll_s ? ` every ${c.poll_s >= 60 ? `${fmt(c.poll_s / 60)} min` : `${fmt(c.poll_s)} s`}` : "";
  if (c.preset) return `Fetched${every} with the ready-made ${c.preset} setup`;
  if (c.type === "rss") return `Fetched${every} from a news feed`;
  if (c.type === "json_list") return `Fetched${every} from a JSON list`;
  return `Fetched${every} from a JSON address`;
}

function InputCard({
  input,
  notes,
  onInput,
  onOperators,
}: {
  input: InputSpec;
  notes: CardNotes;
  onInput: (patch: Partial<InputSpec>) => void;
  onOperators: (ops: OperatorConfig[], renamed?: [string, string]) => void;
}) {
  const catalog = useOperatorCatalog();
  const ops = input.operators ?? [];
  const choices = operatorChoices(input.shape, catalog);
  const unused = choices.filter((n) => !ops.some((o) => o.op === n));
  const p = input.plausibility ?? {};
  const setP = (patch: Partial<Plausibility>) => onInput({ plausibility: cleanPlausibility({ ...p, ...patch }) });
  const locked = input.tier === "locked";
  const visibility = input.visibility ?? "authority";
  const tierHelp = useHelp("tiers", TIER_DETAIL);
  const opHelp = useHelp("map layers", OPERATOR_DETAIL);
  const spoofHelp = useHelp("spoof checks", PLAUSIBILITY_DETAIL);
  const opLabel = (c: string) => `${operatorPlain(c).name} (${c})`;

  return (
    <div className={`space-y-4 rounded-2xl border p-4 sm:p-5 ${TIER_SURFACE[input.tier]}`}>
      <div className="space-y-1">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className="break-all font-mono text-[14px] font-semibold text-slate">{input.id}</span>
          <span className="text-[12.5px] text-ink">
            {arrival(input)} · {SHAPE_PLAIN[input.shape]}
          </span>
        </div>
        {input.meaning && <p className="max-w-[70ch] text-[13.5px] italic text-ink">“{input.meaning}”</p>}
      </div>

      <div className="space-y-1.5">
        <div className="flex items-center gap-1.5">
          <span className="bld-label">How far the AI may change it</span>
          {tierHelp.button}
        </div>
        {tierHelp.panel}
        <TierPicker inputId={input.id} value={input.tier} onChange={(tier) => onInput({ tier })} />
      </div>

      <div className="space-y-2">
        <div className="flex items-center gap-1.5">
          <span className="bld-label">How it shows on the map</span>
          {opHelp.button}
          {locked && (
            <span className="ml-1 inline-flex items-center gap-1 text-[12px] text-ink">
              <Lock aria-hidden className="h-3 w-3" /> weights can't change
            </span>
          )}
        </div>
        {opHelp.panel}
        {ops.length === 0 && <p className="text-[12.5px] text-ink">No layers: this source won't affect the score.</p>}
        {ops.map((o, n) => (
          <div key={layerIdOf(input.id, o)} className="space-y-1 rounded-xl bg-white/45 px-3 py-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <select
                aria-label={`${input.id}: how it shows on the map`}
                value={o.op}
                onChange={(e) => {
                  const op = e.target.value;
                  const layer_id = `${input.id}.${op}`;
                  // a different operator takes its own default params; keep weight and noise floor
                  const next: OperatorConfig = { op, weight: o.weight, noise_floor: o.noise_floor ?? null, layer_id };
                  onOperators(ops.map((x, j) => (j === n ? next : x)), [layerIdOf(input.id, o), layer_id]);
                }}
                className={`${field} min-w-0 max-w-full flex-1 font-semibold sm:flex-none`}
              >
                {[o.op, ...choices.filter((c) => c !== o.op && !ops.some((x) => x.op === c))].map((c) => (
                  <option key={c} value={c}>
                    {opLabel(c)}
                  </option>
                ))}
              </select>
              {locked ? (
                <span
                  className="inline-flex shrink-0 items-center gap-1 px-1 text-[12.5px] text-slate"
                  title="Locked weights never change. Pick another tier first to edit it."
                >
                  <Lock aria-hidden className="h-3 w-3" />
                  <span className="text-ink">counts</span>
                  <span className="font-mono tabular-nums">{o.weight}</span>
                </span>
              ) : (
                <label className={`flex shrink-0 items-center gap-1.5 ${label}`}>
                  counts
                  <input
                    aria-label={`${input.id} ${o.op} weight`}
                    type="number"
                    step="0.05"
                    min={0}
                    value={o.weight}
                    onChange={(e) => onOperators(ops.map((x, j) => (j === n ? { ...x, weight: Number(e.target.value) } : x)))}
                    className={`${mono} w-20`}
                  />
                </label>
              )}
              <button
                type="button"
                aria-label={`remove ${layerIdOf(input.id, o)}`}
                title={`Remove ${layerIdOf(input.id, o)}`}
                onClick={() => onOperators(ops.filter((_, j) => j !== n))}
                className={`${smallBtn} tap ml-auto shrink-0 !px-2`}
              >
                <Trash2 aria-hidden className="h-3.5 w-3.5" />
              </button>
            </div>
            <p className="text-[12px] text-ink">{operatorPlain(o.op).line}</p>
          </div>
        ))}
        {unused.length > 0 && (
          <select
            aria-label={`${input.id} add a map layer`}
            value=""
            onChange={(e) => {
              const op = e.target.value;
              if (op) onOperators([...ops, { op, weight: 0.5, layer_id: `${input.id}.${op}` }]);
            }}
            className={`${field} w-fit max-w-full`}
          >
            <option value="">Add another way to show it…</option>
            {unused.map((c) => (
              <option key={c} value={c}>
                {opLabel(c)}: {operatorPlain(c).line}
              </option>
            ))}
          </select>
        )}
      </div>

      {input.connector && (
        <p className="text-[12.5px] text-ink">
          <span>Fetched from </span>
          <span className="font-mono text-slate">
            {input.connector.type}
            {input.connector.preset ? ` (${input.connector.preset})` : ""}
          </span>{" "}
          <span className="break-all font-mono text-[11.5px]">{input.connector.url ?? ""}</span>
        </p>
      )}

      {input.kind === "push" && input.webhook && <WebhookSummary w={input.webhook} />}
      {input.connector?.type === "json_list" && <WebhookSummary title="Reads each item of the JSON list" w={listMappingOf(input.connector)} />}

      {/* This input's notes stay on the card, open or closed. */}
      {notes.errors.length > 0 && (
        <ul className="bld-warn space-y-0.5 font-semibold">
          {notes.errors.map((x) => (
            <li key={x} className="[overflow-wrap:anywhere]">
              {x}
            </li>
          ))}
        </ul>
      )}
      {notes.warnings.length > 0 && (
        <ul className="bld-warn space-y-0.5">
          {notes.warnings.map((x) => (
            <li key={x}>{x}</li>
          ))}
        </ul>
      )}

      <details className="group rounded-xl bg-white/35 open:bg-white/50">
        <summary className="flex min-h-11 cursor-pointer list-none items-start gap-2 rounded-xl px-3 py-2.5 text-[12.5px] transition-colors duration-150 hover:bg-white/45 sm:min-h-0 [&::-webkit-details-marker]:hidden">
          <ChevronRight aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ink transition-transform duration-200 group-open:rotate-90" />
          <span className="min-w-0">
            <span className="font-bold text-slate">More settings</span>
            <span className="block text-ink [overflow-wrap:anywhere] group-open:hidden">{advancedSummary(input)}</span>
          </span>
        </summary>
        <div className="space-y-4 px-3 pb-3 pt-1">
          <div className="space-y-1.5">
            <span className="bld-label">Who can see this source's layers</span>
            <VisibilityPicker name={input.id} value={visibility} onChange={(v) => onInput({ visibility: v })} />
          </div>

          {hasPlausibility(input) && (
            <div className="space-y-1.5">
              <div className="flex items-center gap-1.5">
                <span className="bld-label">Spoof checks</span>
                {spoofHelp.button}
              </div>
              {spoofHelp.panel}
              <div className={`flex flex-wrap items-center gap-x-5 gap-y-2 ${label}`}>
                <label className="flex items-center gap-1.5">
                  top speed (m/s)
                  <input
                    aria-label={`${input.id} max speed`}
                    type="number"
                    min={0}
                    value={p.max_speed_mps ?? ""}
                    placeholder="no limit"
                    onChange={(e) => setP({ max_speed_mps: numOrNull(e.target.value) })}
                    className={`${mono} w-24`}
                  />
                </label>
                <label className="flex items-center gap-1.5">
                  position wobble allowed (m)
                  <input
                    aria-label={`${input.id} jitter`}
                    type="number"
                    min={0}
                    value={p.jitter_m ?? ""}
                    placeholder="none"
                    onChange={(e) => setP({ jitter_m: numOrNull(e.target.value) })}
                    className={`${mono} w-24`}
                  />
                </label>
                <label className="flex items-center gap-1.5 max-sm:min-h-11">
                  <input type="checkbox" className="accent-mutedTeal" checked={!!p.flag_constant_speed} onChange={(e) => setP({ flag_constant_speed: e.target.checked })} />
                  flag a perfectly constant speed
                </label>
                <label className="flex items-center gap-1.5 max-sm:min-h-11">
                  <input type="checkbox" className="accent-mutedTeal" checked={!!p.flag_straight_line} onChange={(e) => setP({ flag_straight_line: e.target.checked })} />
                  flag perfectly straight paths
                </label>
              </div>
            </div>
          )}

          {ops.length > 0 && (
            <div className="space-y-1">
              <span className="bld-label">Technical settings</span>
              <p className="text-[12px] text-ink">The Compiler set these for each layer; most apps never need to change them.</p>
              <ul className="space-y-1">
                {ops.map((o) => (
                  <li key={layerIdOf(input.id, o)} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 text-[12px] text-ink">
                    <span className="break-all font-mono text-slate">{layerIdOf(input.id, o)}</span>
                    {operatorParams(o).map(([k, v]) => (
                      <span key={k}>
                        {k} <span className="font-mono tabular-nums text-slate">{v}</span>
                      </span>
                    ))}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </details>
    </div>
  );
}

// ---------- sections ----------

export function InputsSection({ d }: { d: SpecDraft }) {
  const { draft, change } = d;
  const inputs = draft.inputs ?? [];
  const outputs = draft.outputs ?? {};
  const outLayers = outputs.layers ?? [];

  const setInput = (k: number, patch: Partial<InputSpec>) => change({ ...draft, inputs: inputs.map((x, j) => (j === k ? { ...x, ...patch } : x)) });

  /** Replace one input's operators, keeping outputs.layers pointing only at layers that still exist. */
  const setOperators = (k: number, ops: OperatorConfig[], renamed?: [string, string]) => {
    if (!inputs[k]) return;
    const nextInputs = inputs.map((x, j) => (j === k ? { ...x, operators: ops } : x));
    const known = new Set(["score", ...nextInputs.flatMap((x) => (x.operators ?? []).map((o) => layerIdOf(x.id, o)))]);
    const nextLayers = outLayers.map((l) => (renamed && l.id === renamed[0] ? { ...l, id: renamed[1] } : l)).filter((l) => known.has(l.id));
    change({ ...draft, inputs: nextInputs, outputs: { ...outputs, layers: nextLayers } });
  };

  return (
    <ReviewSection
      id="rv-inputs"
      title="Your sources"
      aside="For each source: how far the AI may change its weight, and how it shows on the map."
    >
      <div className="grid gap-4">
        {inputs.map((i, k) => (
          <InputCard key={i.id} input={i} notes={notesFor(i, k, d)} onInput={(patch) => setInput(k, patch)} onOperators={(ops, r) => setOperators(k, ops, r)} />
        ))}
      </div>
    </ReviewSection>
  );
}

export function VerificationSection({ d }: { d: SpecDraft }) {
  const verification = [...(d.draft.verification ?? [])].sort((a, b) => a.priority - b.priority);
  return (
    <ReviewSection
      id="rv-verification"
      title="How changes are checked"
      aside="Before the AI changes a weight, it must pass these checks on held-back data, in this order."
      help={VERIFICATION_DETAIL}
    >
      {verification.length === 0 ? (
        <p className="bld-warn">No checks were proposed, so the AI can never change a weight.</p>
      ) : (
        <ol className="space-y-2">
          {verification.map((v, n) => (
            <li key={v.method} className="flex items-start gap-3">
              <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-white/70 font-mono text-[12px] font-semibold tabular-nums text-slate">
                {v.priority}
              </span>
              <span className="min-w-0 space-y-0.5">
                <span className="flex flex-wrap items-baseline gap-x-2">
                  <span className="text-[14px] font-bold text-slate">{VERIFICATION_PLAIN[v.method]?.name ?? v.method}</span>
                  <span className="font-mono text-[11.5px] text-ink">{v.method}</span>
                  {n === 0 && <span className="text-[12px] font-semibold text-mutedTeal">decides first</span>}
                </span>
                <span className="block text-[12.5px] text-ink">{VERIFICATION_PLAIN[v.method]?.line}</span>
              </span>
            </li>
          ))}
        </ol>
      )}
    </ReviewSection>
  );
}

/** "panic.density" -> "panic · Clusters"; "score" -> "Combined score". */
function layerName(id: string): string {
  if (id === "score") return "Combined score";
  const dot = id.lastIndexOf(".");
  return dot > 0 ? `${id.slice(0, dot)} · ${operatorPlain(id.slice(dot + 1)).name}` : id;
}

export function VisibilityLabelsSection({ d }: { d: SpecDraft }) {
  const { draft, change } = d;
  const outputs = draft.outputs ?? {};
  const outLayers = outputs.layers ?? [];
  const labels = outputs.poi_labels ?? {};
  const labelSet = labels.label_set ?? [];
  const mode = labels.mode ?? "auto";
  const setOutputs = (patch: Partial<Outputs>) => change({ ...draft, outputs: { ...outputs, ...patch } });
  const setLabels = (patch: Partial<PoiLabelConfig>) => setOutputs({ poi_labels: { ...labels, ...patch } });
  const setLabel = (k: number, patch: Partial<PoiLabel>) => setLabels({ label_set: labelSet.map((x, j) => (j === k ? { ...x, ...patch } : x)) });
  const hotHelp = useHelp("hotspot threshold", HOTSPOT_DETAIL);
  const labelHelp = useHelp("place labels", LABELS_DETAIL);

  return (
    <ReviewSection
      id="rv-visibility"
      title="Who sees what, and place names"
      aside="Public items reach anyone with the app's link; private ones need your authority key. End users only ever get directions."
    >
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-2">
          <h3 className="bld-h3">Map layers</h3>
          {outLayers.length === 0 && <p className="text-[13px] text-ink">No map layers are shared.</p>}
          <div className="grid gap-2">
            {outLayers.map((l, n) => (
              <label key={l.id} className="flex items-center gap-2 text-[13px]">
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="font-semibold text-slate">{layerName(l.id)}</span>
                  <span className="break-all font-mono text-[11px] text-ink">{l.id}</span>
                </span>
                <select
                  aria-label={`${l.id} output visibility`}
                  value={l.visibility ?? "authority"}
                  onChange={(e) => setOutputs({ layers: outLayers.map((x, j) => (j === n ? { ...x, visibility: e.target.value as Visibility } : x)) })}
                  className={field}
                >
                  {VISIBILITIES.map((x) => (
                    <option key={x} value={x}>
                      {VISIBILITY_PLAIN[x].name}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </div>
          <p className="text-[12px] text-ink">The combined score can be public only when every layer in it is public.</p>
          {draft.routing?.enabled && (
            <p className="text-[12px] text-ink">
              End users only see high-risk areas on their map if <code className="font-mono">score</code> is public.
            </p>
          )}
          <div className="space-y-1.5 pt-2">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <label htmlFor="rv-hot" className="bld-label">
                Hotspot threshold
              </label>
              {hotHelp.button}
              <input
                id="rv-hot"
                aria-label="hotspot threshold"
                type="number"
                step="0.05"
                min={0.05}
                max={1}
                value={outputs.hotspot_threshold ?? ""}
                placeholder="0.5"
                onChange={(e) => setOutputs({ hotspot_threshold: numOrUndef(e.target.value) })}
                className={`${mono} w-20`}
              />
              <span className="text-[12px] text-ink">score from 0 to 1</span>
            </div>
            {hotHelp.panel}
          </div>
        </div>

        <div className="space-y-2">
          <div className="flex items-center gap-1.5">
            <h3 className="bld-h3">Place names</h3>
            {labelHelp.button}
          </div>
          {labelHelp.panel}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <select aria-label="label mode" value={mode} onChange={(e) => setLabels({ mode: e.target.value as PoiLabelMode })} className={field}>
              {LABEL_MODES.map((x) => (
                <option key={x} value={x}>
                  {LABEL_MODE_PLAIN[x].name}
                </option>
              ))}
            </select>
            <span className="text-[12px] text-ink">{LABEL_MODE_PLAIN[mode].line}</span>
          </div>
          <label className={`flex flex-wrap items-center gap-1.5 ${label}`}>
            name a place only when at least
            <input
              aria-label="label minimum confidence"
              type="number"
              step="0.05"
              min={0}
              max={1}
              value={labels.min_confidence ?? ""}
              placeholder="0.6"
              onChange={(e) => setLabels({ min_confidence: numOrUndef(e.target.value) })}
              className={`${mono} w-20`}
            />
            sure (0 to 1)
          </label>
          {labelSet.length === 0 && <p className="text-[13px] text-ink">No names yet.</p>}
          {labelSet.map((l, n) => (
            <div key={n} className={`flex flex-wrap items-center gap-x-3 gap-y-1 ${label}`}>
              <input
                aria-label="label"
                value={l.label}
                placeholder="e.g. gathering point"
                onChange={(e) => setLabel(n, { label: e.target.value })}
                className={`${field} min-w-0 flex-1 max-sm:basis-full`}
              />
              <label className="flex items-center gap-1 max-sm:min-h-11" title="Shown only with the authority key">
                <input
                  type="checkbox"
                  className="accent-mutedTeal"
                  checked={!!l.sensitive}
                  onChange={(e) => setLabel(n, { sensitive: e.target.checked, ...(e.target.checked ? { destination: false } : {}) })}
                />
                private
              </label>
              <label className="flex items-center gap-1 max-sm:min-h-11" title="A public routing target; never private">
                <input
                  type="checkbox"
                  className="accent-mutedTeal"
                  checked={!!l.destination}
                  onChange={(e) => setLabel(n, { destination: e.target.checked, ...(e.target.checked ? { sensitive: false } : {}) })}
                />
                route people here
              </label>
              <button type="button" aria-label={`remove label ${l.label}`} onClick={() => setLabels({ label_set: labelSet.filter((_, j) => j !== n) })} className={`${smallBtn} tap !px-2`}>
                <Trash2 aria-hidden className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
          <button type="button" onClick={() => setLabels({ label_set: [...labelSet, { label: "", sensitive: false, destination: false }] })} className={`${smallBtn} tap`}>
            <Plus aria-hidden className="h-3.5 w-3.5" /> Add a name
          </button>
          <p className="text-[12px] text-ink">A private name is seen only with your key. A place people are routed to is always public.</p>
        </div>
      </div>
    </ReviewSection>
  );
}

export function RoutingSection({ d }: { d: SpecDraft }) {
  const { draft, change } = d;
  const routing = draft.routing ?? {};
  const setRouting = (patch: Partial<RoutingSpec>) => change({ ...draft, routing: { ...routing, ...patch } });
  const dests = routing.destination_points ?? [];
  const fallback = routing.fallback_text ?? "";
  const over = fallback.length > FALLBACK_TEXT_MAX;
  const impHelp = useHelp("the no-go score", IMPASSABLE_DETAIL);
  const alphaHelp = useHelp("steering", ALPHA_DETAIL);

  return (
    <ReviewSection
      id="rv-routing"
      title="Directions for end users"
      aside="Public, short instructions toward a safe destination that steer around high scores."
      help={ROUTING_DETAIL}
    >
      <label className="flex items-center gap-2 text-[14px] font-semibold text-slate max-sm:min-h-11">
        <input type="checkbox" className="accent-mutedTeal" checked={!!routing.enabled} onChange={(e) => setRouting({ enabled: e.target.checked })} />
        Give end users directions
      </label>
      {routing.enabled && (
        <div className="space-y-4">
          <p className="text-[13px] text-ink">
            {dests.length === 0
              ? "No fixed destinations: routes go only to named places marked “route people here”."
              : `Fixed destinations: ${dests.map((p) => p.label).join(", ")}.`}
          </p>
          <div className="flex flex-wrap gap-x-6 gap-y-3">
            <div className="space-y-1.5">
              <div className={`flex items-center gap-1.5 ${label}`}>
                <label htmlFor="rv-imp">never route through a score of</label>
                <input
                  id="rv-imp"
                  aria-label="impassable score"
                  type="number"
                  step="0.05"
                  min={0}
                  max={1}
                  value={routing.impassable_score ?? ""}
                  placeholder="0.8"
                  onChange={(e) => setRouting({ impassable_score: numOrUndef(e.target.value) })}
                  className={`${mono} w-20`}
                />
                <span>or more</span>
                {impHelp.button}
              </div>
              {impHelp.panel}
            </div>
            <div className="space-y-1.5">
              <div className={`flex items-center gap-1.5 ${label}`}>
                <label htmlFor="rv-alpha">steer around warm areas</label>
                <input
                  id="rv-alpha"
                  aria-label="routing alpha"
                  type="number"
                  step="0.5"
                  min={0}
                  value={routing.alpha ?? ""}
                  placeholder="4"
                  onChange={(e) => setRouting({ alpha: numOrUndef(e.target.value) })}
                  className={`${mono} w-20`}
                />
                {alphaHelp.button}
              </div>
              {alphaHelp.panel}
            </div>
          </div>
          <label className="block space-y-1">
            <span className="bld-label">Message when no route exists</span>
            <textarea
              aria-label="routing fallback text"
              aria-invalid={over}
              rows={2}
              value={fallback}
              placeholder="e.g. No safe route right now. Stay where you are and check again shortly."
              onChange={(e) => setRouting({ fallback_text: e.target.value })}
              className={`${field} block w-full max-w-[70ch] resize-y`}
            />
            <span className={`block text-[12px] tabular-nums ${over ? "font-semibold text-danger" : "text-ink"}`}>
              {fallback.length} / {FALLBACK_TEXT_MAX}
            </span>
          </label>
        </div>
      )}
    </ReviewSection>
  );
}

/** Local warnings, the server's save warnings, and save errors. */
export function ReviewMessages({ d }: { d: SpecDraft }) {
  return (
    <>
      {d.warnings.length > 0 && (
        <div className="bld-warn p-3.5">
          <p className="font-bold">Check before confirming</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {d.warnings.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </div>
      )}
      {d.saveWarnings && d.saveWarnings.length > 0 && (
        <div role="status" className="bld-note p-3.5">
          <p className="font-bold">Saved, with notes</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {d.saveWarnings.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </div>
      )}
      {d.saveErrors.length > 0 && (
        <div role="alert" className="bld-warn p-3.5">
          <p className="font-bold">The server didn't accept these edits. Fix them, then save again.</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {d.saveErrors.map((x) => (
              <li key={x} className="[overflow-wrap:anywhere]">
                {x}
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}
