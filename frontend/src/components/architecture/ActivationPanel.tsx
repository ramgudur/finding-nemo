// Review and edit the proposed spec, save it (PUT /spec), then Confirm & Activate (POST /activate).
// Ported from Builder.tsx `Review` + save()/confirm(). Rule 4: the Compiler only proposed these values;
// they take effect when the dev confirms, and after that no agent can change tiers, locked weights or verification.
import { Lock, Plus, ShieldCheck, Trash2, TriangleAlert, Undo2 } from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { saveKey } from "../../api/authorityKey";
import { ApiError, api } from "../../api/client";
import type {
  ActivateResponse,
  AppSpec,
  InputSpec,
  OperatorConfig,
  Outputs,
  Plausibility,
  PoiLabel,
  PoiLabelConfig,
  PoiLabelMode,
  Tier,
  Visibility,
} from "../../api/types";
import { TIER_SURFACE, TierBadge } from "./TierBadge";
import { TIER_HELP, TIERS, layerIdOf, operatorChoices, useOperatorCatalog } from "./specLayers";

const VISIBILITIES: Visibility[] = ["authority", "public"];
const LABEL_MODES: PoiLabelMode[] = ["auto", "on", "off"];
const numOrNull = (v: string) => (v.trim() === "" || !Number.isFinite(Number(v)) ? null : Number(v));

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
    out.push("score is public while some layers are private.");
  }
  return out;
}

const field =
  "rounded-lg border border-paleWater bg-white/70 px-2 py-1 text-[12.5px] text-slate focus:outline-none focus:ring-2 focus:ring-mutedTeal/40";
const mono = `${field} font-mono`;
const ghostBtn =
  "inline-flex items-center gap-1 rounded-full bg-white/60 px-2 py-0.5 text-[11.5px] font-semibold text-ink ring-1 ring-paleWater transition-[transform,box-shadow] duration-150 hover:-translate-y-px hover:shadow-sm disabled:opacity-50";

function Section({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <motion.section
      variants={{ hidden: { opacity: 0, y: 8 }, show: { opacity: 1, y: 0 } }}
      className="space-y-2 rounded-2xl border border-paleWater bg-white/35 p-3.5"
    >
      <div className="flex flex-wrap items-baseline gap-2">
        <h4 className="text-[13px] font-bold text-slate">{title}</h4>
        {aside && <span className="text-[11.5px] text-inkSoft">{aside}</span>}
      </div>
      {children}
    </motion.section>
  );
}

function TierPicker({ inputId, value, onChange }: { inputId: string; value: Tier; onChange: (t: Tier) => void }) {
  return (
    <div role="radiogroup" aria-label={`${inputId} tier`} className="inline-flex flex-wrap gap-1">
      {TIERS.map((t) => (
        <button
          key={t}
          type="button"
          role="radio"
          aria-checked={value === t}
          title={TIER_HELP[t]}
          onClick={() => onChange(t)}
          className={`rounded-full transition-opacity ${value === t ? "opacity-100" : "opacity-45 hover:opacity-80"}`}
        >
          <TierBadge tier={t} />
        </button>
      ))}
    </div>
  );
}

function InputCard({
  input,
  onInput,
  onOperators,
}: {
  input: InputSpec;
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

  return (
    <motion.div
      variants={{ hidden: { opacity: 0, y: 8 }, show: { opacity: 1, y: 0 } }}
      className={`space-y-2.5 rounded-2xl border p-3.5 ${TIER_SURFACE[input.tier]}`}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-mono text-[13px] font-medium text-slate">{input.id}</span>
        <span className="text-[11.5px] text-inkSoft">
          {input.kind} · {input.shape}
        </span>
        <label className="ml-auto flex items-center gap-1 text-[11.5px] text-inkSoft">
          visibility
          <select
            aria-label={`${input.id} visibility`}
            value={input.visibility ?? "authority"}
            onChange={(e) => onInput({ visibility: e.target.value as Visibility })}
            className={field}
          >
            {VISIBILITIES.map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </label>
      </div>
      <TierPicker inputId={input.id} value={input.tier} onChange={(tier) => onInput({ tier })} />
      {input.meaning && <p className="border-l-2 border-sage/70 pl-2 text-[12px] italic text-inkSoft">“{input.meaning}”</p>}

      <div className="space-y-1.5">
        <span className="text-[11.5px] font-semibold uppercase tracking-wide text-inkSoft">operators</span>
        {ops.length === 0 && <p className="text-[12px] text-inkSoft">none</p>}
        {ops.map((o, n) => (
          <div key={layerIdOf(input.id, o)} className="flex flex-wrap items-center gap-1.5">
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
              className={mono}
            >
              {[o.op, ...choices.filter((c) => c !== o.op && !ops.some((x) => x.op === c))].map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
            {locked ? (
              <span
                className="inline-flex items-center gap-1 px-1 text-[12px] text-slate"
                title="Locked weights never change. Pick another tier first to edit it."
              >
                <Lock aria-hidden className="h-3 w-3" />
                <span className="text-inkSoft">weight</span>
                <span className="font-mono tabular-nums">{o.weight}</span>
              </span>
            ) : (
              <label className="flex items-center gap-1 text-[11.5px] text-inkSoft">
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
            <span className="font-mono text-[11px] text-inkSoft">{layerIdOf(input.id, o)}</span>
            <button
              type="button"
              aria-label={`remove ${layerIdOf(input.id, o)}`}
              onClick={() => onOperators(ops.filter((_, j) => j !== n))}
              className={`${ghostBtn} ml-auto`}
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
            <option value="">add an operator…</option>
            {unused.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        )}
      </div>

      {(input.shape === "point" || input.shape === "track") && (
        <div className="space-y-1.5">
          <span className="text-[11.5px] font-semibold uppercase tracking-wide text-inkSoft">plausibility</span>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-[11.5px] text-inkSoft">
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
            <label className="flex items-center gap-1">
              <input
                type="checkbox"
                className="accent-mutedTeal"
                checked={!!p.flag_constant_speed}
                onChange={(e) => setP({ flag_constant_speed: e.target.checked })}
              />
              flag constant speed
            </label>
            <label className="flex items-center gap-1">
              <input
                type="checkbox"
                className="accent-mutedTeal"
                checked={!!p.flag_straight_line}
                onChange={(e) => setP({ flag_straight_line: e.target.checked })}
              />
              flag straight lines
            </label>
          </div>
        </div>
      )}

      {input.connector && (
        <p className="text-[12px] text-ink">
          <span className="text-inkSoft">connector </span>
          <span className="font-mono">
            {input.connector.type}
            {input.connector.preset ? ` (${input.connector.preset})` : ""}
          </span>{" "}
          <span className="break-all font-mono text-[11px] text-inkSoft">{input.connector.url ?? ""}</span>
        </p>
      )}
    </motion.div>
  );
}

export interface ActivationPanelProps {
  spec: AppSpec;
  appId: string;
  onSpecSaved: (spec: AppSpec) => void;
  onActivated: (resp: ActivateResponse) => void;
}

export function ActivationPanel({ spec, appId, onSpecSaved, onActivated }: ActivationPanelProps) {
  const specJson = JSON.stringify(spec);
  const [draft, setDraft] = useState<AppSpec>(spec);
  const [saving, setSaving] = useState(false);
  const [activating, setActivating] = useState(false);
  const [saveWarnings, setSaveWarnings] = useState<string[] | null>(null);
  const [saveErrors, setSaveErrors] = useState<string[]>([]);
  const [activateError, setActivateError] = useState("");

  // a new spec from the parent (e.g. a recompile) replaces the draft
  useEffect(() => {
    setDraft(JSON.parse(specJson) as AppSpec);
  }, [specJson]);

  const dirty = JSON.stringify(draft) !== specJson;
  const localWarnings = useMemo(() => reviewWarnings(draft), [draft]);

  const inputs = draft.inputs ?? [];
  const outputs = draft.outputs ?? {};
  const outLayers = outputs.layers ?? [];
  const labels = outputs.poi_labels ?? {};
  const labelSet = labels.label_set ?? [];
  const routing = draft.routing ?? {};
  const verification = [...(draft.verification ?? [])].sort((a, b) => a.priority - b.priority);

  const change = (next: AppSpec) => {
    setDraft(next);
    setSaveErrors([]);
  };
  const setInput = (k: number, patch: Partial<InputSpec>) =>
    change({ ...draft, inputs: inputs.map((x, j) => (j === k ? { ...x, ...patch } : x)) });
  const setOutputs = (patch: Partial<Outputs>) => change({ ...draft, outputs: { ...outputs, ...patch } });
  const setLabels = (patch: Partial<PoiLabelConfig>) => setOutputs({ poi_labels: { ...labels, ...patch } });
  const setLabel = (k: number, patch: Partial<PoiLabel>) =>
    setLabels({ label_set: labelSet.map((x, j) => (j === k ? { ...x, ...patch } : x)) });

  /** Replace one input's operators, keeping outputs.layers pointing only at layers that still exist. */
  const setOperators = (k: number, ops: OperatorConfig[], renamed?: [string, string]) => {
    if (!inputs[k]) return;
    const nextInputs = inputs.map((x, j) => (j === k ? { ...x, operators: ops } : x));
    const known = new Set(["score", ...nextInputs.flatMap((x) => (x.operators ?? []).map((o) => layerIdOf(x.id, o)))]);
    const nextLayers = outLayers
      .map((l) => (renamed && l.id === renamed[0] ? { ...l, id: renamed[1] } : l))
      .filter((l) => known.has(l.id));
    change({ ...draft, inputs: nextInputs, outputs: { ...outputs, layers: nextLayers } });
  };

  const save = async () => {
    setSaving(true);
    setSaveErrors([]);
    try {
      const r = await api.updateSpec(appId, draft);
      setDraft(r.spec);
      setSaveWarnings(r.warnings);
      onSpecSaved(r.spec);
    } catch (e) {
      setSaveWarnings(null);
      if (e instanceof ApiError && e.details.length > 0) setSaveErrors(e.details);
      else if (e instanceof ApiError && e.status === 409) setSaveErrors(["This app is no longer pending confirmation."]);
      else setSaveErrors([e instanceof ApiError ? e.message : String(e)]);
    } finally {
      setSaving(false);
    }
  };

  const confirm = async () => {
    setActivating(true);
    setActivateError("");
    try {
      const k = await api.activate(appId);
      saveKey(appId, k.authority_key, false); // this tab only; the dev keeps their own copy
      onActivated(k);
    } catch (e) {
      setActivateError(
        e instanceof ApiError && e.status === 409
          ? "This app is already active. Its keys were shown when it was activated."
          : e instanceof ApiError
            ? e.message
            : String(e),
      );
    } finally {
      setActivating(false);
    }
  };

  return (
    <motion.div initial="hidden" animate="show" transition={{ staggerChildren: 0.05 }} className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-[15px] font-bold text-slate">Review before you confirm</h3>
        <span
          className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${dirty ? "bg-coral/20 text-[#8A4B39]" : "bg-seaGlass/25 text-[#2F6265]"}`}
        >
          {dirty ? "unsaved changes" : "saved"}
        </span>
      </div>
      <p className="text-[12.5px] text-ink">
        The Compiler proposed these tiers, operators, weights and visibility. Nothing takes effect until you confirm.
      </p>

      {localWarnings.length > 0 && (
        <div className="rounded-2xl border border-coral/40 bg-coral/10 p-3 text-[12.5px] text-[#7A4232]">
          <p className="flex items-center gap-1.5 font-semibold">
            <TriangleAlert aria-hidden className="h-3.5 w-3.5" /> Check before confirming
          </p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {localWarnings.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </div>
      )}

      {inputs.map((i, k) => (
        <InputCard key={i.id} input={i} onInput={(patch) => setInput(k, patch)} onOperators={(ops, r) => setOperators(k, ops, r)} />
      ))}

      <Section title="Verification" aside="proposed, in priority order; fixed once confirmed">
        {verification.length === 0 ? (
          <p className="text-[12px] text-inkSoft">none proposed</p>
        ) : (
          <ol className="space-y-1">
            {verification.map((v) => (
              <li key={v.method} className="flex items-baseline gap-2 text-[12.5px]">
                <span className="w-4 text-right font-mono text-[11px] text-inkSoft">{v.priority}</span>
                <span className="font-mono text-slate">{v.method}</span>
                {v.priority === verification[0]?.priority && <span className="text-[11px] text-inkSoft">decides backtests first</span>}
              </li>
            ))}
          </ol>
        )}
      </Section>

      <Section title="Layer visibility" aside="what the public map may show">
        {outLayers.length === 0 && <p className="text-[12px] text-inkSoft">none</p>}
        <div className="grid gap-1.5">
          {outLayers.map((l, n) => (
            <label key={l.id} className="flex items-center gap-2 text-[12.5px]">
              <span className="min-w-0 flex-1 break-all font-mono text-slate">{l.id}</span>
              <select
                aria-label={`${l.id} output visibility`}
                value={l.visibility ?? "authority"}
                onChange={(e) =>
                  setOutputs({ layers: outLayers.map((x, j) => (j === n ? { ...x, visibility: e.target.value as Visibility } : x)) })
                }
                className={field}
              >
                {VISIBILITIES.map((x) => (
                  <option key={x}>{x}</option>
                ))}
              </select>
            </label>
          ))}
        </div>
      </Section>

      <Section
        title="Labels"
        aside={
          <select aria-label="label mode" value={labels.mode ?? "auto"} onChange={(e) => setLabels({ mode: e.target.value as PoiLabelMode })} className={field}>
            {LABEL_MODES.map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        }
      >
        {labelSet.length === 0 && <p className="text-[12px] text-inkSoft">none</p>}
        {labelSet.map((l, n) => (
          <div key={n} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11.5px] text-inkSoft">
            <input aria-label="label" value={l.label} onChange={(e) => setLabel(n, { label: e.target.value })} className={`${field} min-w-0 flex-1`} />
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
            <button
              type="button"
              aria-label={`remove label ${l.label}`}
              onClick={() => setLabels({ label_set: labelSet.filter((_, j) => j !== n) })}
              className={ghostBtn}
            >
              <Trash2 aria-hidden className="h-3 w-3" />
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() => setLabels({ label_set: [...labelSet, { label: "", sensitive: false, destination: false }] })}
          className={ghostBtn}
        >
          <Plus aria-hidden className="h-3 w-3" /> Add a label
        </button>
        <p className="text-[11px] text-inkSoft">Sensitive labels stay in the authority view. Destination labels are public routing targets.</p>
      </Section>

      <Section title="Routing" aside={`${routing.destination_points?.length ?? 0} fixed destinations`}>
        <label className="flex items-center gap-2 text-[12.5px] text-ink">
          <input
            type="checkbox"
            className="accent-mutedTeal"
            checked={!!routing.enabled}
            onChange={(e) => change({ ...draft, routing: { ...routing, enabled: e.target.checked } })}
          />
          give end users route instructions toward destinations, away from high scores
        </label>
      </Section>

      {saveWarnings && saveWarnings.length > 0 && (
        <div role="status" className="rounded-2xl border border-sage/60 bg-sage/15 p-3 text-[12.5px] text-slate">
          <p className="font-semibold">Saved, with warnings</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {saveWarnings.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </div>
      )}
      {saveErrors.length > 0 && (
        <div role="alert" className="rounded-2xl border border-coral/50 bg-coral/15 p-3 text-[12.5px] text-[#7A4232]">
          <p className="font-semibold">The server didn't accept these edits</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {saveErrors.map((x) => (
              <li key={x} className="[overflow-wrap:anywhere]">
                {x}
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="flex items-start gap-1.5 rounded-xl bg-softStone/50 px-3 py-2 text-[12px] text-slate">
        <ShieldCheck aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 text-mutedTeal" />
        After you confirm, tiers, locked weights and verification methods can't be changed by any agent.
      </p>

      <div className="sticky bottom-0 -mx-1 flex flex-wrap items-center gap-2 rounded-2xl border border-paleWater bg-mist/90 p-2 backdrop-blur-sm">
        <button
          type="button"
          disabled={!dirty || saving}
          onClick={() => {
            setDraft(JSON.parse(specJson) as AppSpec);
            setSaveErrors([]);
          }}
          className={ghostBtn}
        >
          <Undo2 aria-hidden className="h-3 w-3" /> Discard
        </button>
        <button
          type="button"
          disabled={!dirty || saving}
          onClick={() => void save()}
          className="rounded-full bg-white/80 px-3 py-1 text-[12.5px] font-semibold text-slate ring-1 ring-seaGlass transition-[transform,box-shadow] duration-150 hover:-translate-y-px hover:shadow-sm disabled:opacity-50"
        >
          {saving ? "Saving…" : "Save edits"}
        </button>
        <button
          type="button"
          disabled={dirty || saving || activating}
          title={dirty ? "Save or discard your edits first" : undefined}
          onClick={() => void confirm()}
          className="ml-auto rounded-full bg-mutedTeal px-4 py-1.5 text-[12.5px] font-bold text-cream shadow-[0_6px_16px_-8px_rgba(53,89,107,0.8)] transition-[transform,box-shadow] duration-150 hover:-translate-y-px hover:shadow-[0_10px_22px_-10px_rgba(53,89,107,0.9)] disabled:translate-y-0 disabled:opacity-45 disabled:shadow-none"
        >
          {activating ? "Activating…" : "Confirm & Activate"}
        </button>
        {dirty && <p className="w-full text-[11px] text-inkSoft">Save or discard your edits before confirming.</p>}
        {activateError && (
          <p role="alert" className="w-full text-[12px] font-semibold text-[#7A4232]">
            {activateError}
          </p>
        )}
      </div>
    </motion.div>
  );
}

export default ActivationPanel;
