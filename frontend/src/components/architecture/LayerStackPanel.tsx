// RIGHT panel of the Architecture page: the layer stack (one row per operator layer) plus either the
// pre-activation review (ActivationPanel) or, once active, the authority console tabs.
// Rule 8: weights, verification, trace and changes are fetched only with the app's authority key.
import { Activity, FlaskConical, History, KeyRound, ListTree, Scale } from "lucide-react";
import { MotionConfig, motion } from "motion/react";
import { useEffect, useMemo, useState } from "react";
import { forgetKey, loadKey } from "../../api/authorityKey";
import type { ActivateResponse, AppSpec, Change, OperatorConfig, TraceEntry } from "../../api/types";
import { useAuthorityConsoleData } from "../../hooks/useAuthorityConsoleData";
import { AgentTrace } from "../AgentTrace/AgentTrace";
import { VerificationScores } from "../VerificationScores/VerificationScores";
import { WeightsPanel } from "../WeightsPanel/WeightsPanel";
import { ActivationPanel } from "./ActivationPanel";
import { AuthorityKeyPrompt } from "./AuthorityKeyPrompt";
import { ChangesList } from "./ChangesList";
import { KeyReveal } from "./KeyReveal";
import { LayerRow } from "./LayerRow";
import { specLayers, useOperatorCatalog, type SpecLayer } from "./specLayers";
import { TierBadge } from "./TierBadge";

export interface LayerStackPanelProps {
  spec: AppSpec;
  appId: string;
  authorityKey: string | null;
  active: boolean;
  selectedLayerId: string | null;
  onSelectLayer: (id: string | null) => void;
  onSpecSaved: (spec: AppSpec) => void;
  onActivated: (resp: ActivateResponse) => void;
}

type Tab = "weights" | "verification" | "trace" | "changes";
const TABS: { id: Tab; label: string; Icon: typeof Scale }[] = [
  { id: "weights", label: "Weights", Icon: Scale },
  { id: "verification", label: "Verification", Icon: FlaskConical },
  { id: "trace", label: "Trace", Icon: Activity },
  { id: "changes", label: "Changes", Icon: History },
];

const RESERVED = new Set(["op", "weight", "layer_id", "noise_floor"]);
const paramsOf = (op: OperatorConfig) => Object.entries(op).filter(([k, v]) => !RESERVED.has(k) && v !== null && v !== undefined);

/** Trace entries that name this layer anywhere in what the agent saw, did or called. */
function traceFor(trace: TraceEntry[], layerId: string): TraceEntry[] {
  return trace.filter((e) =>
    [e.saw, e.hypothesis, e.tested, e.result, e.action, e.why, ...e.tool_calls.map((c) => JSON.stringify(c.args) + c.result_summary)].some((s) =>
      s.includes(layerId),
    ),
  );
}

function LayerDetail({
  layer,
  liveWeight,
  pinned,
  trace,
  changes,
  authority,
}: {
  layer: SpecLayer;
  liveWeight: number | null;
  pinned: boolean | null;
  trace: TraceEntry[];
  changes: Change[];
  authority: boolean;
}) {
  const { input, op } = layer;
  const params = paramsOf(op);
  const fields = input.fields ?? [];
  const recent = [...trace].reverse().slice(0, 2);
  const recentChanges = [...changes].sort((a, b) => b.t.localeCompare(a.t)).slice(0, 3);
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-[12px]">
      <dt className="text-inkSoft">source</dt>
      <dd className="text-ink">
        <span className="font-mono">{input.id}</span> · {input.kind} · {input.shape}
        {input.connector && (
          <span className="font-mono text-inkSoft">
            {" "}
            · {input.connector.type}
            {input.connector.preset ? ` (${input.connector.preset})` : ""}
          </span>
        )}
      </dd>
      {fields.length > 0 && (
        <>
          <dt className="text-inkSoft">fields</dt>
          <dd className="break-all font-mono text-ink">{fields.map((f) => `${f.name}:${f.type}${f.required === false ? "?" : ""}`).join(", ")}</dd>
        </>
      )}
      <dt className="text-inkSoft">params</dt>
      <dd className="break-all font-mono text-ink">
        {params.length ? params.map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join("  ") : "defaults"}
        {op.noise_floor != null && `  noise_floor=${op.noise_floor}`}
      </dd>
      <dt className="text-inkSoft">weight</dt>
      <dd className="font-mono text-ink">
        spec {op.weight}
        {liveWeight !== null && ` · live ${liveWeight.toFixed(3)}`}
        {pinned && " · pinned"}
      </dd>
      {authority && (
        <>
          <dt className="text-inkSoft">trace</dt>
          <dd className="text-ink">
            {recent.length === 0
              ? "no agent run has mentioned this layer yet"
              : recent.map((e, i) => (
                  <span key={i} className="block">
                    <span className="font-semibold">{e.agent}</span>{" "}
                    <span className="font-mono text-[11px] text-inkSoft">{new Date(e.t).toLocaleTimeString()}</span>{" "}
                    {e.skipped_reason ? `skipped: ${e.skipped_reason}` : e.action || e.result || e.why}
                  </span>
                ))}
            {trace.length > 2 && <span className="block text-inkSoft">+{trace.length - 2} more in Trace</span>}
          </dd>
          <dt className="text-inkSoft">changes</dt>
          <dd className="text-ink">
            {recentChanges.length === 0
              ? "none"
              : recentChanges.map((c) => (
                  <span key={c.id} className="block">
                    <span className="font-mono text-[11px] text-inkSoft">{new Date(c.t).toLocaleTimeString()}</span> {c.actor}: {c.reason}
                  </span>
                ))}
          </dd>
        </>
      )}
    </dl>
  );
}

export function LayerStackPanel({
  spec,
  appId,
  authorityKey,
  active,
  selectedLayerId,
  onSelectLayer,
  onSpecSaved,
  onActivated,
}: LayerStackPanelProps) {
  const catalog = useOperatorCatalog();
  const [key, setKey] = useState<string | null>(() => authorityKey || loadKey(appId).key || null);
  const [revealed, setRevealed] = useState<ActivateResponse | null>(null);
  const [tab, setTab] = useState<Tab>("weights");

  useEffect(() => {
    if (authorityKey) setKey(authorityKey);
  }, [authorityKey]);

  const data = useAuthorityConsoleData(appId, active ? key : null);
  const authority = active && !!key && !data.rejected;
  const layers = useMemo(() => specLayers(spec), [spec]);
  const live = new Map((data.weights?.layers ?? []).map((l) => [l.layer_id, l]));
  const weightOf = (l: SpecLayer) => live.get(l.layerId)?.weight ?? l.op.weight;
  const maxWeight = Math.max(0, ...layers.filter((l) => l.op.op !== "override").map(weightOf));
  const infoOf = (op: string) => catalog?.find((o) => o.name === op) ?? null;

  const statusOf = (l: SpecLayer) => {
    if (!active) return "proposed";
    if ((infoOf(l.op.op)?.role ?? (l.op.op === "override" ? "override" : "layer")) === "override") return "override";
    return live.get(l.layerId)?.pinned ? "pinned" : "live";
  };

  const activated = (resp: ActivateResponse) => {
    setKey(resp.authority_key);
    setRevealed(resp);
    onActivated(resp);
  };

  const tierCounts = layers.reduce<Record<string, number>>((acc, l) => ({ ...acc, [l.input.tier]: (acc[l.input.tier] ?? 0) + 1 }), {});

  return (
    <MotionConfig reducedMotion="user">
      <aside
        aria-label="Layer stack"
        className="watercolor-grain flex min-w-0 flex-col gap-4 rounded-[28px] border border-white/60 bg-[linear-gradient(160deg,rgba(240,235,221,0.55),rgba(213,232,235,0.75)_45%,rgba(187,220,227,0.6))] p-4 shadow-[0_18px_40px_-28px_rgba(53,89,107,0.55)] sm:p-5"
      >
        <header className="relative space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <ListTree aria-hidden className="h-4 w-4 text-mutedTeal" />
            <h2 className="text-[16px] font-bold text-slate">Layer stack</h2>
            <span className="font-mono text-[11.5px] text-inkSoft">{layers.length} layers</span>
            <span
              className={`ml-auto rounded-full px-2 py-0.5 text-[11px] font-semibold ${
                active ? "bg-seaGlass/30 text-[#2F6265]" : "bg-softStone/70 text-slate"
              }`}
            >
              {active ? "active" : "awaiting confirmation"}
            </span>
          </div>
          <p className="rounded-xl bg-white/40 px-2.5 py-1.5 font-mono text-[11.5px] text-ink">
            score = clamp(Σ wᵢ · norm(layerᵢ), 0, 1) <span className="text-inkSoft">→ then authority overrides</span>
          </p>
          <div className="flex flex-wrap gap-1.5">
            {(["locked", "stiff", "flexible"] as const)
              .filter((t) => tierCounts[t])
              .map((t) => (
                <span key={t} className="inline-flex items-center gap-1">
                  <TierBadge tier={t} />
                  <span className="font-mono text-[11px] text-inkSoft">×{tierCounts[t]}</span>
                </span>
              ))}
          </div>
        </header>

        {revealed && <KeyReveal keys={revealed} onDismiss={() => setRevealed(null)} />}

        {layers.length === 0 ? (
          <p className="text-[12.5px] text-inkSoft">This spec has no operator layers yet.</p>
        ) : (
          <motion.ul
            initial="hidden"
            animate="show"
            variants={{ hidden: {}, show: { transition: { staggerChildren: 0.06, delayChildren: 0.05 } } }}
            className="relative space-y-2.5 p-0"
          >
            {layers.map((l) => {
              const selected = selectedLayerId === l.layerId;
              const row = live.get(l.layerId);
              return (
                <LayerRow
                  key={l.layerId}
                  layer={l}
                  info={infoOf(l.op.op)}
                  weight={weightOf(l)}
                  maxWeight={maxWeight}
                  status={statusOf(l)}
                  selected={selected}
                  onToggle={() => onSelectLayer(selected ? null : l.layerId)}
                >
                  <LayerDetail
                    layer={l}
                    liveWeight={row ? row.weight : null}
                    pinned={row ? row.pinned : null}
                    trace={authority ? traceFor(data.trace, l.layerId) : []}
                    changes={authority ? data.changes.filter((c) => c.target === l.layerId) : []}
                    authority={authority}
                  />
                </LayerRow>
              );
            })}
          </motion.ul>
        )}

        {!active ? (
          <ActivationPanel spec={spec} appId={appId} onSpecSaved={onSpecSaved} onActivated={activated} />
        ) : !key || data.rejected ? (
          <AuthorityKeyPrompt appId={appId} rejected={data.rejected} onKey={setKey} />
        ) : (
          <section aria-label="Authority console" className="space-y-3">
            <div className="flex items-center gap-2">
              <div role="tablist" aria-label="Console" className="relative flex flex-1 flex-wrap gap-1 rounded-full bg-white/40 p-1">
                {TABS.map(({ id, label, Icon }) => (
                  <button
                    key={id}
                    role="tab"
                    type="button"
                    aria-selected={tab === id}
                    onClick={() => setTab(id)}
                    className={`relative flex flex-1 items-center justify-center gap-1 rounded-full px-2.5 py-1 text-[12px] font-semibold transition-colors ${
                      tab === id ? "text-slate" : "text-inkSoft hover:text-ink"
                    }`}
                  >
                    {tab === id && (
                      <motion.span
                        layoutId="console-tab"
                        aria-hidden
                        className="absolute inset-0 rounded-full bg-cream shadow-sm ring-1 ring-paleWater"
                        transition={{ type: "spring", stiffness: 420, damping: 36 }}
                      />
                    )}
                    <Icon aria-hidden className="relative h-3.5 w-3.5" />
                    <span className="relative">{label}</span>
                    {id === "changes" && data.changes.length > 0 && (
                      <span className="relative font-mono text-[10.5px] text-inkSoft">{data.changes.length}</span>
                    )}
                  </button>
                ))}
              </div>
              <button
                type="button"
                title="Forget the authority key in this browser"
                aria-label="forget authority key"
                onClick={() => {
                  forgetKey(appId);
                  setKey(null);
                }}
                className="rounded-full p-1.5 text-inkSoft ring-1 ring-paleWater transition-colors hover:bg-white/50 hover:text-ink"
              >
                <KeyRound aria-hidden className="h-3.5 w-3.5" />
              </button>
            </div>
            <motion.div
              key={tab}
              role="tabpanel"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.2 }}
            >
              {!data.loaded ? (
                <p className="text-[12.5px] text-inkSoft">Loading the authority view…</p>
              ) : tab === "weights" ? (
                data.weights ? (
                  <>
                    <WeightsPanel view={data.weights} busy={data.pinBusy} onPin={(l, p) => void data.pin(l, p)} />
                    {data.pinError && <p className="mt-1 text-[12px] font-semibold text-[#7A4232]">{data.pinError}</p>}
                  </>
                ) : (
                  <p className="text-[12.5px] text-inkSoft">Weights aren't available yet.</p>
                )
              ) : tab === "verification" ? (
                data.verification ? (
                  <VerificationScores view={data.verification} />
                ) : (
                  <p className="text-[12.5px] text-inkSoft">Verification isn't available yet.</p>
                )
              ) : tab === "trace" ? (
                <AgentTrace entries={data.trace} />
              ) : (
                <ChangesList changes={data.changes} onRevert={data.revert} />
              )}
            </motion.div>
          </section>
        )}
      </aside>
    </MotionConfig>
  );
}

export default LayerStackPanel;
