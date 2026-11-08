// The editable proposal on the Review page: a draft of the AppSpec (useSpecDraft) and one section per
// concern (inputs & tiers, verification, visibility & labels, routing). Save goes through PUT /spec;
// the page owns Confirm & activate. Rule 4: the Compiler only proposed these values; they take effect
// when the dev confirms, and after that no agent can change tiers, locked weights or verification.
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
import { WebhookSummary } from "../sources/WebhookSummary";
import { TIER_SURFACE, TierBadge } from "./TierBadge";
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
      out.push(`${i.id}: no plausibility limits, so spoof checks use neutral defaults (no speed limit).`);
    }
  }
  const layers = spec.outputs?.layers ?? [];
  const score = layers.find((l) => l.id === "score");
  if (score?.visibility === "public" && layers.some((l) => l.id !== "score" && l.visibility !== "public")) {
    out.push("score is public while some layers are private; the server will reject this.");
  }
  if ((spec.routing?.fallback_text ?? "").length > FALLBACK_TEXT_MAX) {
    out.push(`Routing fallback text is over ${FALLBACK_TEXT_MAX} characters.`);
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

const field =
  "phone-16 rounded-lg border border-paleWater bg-white/70 px-2 py-1 text-[13px] text-slate max-sm:min-h-11";
const mono = `${field} font-mono`;
const smallBtn =
  "inline-flex items-center gap-1 rounded-full bg-white/60 px-2.5 py-1 text-[12px] font-semibold text-ink ring-1 ring-paleWater transition-[background-color,box-shadow] duration-150 hover:bg-white hover:shadow-sm disabled:opacity-50";
const label = "text-[12px] text-ink";

export function ReviewSection({ id, title, aside, children }: { id: string; title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="space-y-3 border-t border-paleWater/80 pt-6 first:border-t-0 first:pt-0">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 id={id} className="text-[17px] font-bold text-slate">
          {title}
        </h2>
        {aside && <span className="text-[13px] text-ink">{aside}</span>}
      </div>
      {children}
    </section>
  );
}

/** Tier choice: the selected tier carries a check and a ring, not just more opacity. */
function TierPicker({ inputId, value, onChange }: { inputId: string; value: Tier; onChange: (t: Tier) => void }) {
  return (
    <div role="radiogroup" aria-label={`${inputId} tier`} className="flex flex-wrap gap-1.5">
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
            className={`tap inline-flex items-center gap-1 rounded-full p-0.5 pr-1 transition-[box-shadow,background-color] duration-150 max-sm:px-1.5 ${
              on ? "bg-white/80 ring-2 ring-slate/70" : "ring-1 ring-transparent hover:bg-white/50 hover:ring-paleWater"
            }`}
          >
            <span aria-hidden className={`flex h-4 w-4 items-center justify-center rounded-full ${on ? "bg-slate text-cream" : "border border-inkSoft/60"}`}>
              {on && <Check className="h-3 w-3" strokeWidth={3} />}
            </span>
            <TierBadge tier={t} />
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

/** One line naming the values the Advanced disclosure holds, so closing it hides nothing silently. */
function advancedSummary(input: InputSpec): string {
  const parts = [`visibility ${input.visibility ?? "authority"}`];
  if (hasPlausibility(input)) {
    const p = input.plausibility;
    if (!p) parts.push("plausibility: neutral defaults");
    else {
      parts.push(p.max_speed_mps != null ? `max ${fmt(p.max_speed_mps)} m/s` : "no speed limit");
      if (p.jitter_m != null) parts.push(`jitter ${fmt(p.jitter_m)} m`);
      const flags = [p.flag_constant_speed && "constant speed", p.flag_straight_line && "straight lines"].filter(Boolean);
      parts.push(flags.length ? `flags ${flags.join(", ")}` : "no shape flags");
    }
  }
  const ops = input.operators ?? [];
  if (ops.length > 0) {
    const tuned = ops.filter((o) => operatorParams(o).length > 1 || o.noise_floor != null).length;
    parts.push(tuned ? `params set on ${tuned} of ${ops.length} operator${ops.length === 1 ? "" : "s"}` : "operator params at defaults");
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

  return (
    <div className={`space-y-3 rounded-2xl border p-4 ${TIER_SURFACE[input.tier]}`}>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="break-all font-mono text-[14px] font-medium text-slate">{input.id}</span>
        <span className="text-[12px] text-ink">
          {input.kind} · {input.shape}
        </span>
      </div>
      {input.meaning && <p className="max-w-[70ch] text-[13px] italic text-ink">“{input.meaning}”</p>}
      <TierPicker inputId={input.id} value={input.tier} onChange={(tier) => onInput({ tier })} />

      <div className="space-y-1.5">
        <span className="flex items-center gap-2 text-[12px] font-semibold text-slate">
          Operators
          {locked && (
            <span className="inline-flex items-center gap-1 font-normal text-ink">
              <Lock aria-hidden className="h-3 w-3" /> weights can't change
            </span>
          )}
        </span>
        {ops.length === 0 && <p className="text-[12.5px] text-ink">None: this input won't shape the score.</p>}
        {ops.map((o, n) => (
          <div key={layerIdOf(input.id, o)} className="flex items-center gap-1.5">
            <select
              aria-label={`${input.id} operator`}
              value={o.op}
              onChange={(e) => {
                const op = e.target.value;
                const layer_id = `${input.id}.${op}`;
                // a different operator takes its own default params; keep weight and noise floor
                const next: OperatorConfig = { op, weight: o.weight, noise_floor: o.noise_floor ?? null, layer_id };
                onOperators(ops.map((x, j) => (j === n ? next : x)), [layerIdOf(input.id, o), layer_id]);
              }}
              className={`${mono} min-w-0`}
            >
              {[o.op, ...choices.filter((c) => c !== o.op && !ops.some((x) => x.op === c))].map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
            {locked ? (
              <span
                className="inline-flex shrink-0 items-center gap-1 px-1 text-[12.5px] text-slate"
                title="Locked weights never change. Pick another tier first to edit it."
              >
                <Lock aria-hidden className="h-3 w-3" />
                <span className="text-ink">weight</span>
                <span className="font-mono tabular-nums">{o.weight}</span>
              </span>
            ) : (
              <label className={`flex shrink-0 items-center gap-1 ${label}`}>
                weight
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
              className={`${smallBtn} tap ml-auto shrink-0 justify-center`}
            >
              <Trash2 aria-hidden className="h-3 w-3" />
            </button>
          </div>
        ))}
        {unused.length > 0 && (
          <select
            aria-label={`${input.id} add operator`}
            value=""
            onChange={(e) => {
              const op = e.target.value;
              if (op) onOperators([...ops, { op, weight: 0.5, layer_id: `${input.id}.${op}` }]);
            }}
            className={field}
          >
            <option value="">Add an operator…</option>
            {unused.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        )}
      </div>

      {input.connector && (
        <p className="text-[12.5px] text-ink">
          <span>polls </span>
          <span className="font-mono text-slate">
            {input.connector.type}
            {input.connector.preset ? ` (${input.connector.preset})` : ""}
          </span>{" "}
          <span className="break-all font-mono text-[11.5px]">{input.connector.url ?? ""}</span>
        </p>
      )}

      {input.kind === "push" && input.webhook && <WebhookSummary w={input.webhook} />}

      {/* This input's notes stay on the card, open or closed. */}
      {notes.errors.length > 0 && (
        <ul className="space-y-0.5 rounded-xl bg-coral/20 px-3 py-2 text-[12.5px] font-semibold text-[#7A4232]">
          {notes.errors.map((x) => (
            <li key={x} className="[overflow-wrap:anywhere]">
              {x}
            </li>
          ))}
        </ul>
      )}
      {notes.warnings.length > 0 && (
        <ul className="space-y-0.5 rounded-xl bg-coral/15 px-3 py-2 text-[12.5px] text-[#7A4232]">
          {notes.warnings.map((x) => (
            <li key={x}>{x}</li>
          ))}
        </ul>
      )}

      <details className="group rounded-xl bg-white/35 open:bg-white/45">
        <summary className="flex min-h-11 cursor-pointer list-none items-start gap-2 rounded-xl px-3 py-2.5 text-[12.5px] transition-colors duration-150 hover:bg-white/40 sm:min-h-0 [&::-webkit-details-marker]:hidden">
          <ChevronRight aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ink transition-transform duration-200 group-open:rotate-90" />
          <span className="min-w-0">
            <span className="font-semibold text-slate">Advanced</span>
            <span className="block text-ink [overflow-wrap:anywhere] group-open:hidden">{advancedSummary(input)}</span>
          </span>
        </summary>
        <div className="space-y-3 px-3 pb-3 pt-1">
          <label className={`flex flex-wrap items-center gap-1.5 ${label}`}>
            visibility
            <select
              aria-label={`${input.id} visibility`}
              value={visibility}
              onChange={(e) => onInput({ visibility: e.target.value as Visibility })}
              className={field}
            >
              {VISIBILITIES.map((x) => (
                <option key={x}>{x}</option>
              ))}
            </select>
          </label>

          {hasPlausibility(input) && (
            <div className="space-y-1.5">
              <span className="text-[12px] font-semibold text-slate">Plausibility</span>
              <div className={`flex flex-wrap items-center gap-x-4 gap-y-1.5 ${label}`}>
                <label className="flex items-center gap-1">
                  max speed (m/s)
                  <input
                    aria-label={`${input.id} max speed`}
                    type="number"
                    min={0}
                    value={p.max_speed_mps ?? ""}
                    placeholder="none"
                    onChange={(e) => setP({ max_speed_mps: numOrNull(e.target.value) })}
                    className={`${mono} w-20`}
                  />
                </label>
                <label className="flex items-center gap-1">
                  jitter (m)
                  <input
                    aria-label={`${input.id} jitter`}
                    type="number"
                    min={0}
                    value={p.jitter_m ?? ""}
                    placeholder="none"
                    onChange={(e) => setP({ jitter_m: numOrNull(e.target.value) })}
                    className={`${mono} w-20`}
                  />
                </label>
                <label className="flex items-center gap-1 max-sm:min-h-11">
                  <input type="checkbox" className="accent-mutedTeal" checked={!!p.flag_constant_speed} onChange={(e) => setP({ flag_constant_speed: e.target.checked })} />
                  flag constant speed
                </label>
                <label className="flex items-center gap-1 max-sm:min-h-11">
                  <input type="checkbox" className="accent-mutedTeal" checked={!!p.flag_straight_line} onChange={(e) => setP({ flag_straight_line: e.target.checked })} />
                  flag straight lines
                </label>
              </div>
            </div>
          )}

          {ops.length > 0 && (
            <div className="space-y-1">
              <span className="text-[12px] font-semibold text-slate">Operator params</span>
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
    <ReviewSection id="rv-inputs" title="Inputs & tiers" aside="Tiers decide how far agents may move each weight.">
      <div className="grid gap-3 xl:grid-cols-2">
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
    <ReviewSection id="rv-verification" title="Verification" aside="In priority order. The first method with data decides every backtest.">
      {verification.length === 0 ? (
        <p className="text-[13px] text-ink">None proposed, so the Critic can never change a weight.</p>
      ) : (
        <ol className="space-y-1.5">
          {verification.map((v, n) => (
            <li key={v.method} className="flex items-baseline gap-3 text-[13.5px]">
              <span className="w-5 text-right font-mono text-[12px] tabular-nums text-ink">{v.priority}</span>
              <span className="font-mono text-slate">{v.method}</span>
              {n === 0 && <span className="text-[12px] text-ink">decides first</span>}
            </li>
          ))}
        </ol>
      )}
    </ReviewSection>
  );
}

export function VisibilityLabelsSection({ d }: { d: SpecDraft }) {
  const { draft, change } = d;
  const outputs = draft.outputs ?? {};
  const outLayers = outputs.layers ?? [];
  const labels = outputs.poi_labels ?? {};
  const labelSet = labels.label_set ?? [];
  const setOutputs = (patch: Partial<Outputs>) => change({ ...draft, outputs: { ...outputs, ...patch } });
  const setLabels = (patch: Partial<PoiLabelConfig>) => setOutputs({ poi_labels: { ...labels, ...patch } });
  const setLabel = (k: number, patch: Partial<PoiLabel>) => setLabels({ label_set: labelSet.map((x, j) => (j === k ? { ...x, ...patch } : x)) });

  return (
    <ReviewSection id="rv-visibility" title="Visibility & labels" aside="Public items reach anyone; everything else needs the authority key.">
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-2">
          <h3 className="text-[13px] font-semibold text-slate">Map layers</h3>
          {outLayers.length === 0 && <p className="text-[13px] text-ink">No output layers.</p>}
          <div className="grid gap-1.5">
            {outLayers.map((l, n) => (
              <label key={l.id} className="flex items-center gap-2 text-[13px]">
                <span className="min-w-0 flex-1 break-all font-mono text-slate">{l.id}</span>
                <select
                  aria-label={`${l.id} output visibility`}
                  value={l.visibility ?? "authority"}
                  onChange={(e) => setOutputs({ layers: outLayers.map((x, j) => (j === n ? { ...x, visibility: e.target.value as Visibility } : x)) })}
                  className={field}
                >
                  {VISIBILITIES.map((x) => (
                    <option key={x}>{x}</option>
                  ))}
                </select>
              </label>
            ))}
          </div>
          <label className={`flex items-center gap-2 pt-1 ${label}`}>
            hotspot threshold
            <input
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
            <span className="text-[12px]">score at which cells form a hotspot</span>
          </label>
        </div>

        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-[13px] font-semibold text-slate">Place labels</h3>
            <select aria-label="label mode" value={labels.mode ?? "auto"} onChange={(e) => setLabels({ mode: e.target.value as PoiLabelMode })} className={field}>
              {LABEL_MODES.map((x) => (
                <option key={x}>{x}</option>
              ))}
            </select>
            <label className={`ml-auto flex items-center gap-1.5 ${label}`}>
              min confidence
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
            </label>
          </div>
          {labelSet.length === 0 && <p className="text-[13px] text-ink">No labels yet.</p>}
          {labelSet.map((l, n) => (
            <div key={n} className={`flex flex-wrap items-center gap-x-3 gap-y-1 ${label}`}>
              <input aria-label="label" value={l.label} onChange={(e) => setLabel(n, { label: e.target.value })} className={`${field} min-w-0 flex-1 max-sm:basis-full`} />
              <label className="flex items-center gap-1" title="Shown only with the authority key">
                <input
                  type="checkbox"
                  className="accent-mutedTeal"
                  checked={!!l.sensitive}
                  onChange={(e) => setLabel(n, { sensitive: e.target.checked, ...(e.target.checked ? { destination: false } : {}) })}
                />
                sensitive
              </label>
              <label className="flex items-center gap-1" title="A public routing target; never sensitive">
                <input
                  type="checkbox"
                  className="accent-mutedTeal"
                  checked={!!l.destination}
                  onChange={(e) => setLabel(n, { destination: e.target.checked, ...(e.target.checked ? { sensitive: false } : {}) })}
                />
                destination
              </label>
              <button type="button" aria-label={`remove label ${l.label}`} onClick={() => setLabels({ label_set: labelSet.filter((_, j) => j !== n) })} className={`${smallBtn} tap justify-center`}>
                <Trash2 aria-hidden className="h-3 w-3" />
              </button>
            </div>
          ))}
          <button type="button" onClick={() => setLabels({ label_set: [...labelSet, { label: "", sensitive: false, destination: false }] })} className={`${smallBtn} tap`}>
            <Plus aria-hidden className="h-3 w-3" /> Add a label
          </button>
          <p className="text-[12px] text-ink">Sensitive labels stay in the authority view. Destination labels are public routing targets.</p>
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

  return (
    <ReviewSection id="rv-routing" title="Routing" aside="Public route instructions toward destinations, away from high scores.">
      <label className="flex items-center gap-2 text-[13.5px] text-slate max-sm:min-h-11">
        <input type="checkbox" className="accent-mutedTeal" checked={!!routing.enabled} onChange={(e) => setRouting({ enabled: e.target.checked })} />
        Give end users route instructions
      </label>
      {routing.enabled && (
        <div className="space-y-3">
          <p className="text-[13px] text-ink">
            {dests.length === 0
              ? "No fixed destinations: routes go only to places labelled as destinations."
              : `Fixed destinations: ${dests.map((p) => p.label).join(", ")}.`}
          </p>
          <div className={`flex flex-wrap gap-x-5 gap-y-2 ${label}`}>
            <label className="flex items-center gap-1.5">
              impassable at score
              <input
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
            </label>
            <label className="flex items-center gap-1.5">
              avoidance (alpha)
              <input
                aria-label="routing alpha"
                type="number"
                step="0.5"
                min={0}
                value={routing.alpha ?? ""}
                placeholder="4"
                onChange={(e) => setRouting({ alpha: numOrUndef(e.target.value) })}
                className={`${mono} w-20`}
              />
            </label>
          </div>
          <label className="block space-y-1">
            <span className={label}>Text when no route exists</span>
            <textarea
              aria-label="routing fallback text"
              aria-invalid={over}
              rows={2}
              value={fallback}
              placeholder="No route available."
              onChange={(e) => setRouting({ fallback_text: e.target.value })}
              className={`${field} block w-full max-w-[70ch] resize-y`}
            />
            <span className={`block text-[12px] tabular-nums ${over ? "font-semibold text-[#8A4B39]" : "text-ink"}`}>
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
        <div className="rounded-2xl bg-coral/15 p-3.5 text-[13px] text-[#7A4232]">
          <p className="font-semibold">Check before confirming</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {d.warnings.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </div>
      )}
      {d.saveWarnings && d.saveWarnings.length > 0 && (
        <div role="status" className="rounded-2xl bg-sage/20 p-3.5 text-[13px] text-slate">
          <p className="font-semibold">Saved, with warnings</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {d.saveWarnings.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </div>
      )}
      {d.saveErrors.length > 0 && (
        <div role="alert" className="rounded-2xl bg-coral/20 p-3.5 text-[13px] text-[#7A4232]">
          <p className="font-semibold">The server didn't accept these edits. Fix them, then save again.</p>
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
