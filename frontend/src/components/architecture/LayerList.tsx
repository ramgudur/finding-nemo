// The console's Layers tab: one row per operator layer, selectable; selecting one draws it on the map.
// With the authority key it lists the spec's layers (tier, weight, detail, trace mentions). In the
// public view the spec is authority-only (rule 8), so it lists only the layers the server sent.
import { Layers } from "lucide-react";
import { useMemo } from "react";
import type { AppSpec, Change, OperatorConfig, SourceStatus, TraceEntry, WeightsView } from "../../api/types";
import { useNow } from "../../hooks/useSourceStatus";
import { describeSource } from "../app/sourceStatus";
import { LayerRow } from "./LayerRow";
import { specLayers, useOperatorCatalog, type SpecLayer } from "./specLayers";
import { TierBadge } from "./TierBadge";

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

function LayerDetail({ layer, liveWeight, pinned, trace, changes }: { layer: SpecLayer; liveWeight: number | null; pinned: boolean | null; trace: TraceEntry[]; changes: Change[] }) {
  const { input, op } = layer;
  const params = paramsOf(op);
  const fields = input.fields ?? [];
  const recent = [...trace].reverse().slice(0, 2);
  const recentChanges = [...changes].sort((a, b) => b.t.localeCompare(a.t)).slice(0, 3);
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 text-[12.5px]">
      <dt className="text-ink">source</dt>
      <dd className="text-slate">
        <span className="font-mono">{input.id}</span> · {input.kind} · {input.shape}
        {input.connector && (
          <span className="font-mono text-ink">
            {" "}
            · {input.connector.type}
            {input.connector.preset ? ` (${input.connector.preset})` : ""}
          </span>
        )}
      </dd>
      {fields.length > 0 && (
        <>
          <dt className="text-ink">fields</dt>
          <dd className="break-all font-mono text-slate">{fields.map((f) => `${f.name}:${f.type}${f.required === false ? "?" : ""}`).join(", ")}</dd>
        </>
      )}
      <dt className="text-ink">params</dt>
      <dd className="break-all font-mono text-slate">
        {params.length ? params.map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join("  ") : "defaults"}
        {op.noise_floor != null && `  noise_floor=${op.noise_floor}`}
      </dd>
      <dt className="text-ink">weight</dt>
      <dd className="font-mono text-slate">
        spec {op.weight}
        {liveWeight !== null && ` · live ${liveWeight.toFixed(3)}`}
        {pinned && " · pinned"}
      </dd>
      <dt className="text-ink">trace</dt>
      <dd className="text-slate">
        {recent.length === 0
          ? "No agent run has mentioned this layer yet."
          : recent.map((e, i) => (
              <span key={i} className="block">
                <span className="font-semibold">{e.agent}</span> <span className="font-mono text-[11px] text-ink">{new Date(e.t).toLocaleTimeString()}</span>{" "}
                {e.skipped_reason ? `skipped: ${e.skipped_reason}` : e.action || e.result || e.why}
              </span>
            ))}
        {trace.length > 2 && <span className="block text-ink">+{trace.length - 2} more in Trace</span>}
      </dd>
      <dt className="text-ink">changes</dt>
      <dd className="text-slate">
        {recentChanges.length === 0
          ? "None."
          : recentChanges.map((c) => (
              <span key={c.id} className="block">
                <span className="font-mono text-[11px] text-ink">{new Date(c.t).toLocaleTimeString()}</span> {c.actor}: {c.reason}
              </span>
            ))}
      </dd>
    </dl>
  );
}

function ScoreRow({ selected, onSelect }: { selected: boolean; onSelect: () => void }) {
  return (
    <li className="list-none">
      <button
        type="button"
        aria-pressed={selected}
        onClick={onSelect}
        className={`flex w-full items-center gap-2 rounded-2xl px-3.5 py-2.5 text-left transition-[box-shadow,background-color] duration-150 ${
          selected ? "bg-cream ring-2 ring-mutedTeal/70" : "bg-white/40 ring-1 ring-paleWater hover:bg-white/60"
        }`}
      >
        <Layers aria-hidden className="h-4 w-4 text-mutedTeal" />
        <span className="font-mono text-[13px] font-medium text-slate">score</span>
        <span className="text-[12px] text-ink">combined, overrides applied</span>
      </button>
    </li>
  );
}

export interface LayerListProps {
  /** The full spec (authority view), or null in the public view. */
  spec: AppSpec | null;
  /** Layer ids the server sent on /map for this key. */
  visibleIds: string[];
  weights: WeightsView | null;
  trace: TraceEntry[];
  changes: Change[];
  selectedLayerId: string | null;
  /** The layer the map actually draws (score, or the first public layer when score is private). */
  shownId: string | null;
  onSelectLayer: (id: string | null) => void;
  /** GET /sources/status rows (authority view), for each row's freshness; null when unknown. */
  sources?: SourceStatus[] | null;
}

export function LayerList({ spec, visibleIds, weights, trace, changes, selectedLayerId, shownId, onSelectLayer, sources }: LayerListProps) {
  const now = useNow(5000);
  const srcById = new Map((sources ?? []).map((r) => [r.input_id, r]));
  const catalog = useOperatorCatalog();
  const layers = useMemo(() => (spec ? specLayers(spec) : []), [spec]);
  const live = new Map((weights?.layers ?? []).map((l) => [l.layer_id, l]));
  const infoOf = (op: string) => catalog?.find((o) => o.name === op) ?? null;
  const weightOf = (l: SpecLayer) => live.get(l.layerId)?.weight ?? l.op.weight;
  const maxWeight = Math.max(0, ...layers.filter((l) => l.op.op !== "override").map(weightOf));
  // Real freshness from the layer's input (never a blanket "live"); pinned and override say so.
  const statusOf = (l: SpecLayer): { status: string; tone?: NonNullable<Parameters<typeof LayerRow>[0]["tone"]> } => {
    const src = srcById.get(l.input.id);
    const fresh = src ? describeSource(src, now) : null;
    if ((infoOf(l.op.op)?.role ?? (l.op.op === "override" ? "override" : "layer")) === "override")
      return fresh ? { status: `override · ${fresh.short}`, tone: fresh.tone === "error" ? "error" : "override" } : { status: "override", tone: "override" };
    if (live.get(l.layerId)?.pinned) return { status: fresh ? `pinned · ${fresh.short}` : "pinned", tone: fresh?.tone === "error" ? "error" : "pinned" };
    return fresh ? { status: fresh.short, tone: fresh.tone } : { status: "" };
  };
  const tierCounts = layers.reduce<Record<string, number>>((acc, l) => ({ ...acc, [l.input.tier]: (acc[l.input.tier] ?? 0) + 1 }), {});
  const showScore = spec ? true : visibleIds.includes("score");

  if (!spec) {
    const ids = visibleIds.filter((id) => id !== "score");
    return (
      <div className="space-y-2.5">
        <p className="text-[13px] text-ink">Public layers only. Unlock the authority view for tiers, weights and every layer.</p>
        <ul className="space-y-2 p-0">
          {showScore && <ScoreRow selected={shownId === "score"} onSelect={() => onSelectLayer(null)} />}
          {ids.map((id) => {
            const on = shownId === id;
            return (
              <li key={id} className="list-none">
                <button
                  type="button"
                  aria-pressed={on}
                  onClick={() => onSelectLayer(on ? null : id)}
                  className={`w-full rounded-2xl px-3.5 py-2.5 text-left font-mono text-[13px] text-slate transition-[box-shadow,background-color] duration-150 ${
                    on ? "bg-cream ring-2 ring-mutedTeal/70" : "bg-white/40 ring-1 ring-paleWater hover:bg-white/60"
                  }`}
                >
                  {id}
                </button>
              </li>
            );
          })}
          {!showScore && ids.length === 0 && <li className="list-none text-[13px] text-ink">No layer of this app is public.</li>}
        </ul>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-1.5">
        {(["locked", "stiff", "flexible"] as const)
          .filter((t) => tierCounts[t])
          .map((t) => (
            <span key={t} className="inline-flex items-center gap-1">
              <TierBadge tier={t} />
              <span className="font-mono text-[11.5px] text-ink">×{tierCounts[t]}</span>
            </span>
          ))}
      </div>
      <ul className="space-y-2.5 p-0">
        <ScoreRow selected={selectedLayerId === null} onSelect={() => onSelectLayer(null)} />
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
              {...statusOf(l)}
              selected={selected}
              onToggle={() => onSelectLayer(selected ? null : l.layerId)}
            >
              <LayerDetail
                layer={l}
                liveWeight={row ? row.weight : null}
                pinned={row ? row.pinned : null}
                trace={traceFor(trace, l.layerId)}
                changes={changes.filter((c) => c.target === l.layerId)}
              />
            </LayerRow>
          );
        })}
      </ul>
    </div>
  );
}
