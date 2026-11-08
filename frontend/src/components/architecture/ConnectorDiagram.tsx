// The data-flow diagram for one AppSpec: sources -> operators -> layers -> weighted sum -> score, with
// authority overrides entering AFTER the sum. Everything drawn comes from the real spec and the public
// operator catalogue (GET /v1/operators); nothing is invented. Agents never appear here as writing the
// score: weight changes only ever reach a layer's weight through guarded, backtested tools.
import { motion } from "motion/react";
import { useEffect, useMemo, useState, type KeyboardEvent } from "react";
import { api } from "../../api/client";
import type { AppSpec, InputSpec, OperatorConfig, OperatorInfo, Tier, Visibility } from "../../api/types";
import { usePrefersReducedMotion } from "../../hooks/usePrefersReducedMotion";

export interface ConnectorDiagramProps {
  spec: AppSpec;
  selectedLayerId: string | null;
  onSelectLayer: (id: string | null) => void;
}

// ---------- model ----------

interface LayerPath {
  layerId: string;
  input: InputSpec;
  op: OperatorConfig;
  role: "layer" | "override";
  visibility: Visibility;
}

interface SourceRow {
  input: InputSpec;
  layers: LayerPath[];
}

const RESERVED = new Set(["op", "weight", "layer_id", "noise_floor"]);
const layerIdOf = (input: InputSpec, op: OperatorConfig) => op.layer_id ?? `${input.id}.${op.op}`;

function buildModel(spec: AppSpec, catalogue: OperatorInfo[] | null): SourceRow[] {
  const roleOf = (op: string): "layer" | "override" =>
    catalogue?.find((c) => c.name === op)?.role ?? (op === "override" ? "override" : "layer");
  const vis = new Map((spec.outputs?.layers ?? []).map((l) => [l.id, l.visibility ?? "authority"] as const));
  const rows: SourceRow[] = (spec.inputs ?? []).map((input) => {
    const layers = (input.operators ?? []).map<LayerPath>((op) => {
      const layerId = layerIdOf(input, op);
      return { layerId, input, op, role: roleOf(op.op), visibility: vis.get(layerId) ?? "authority" };
    });
    // weighted layers above overrides inside one source, so its wires fan without crossing
    layers.sort((a, b) => Number(a.role === "override") - Number(b.role === "override"));
    return { input, layers };
  });
  // sources that only feed overrides go to the bottom, next to where overrides join (after the sum)
  const onlyOverride = (r: SourceRow) => r.layers.length > 0 && r.layers.every((l) => l.role === "override");
  return rows.map((r, i) => ({ r, i })).sort((a, b) => Number(onlyOverride(a.r)) - Number(onlyOverride(b.r)) || a.i - b.i).map((x) => x.r);
}

// ---------- geometry (viewBox units) ----------

const VB_W = 1040;
const TOP = 48;
const ROW = 74;
const GAP = 22; // between weighted rows and override rows
const BOTTOM = 26;
const SRC = { x: 8, w: 200, h: 52 };
const OP = { x: 270, w: 132, h: 34 };
const LAY = { x: 466, w: 210, h: 52 };
const SIG = { cx: 772, r: 23 };
const MERGE_X = 850;
const SCORE = { x: 890, w: 146, h: 62 };

/** A soft cubic wire. Near-level wires droop a little, like a slack thread, so nothing looks ruled. */
function bezier(x1: number, y1: number, x2: number, y2: number, bend = 0.5, sag = 5): string {
  const dx = (x2 - x1) * bend;
  const level = Math.abs(y2 - y1) < 12;
  const s = level ? sag : 0;
  return `M${x1},${y1} C${x1 + dx},${y1 + (y2 - y1) * 0.06 + s} ${x2 - dx},${y2 + s} ${x2},${y2}`;
}

function clip(s: string, max: number): string {
  return s.length <= max ? s : `${s.slice(0, max - 1)}…`;
}

const fmtW = (w: number) => w.toFixed(2);

const TIER_STYLE: Record<Tier, { fill: string; text: string; label: string }> = {
  locked: { fill: "#35596B", text: "#F0EBDD", label: "locked" },
  stiff: { fill: "#A9BCA5", text: "#35596B", label: "stiff" },
  flexible: { fill: "#A9D2D5", text: "#35596B", label: "flexible" },
};

const TEAL = "#638F91";
const CORAL = "#C97F69";

// ---------- component ----------

export default function ConnectorDiagram({ spec, selectedLayerId, onSelectLayer }: ConnectorDiagramProps) {
  const reduced = usePrefersReducedMotion();
  const [catalogue, setCatalogue] = useState<OperatorInfo[] | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .operators()
      .then((c) => alive && setCatalogue(c))
      .catch(() => alive && setCatalogue([]));
    return () => {
      alive = false;
    };
  }, []);

  const rows = useMemo(() => buildModel(spec, catalogue), [spec, catalogue]);

  const geo = useMemo(() => {
    // one row per layer; a source without operators still takes a row so it is visible
    const weighted: { layer: LayerPath | null; src: SourceRow }[] = [];
    const overrides: { layer: LayerPath; src: SourceRow }[] = [];
    for (const r of rows) {
      if (r.layers.length === 0) weighted.push({ layer: null, src: r });
      for (const l of r.layers) (l.role === "override" ? overrides : weighted).push({ layer: l, src: r });
    }
    const yOf = new Map<string, number>(); // layerId -> y
    const emptyY = new Map<string, number>(); // inputId -> y (no operators)
    weighted.forEach((w, i) => {
      const y = TOP + i * ROW + ROW / 2;
      if (w.layer) yOf.set(w.layer.layerId, y);
      else emptyY.set(w.src.input.id, y);
    });
    const ovTop = TOP + weighted.length * ROW + (weighted.length && overrides.length ? GAP : 0);
    overrides.forEach((o, i) => yOf.set(o.layer.layerId, ovTop + i * ROW + ROW / 2));
    const srcY = new Map<string, number>();
    for (const r of rows) {
      const ys = r.layers.map((l) => yOf.get(l.layerId) ?? 0);
      srcY.set(r.input.id, ys.length ? ys.reduce((a, b) => a + b, 0) / ys.length : (emptyY.get(r.input.id) ?? TOP));
    }
    const wYs = weighted.filter((w) => w.layer).map((w) => yOf.get(w.layer!.layerId)!);
    const sigY = wYs.length ? (Math.min(...wYs) + Math.max(...wYs)) / 2 : TOP + ROW / 2;
    const height = Math.max(ovTop + overrides.length * ROW, TOP + weighted.length * ROW) + BOTTOM;
    return { yOf, srcY, sigY, height: Math.max(height, TOP + ROW + BOTTOM), hasOverrides: overrides.length > 0, hasWeighted: wYs.length > 0 };
  }, [rows]);

  const allLayers = rows.flatMap((r) => r.layers);
  // weighted wires land spread around the left of the Σ circle, in row order, so they never cross
  const weightedOrder = allLayers.filter((l) => l.role === "layer").map((l) => l.layerId);
  const landing = (layerId: string) => {
    const n = weightedOrder.length;
    const span = Math.min(110, 34 * (n - 1)) * (Math.PI / 180); // total fan angle
    const a = n <= 1 ? 0 : -span / 2 + (span * weightedOrder.indexOf(layerId)) / (n - 1);
    return { x: SIG.cx - (SIG.r + 1.5) * Math.cos(a), y: geo.sigY + (SIG.r + 1.5) * Math.sin(a) };
  };
  const selected = allLayers.find((l) => l.layerId === selectedLayerId) ?? null;
  const focus = selected !== null;
  const onPath = (l: LayerPath) => selected?.layerId === l.layerId;
  const srcOnPath = (r: SourceRow) => selected?.input.id === r.input.id;
  const toggle = (id: string | null) => onSelectLayer(id === selectedLayerId ? null : id);
  const key = (fn: () => void) => (e: KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fn();
    }
  };

  const dim = (on: boolean) => (focus && !on ? 0.22 : 1);
  const colDelay = (c: number) => (reduced ? 0 : 0.12 + c * 0.12);

  const wire = (id: string, d: string, on: boolean, opts: { color?: string; width?: number; col: number }) => {
    const color = opts.color ?? TEAL;
    const marker = color === CORAL ? "cd-arrow-coral" : on && focus ? "cd-arrow-strong" : "cd-arrow";
    return (
      <g key={id} style={{ opacity: dim(on), transition: "opacity 300ms ease" }}>
        <motion.path
          d={d}
          fill="none"
          stroke={color}
          strokeOpacity={on && focus ? 0.95 : 0.55}
          strokeWidth={(opts.width ?? 1.3) + (on && focus ? 0.6 : 0)}
          strokeLinecap="round"
          markerEnd={`url(#${marker})`}
          initial={reduced ? false : { pathLength: 0, opacity: 0 }}
          animate={{ pathLength: 1, opacity: 1 }}
          transition={{ duration: reduced ? 0 : 0.7, delay: colDelay(opts.col), ease: "easeOut" }}
        />
        {on && focus && !reduced && (
          <path d={d} fill="none" stroke="#F0EBDD" strokeWidth={1.1} strokeLinecap="round" strokeDasharray="3 11" className="cd-flow" />
        )}
      </g>
    );
  };

  return (
    <div className="flex flex-col gap-4 xl:flex-row xl:items-start">
      <div className="min-w-0 flex-1 overflow-x-auto [scrollbar-width:thin]">
        <svg
          viewBox={`0 0 ${VB_W} ${geo.height}`}
          className="block h-auto w-full min-w-[620px]"
          role="img"
          aria-label={`Data flow: ${rows.length} sources into ${allLayers.length} layers, combined into the score`}
          onClick={(e) => {
            if (e.target === e.currentTarget) onSelectLayer(null);
          }}
        >
          <defs>
            {[
              ["cd-arrow", TEAL, 0.6],
              ["cd-arrow-strong", TEAL, 1],
              ["cd-arrow-coral", CORAL, 0.85],
            ].map(([id, c, o]) => (
              <marker key={id as string} id={id as string} viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="7" markerHeight="7"
                markerUnits="userSpaceOnUse" orient="auto-start-reverse">
                <path d="M1,1.2 L8.8,5 L1,8.8 Q2.6,5 1,1.2 Z" fill={c as string} fillOpacity={o as number} />
              </marker>
            ))}
            <filter id="cd-soft" x="-20%" y="-30%" width="140%" height="160%">
              <feDropShadow dx="0" dy="1.5" stdDeviation="2.2" floodColor="#35596B" floodOpacity="0.10" />
            </filter>
            <radialGradient id="cd-sig" cx="40%" cy="35%" r="70%">
              <stop offset="0%" stopColor="#F0EBDD" />
              <stop offset="100%" stopColor="#BBDCE3" />
            </radialGradient>
            <style>{`.cd-flow{animation:cd-dash 1.4s linear infinite}@keyframes cd-dash{to{stroke-dashoffset:-28}}
              .cd-node{cursor:pointer;outline:none}.cd-node:focus-visible rect,.cd-node:focus-visible circle{stroke:#35596B;stroke-width:1.6}`}</style>
          </defs>

          {/* column captions */}
          {[
            [SRC.x + 4, "sources"],
            [OP.x + 4, "operators"],
            [LAY.x + 4, "layers · weight"],
            [SIG.cx - 15, "sum"],
            [SCORE.x + 4, "score"],
          ].map(([x, t]) => (
            <text key={t as string} x={x as number} y={20} fontFamily="IBM Plex Mono, monospace" fontSize="11" letterSpacing="1.3" fill="#8199A0">
              {(t as string).toUpperCase()}
            </text>
          ))}

          {/* wires: source -> operator -> layer */}
          {rows.map((r) =>
            r.layers.map((l) => {
              const ys = geo.srcY.get(r.input.id)!;
              const yl = geo.yOf.get(l.layerId)!;
              const ov = l.role === "override";
              return (
                <g key={`w-${l.layerId}`}>
                  {wire(`so-${l.layerId}`, bezier(SRC.x + SRC.w, ys, OP.x - 2, yl), onPath(l), { col: 0, color: ov ? CORAL : TEAL })}
                  {wire(`ol-${l.layerId}`, bezier(OP.x + OP.w, yl, LAY.x - 2, yl), onPath(l), { col: 1, color: ov ? CORAL : TEAL })}
                </g>
              );
            }),
          )}

          {/* wires: layer -> sum (thickness ~ weight), override -> after the sum */}
          {allLayers.map((l) => {
            const yl = geo.yOf.get(l.layerId)!;
            if (l.role === "override") {
              const d = `M${LAY.x + LAY.w},${yl} C${LAY.x + LAY.w + 110},${yl} ${MERGE_X},${yl - (yl - geo.sigY) * 0.35} ${MERGE_X},${geo.sigY + 9}`;
              return wire(`lo-${l.layerId}`, d, onPath(l), { col: 2, color: CORAL });
            }
            const end = landing(l.layerId);
            return wire(`ls-${l.layerId}`, bezier(LAY.x + LAY.w, yl, end.x, end.y, 0.6), onPath(l), {
              col: 2,
              width: 0.9 + Math.min(1, Math.max(0, l.op.weight)) * 2.6,
            });
          })}

          {/* sum -> (overrides) -> score */}
          {geo.hasWeighted &&
            wire("sig-out", `M${SIG.cx + SIG.r},${geo.sigY} L${geo.hasOverrides ? MERGE_X - 8 : SCORE.x - 2},${geo.sigY}`,
              selected?.role === "layer", { col: 3, width: 1.8 })}
          {geo.hasOverrides &&
            wire("merge-out", `M${MERGE_X + 8},${geo.sigY} L${SCORE.x - 2},${geo.sigY}`, focus, { col: 3, width: 1.8 })}

          {/* source nodes */}
          {rows.map((r, i) => {
            const y = geo.srcY.get(r.input.id)! - SRC.h / 2;
            const conn = r.input.kind === "push" ? "ingest API" : r.input.connector ? `${r.input.connector.preset ?? r.input.connector.type}${r.input.connector.poll_s ? ` · ${r.input.connector.poll_s}s` : ""}` : "no connector";
            return (
              <motion.g
                key={`s-${r.input.id}`}
                className="cd-node"
                role="button"
                tabIndex={0}
                aria-label={`source ${r.input.id}, ${r.input.kind}`}
                onClick={() => toggle(srcOnPath(r) ? selectedLayerId : (r.layers[0]?.layerId ?? null))}
                onKeyDown={key(() => toggle(srcOnPath(r) ? selectedLayerId : (r.layers[0]?.layerId ?? null)))}
                initial={reduced ? false : { opacity: 0, x: -8 }}
                animate={{ opacity: dim(srcOnPath(r)), x: 0 }}
                transition={{ duration: reduced ? 0 : 0.45, delay: reduced ? 0 : 0.05 + i * 0.05 }}
              >
                <title>{`${r.input.id}: ${r.input.meaning}`}</title>
                <rect x={SRC.x} y={y} width={SRC.w} height={SRC.h} rx={16} fill="#F0EBDD" fillOpacity={0.92}
                  stroke={srcOnPath(r) ? TEAL : "#A9D2D5"} strokeWidth={srcOnPath(r) ? 1.4 : 1} filter="url(#cd-soft)" />
                <text x={SRC.x + 14} y={y + 21} fontFamily="IBM Plex Mono, monospace" fontSize="13.5" fontWeight={600} fill="#35596B">
                  {clip(r.input.id, 15)}
                </text>
                <text x={SRC.x + SRC.w - 12} y={y + 21} textAnchor="end" fontFamily="IBM Plex Mono, monospace" fontSize="10.5" fill="#8199A0">
                  {r.input.shape}
                </text>
                <rect x={SRC.x + 14} y={y + 30} width={38} height={15} rx={7.5}
                  fill={r.input.kind === "push" ? "#8DB9B7" : "#D7D1C0"} fillOpacity={r.input.kind === "push" ? 0.55 : 1} />
                <text x={SRC.x + 33} y={y + 41} textAnchor="middle" fontFamily="Nunito Sans, sans-serif" fontSize="10" fontWeight={700} fill="#35596B">
                  {r.input.kind}
                </text>
                <text x={SRC.x + 60} y={y + 41.5} fontFamily="IBM Plex Mono, monospace" fontSize="11" fill="#526F7C">
                  {clip(conn, 18)}
                </text>
              </motion.g>
            );
          })}

          {/* operator + layer nodes */}
          {allLayers.map((l, i) => {
            const yl = geo.yOf.get(l.layerId)!;
            const on = onPath(l);
            const ov = l.role === "override";
            const tier = TIER_STYLE[l.input.tier];
            const select = () => toggle(l.layerId);
            return (
              <g key={`n-${l.layerId}`}>
                <motion.g className="cd-node" role="button" tabIndex={0} aria-label={`operator ${l.op.op} for ${l.layerId}`}
                  onClick={select} onKeyDown={key(select)}
                  initial={reduced ? false : { opacity: 0 }} animate={{ opacity: dim(on) }}
                  transition={{ duration: reduced ? 0 : 0.4, delay: reduced ? 0 : 0.25 + i * 0.04 }}>
                  <rect x={OP.x} y={yl - OP.h / 2} width={OP.w} height={OP.h} rx={OP.h / 2}
                    fill={ov ? "#F3E2DA" : "#D5E8EB"} stroke={on ? (ov ? CORAL : TEAL) : ov ? "#E3BCAE" : "#A9D2D5"} strokeWidth={on ? 1.4 : 1} />
                  <text x={OP.x + OP.w / 2} y={yl + 4.5} textAnchor="middle" fontFamily="IBM Plex Mono, monospace" fontSize="12.5" fill="#35596B">
                    {clip(l.op.op, 16)}
                  </text>
                </motion.g>
                <motion.g className="cd-node" role="button" tabIndex={0} aria-label={`layer ${l.layerId}, weight ${fmtW(l.op.weight)}, ${l.input.tier}`}
                  onClick={select} onKeyDown={key(select)}
                  initial={reduced ? false : { opacity: 0, x: -6 }} animate={{ opacity: dim(on), x: 0 }}
                  transition={{ duration: reduced ? 0 : 0.45, delay: reduced ? 0 : 0.38 + i * 0.04 }}>
                  <title>{l.layerId}</title>
                  <rect x={LAY.x} y={yl - LAY.h / 2} width={LAY.w} height={LAY.h} rx={14} fill="#F0EBDD" fillOpacity={0.95}
                    stroke={on ? (ov ? CORAL : TEAL) : "#A9D2D5"} strokeWidth={on ? 1.4 : 1} filter="url(#cd-soft)" />
                  <text x={LAY.x + 14} y={yl - 4} fontFamily="IBM Plex Mono, monospace" fontSize="13" fontWeight={600} fill="#35596B">
                    {clip(l.layerId, 23)}
                  </text>
                  <text x={LAY.x + 14} y={yl + 16} fontFamily="IBM Plex Mono, monospace" fontSize="11.5" fill="#526F7C">
                    {ov ? "after sum" : `w ${fmtW(l.op.weight)}`}
                  </text>
                  <rect x={LAY.x + LAY.w - 74} y={yl + 4} width={62} height={16} rx={8} fill={tier.fill} fillOpacity={l.input.tier === "locked" ? 0.9 : 0.75} />
                  {l.input.tier === "locked" && (
                    <path transform={`translate(${LAY.x + LAY.w - 68},${yl + 7.6})`}
                      d="M1.6,4 V2.8 A2.2,2.2 0 0 1 6,2.8 V4 M0.8,4 H6.8 V8.6 H0.8 Z" fill="none" stroke={tier.text} strokeWidth="1" strokeLinejoin="round" />
                  )}
                  <text x={LAY.x + LAY.w - 43 + (l.input.tier === "locked" ? 4 : 0)} y={yl + 15.6} textAnchor="middle"
                    fontFamily="Nunito Sans, sans-serif" fontSize="10.5" fontWeight={700} fill={tier.text}>
                    {tier.label}
                  </text>
                </motion.g>
              </g>
            );
          })}

          {/* sum, merge and score */}
          {geo.hasWeighted && (
            <motion.g className="cd-node" role="button" tabIndex={0} aria-label="weighted sum; clear selection"
              onClick={() => onSelectLayer(null)} onKeyDown={key(() => onSelectLayer(null))}
              initial={reduced ? false : { opacity: 0, scale: 0.8 }} animate={{ opacity: focus && selected?.role !== "layer" ? 0.4 : 1, scale: 1 }}
              style={{ transformOrigin: `${SIG.cx}px ${geo.sigY}px` }} transition={{ duration: reduced ? 0 : 0.5, delay: colDelay(3) }}>
              <circle cx={SIG.cx} cy={geo.sigY} r={SIG.r} fill="url(#cd-sig)" stroke={TEAL} strokeOpacity={0.6} filter="url(#cd-soft)" />
              <text x={SIG.cx} y={geo.sigY + 6.5} textAnchor="middle" fontFamily="Nunito Sans, sans-serif" fontSize="19" fill="#35596B">
                Σ
              </text>
              <text x={SIG.cx} y={geo.sigY + SIG.r + 17} textAnchor="middle" fontFamily="IBM Plex Mono, monospace" fontSize="11" fill="#8199A0">
                wᵢ · norm
              </text>
            </motion.g>
          )}
          {geo.hasOverrides && (
            <g style={{ opacity: focus && selected?.role !== "override" ? 0.5 : 1, transition: "opacity 300ms" }}>
              <circle cx={MERGE_X} cy={geo.sigY} r={8} fill="#F3E2DA" stroke={CORAL} strokeWidth={1.2} />
              <text x={MERGE_X} y={geo.sigY - 16} textAnchor="middle" fontFamily="IBM Plex Mono, monospace" fontSize="11" fill={CORAL}>
                overrides
              </text>
            </g>
          )}
          <motion.g className="cd-node" role="button" tabIndex={0} aria-label="combined score; clear selection"
            onClick={() => onSelectLayer(null)} onKeyDown={key(() => onSelectLayer(null))}
            initial={reduced ? false : { opacity: 0, x: 8 }} animate={{ opacity: 1, x: 0 }}
            transition={{ duration: reduced ? 0 : 0.5, delay: colDelay(4) }}>
            <rect x={SCORE.x} y={geo.sigY - SCORE.h / 2} width={SCORE.w} height={SCORE.h} rx={22} fill="#35596B" fillOpacity={0.92} filter="url(#cd-soft)" />
            <text x={SCORE.x + 18} y={geo.sigY - 3} fontFamily="Nunito Sans, sans-serif" fontSize="16" fontWeight={700} fill="#F0EBDD">
              score
            </text>
            <text x={SCORE.x + 18} y={geo.sigY + 16} fontFamily="IBM Plex Mono, monospace" fontSize="11" fill="#BBDCE3">
              per cell · 0 … 1
            </text>
          </motion.g>
        </svg>
      </div>

      <ExplainCard spec={spec} selected={selected} catalogue={catalogue} counts={{ sources: rows.length, layers: allLayers.length }} />
    </div>
  );
}

// ---------- explanatory card (deterministic text from the real spec + catalogue) ----------

const TIER_NOTE: Record<Tier, string> = {
  locked: "Locked: no agent can change this weight.",
  stiff:
    "Stiff: the Critic may propose at most ±10% per hour, and a guarded tool applies it only after a held-out backtest decided by authority confirmation passes.",
  flexible:
    "Flexible: the Critic may propose up to ±25% per step; a guarded tool applies it only after a passing held-out backtest, and flexible weights renormalise to their original total.",
};

function ExplainCard({
  spec,
  selected,
  catalogue,
  counts,
}: {
  spec: AppSpec;
  selected: LayerPath | null;
  catalogue: OperatorInfo[] | null;
  counts: { sources: number; layers: number };
}) {
  const info = selected ? catalogue?.find((c) => c.name === selected.op.op) : undefined;
  const params = selected
    ? Object.entries(selected.op).filter(([k, v]) => !RESERVED.has(k) && v !== null && v !== undefined)
    : [];
  const overrideRadius = (() => {
    if (!selected || selected.role !== "override") return null;
    const own = selected.op["radius_m"];
    if (typeof own === "number") return own;
    const props = (info?.params as { properties?: Record<string, { default?: unknown }> } | undefined)?.properties;
    const d = props?.["radius_m"]?.default;
    return typeof d === "number" ? d : null;
  })();
  const weightedTotal = (spec.inputs ?? [])
    .flatMap((i) => i.operators ?? [])
    .filter((o) => (catalogue?.find((c) => c.name === o.op)?.role ?? (o.op === "override" ? "override" : "layer")) === "layer")
    .reduce((a, o) => a + o.weight, 0);

  return (
    <aside className="w-full shrink-0 rounded-2xl border border-softAqua/70 bg-cream/70 p-4 text-sm text-ink shadow-[0_2px_12px_rgba(53,89,107,0.06)] xl:w-72">
      {!selected ? (
        <div className="flex flex-col gap-2">
          <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-inkSoft">How the score is made</p>
          <p>
            {counts.sources} source{counts.sources === 1 ? "" : "s"} feed {counts.layers} layer{counts.layers === 1 ? "" : "s"}. Each operator turns its
            input into a grid layer; weighted layers are normalised and summed, then clamped.
          </p>
          <p>
            Authority overrides are applied <em>after</em> the sum and always win. Weights only change through guarded tools after a passing
            held-out backtest; nothing writes the score directly.
          </p>
          <p className="font-mono text-[11px] text-inkSoft">Σ weights (weighted layers) = {weightedTotal.toFixed(2)} · select a node to trace it</p>
        </div>
      ) : (
        <div className="flex flex-col gap-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="break-all font-mono text-[13px] font-semibold text-slate">{selected.layerId}</span>
            <span
              className="rounded-full px-2 py-0.5 text-[10px] font-bold"
              style={{ background: TIER_STYLE[selected.input.tier].fill, color: TIER_STYLE[selected.input.tier].text }}
            >
              {selected.input.tier}
            </span>
            <span className="rounded-full bg-washedSky px-2 py-0.5 font-mono text-[10px] text-ink">{selected.visibility}</span>
          </div>
          <p>
            <span className="text-inkSoft">From </span>
            <span className="font-mono text-slate">{selected.input.id}</span>
            <span className="text-inkSoft">
              {" "}
              ({selected.input.kind} · {selected.input.shape}
              {selected.input.kind === "pull" && selected.input.connector ? ` · ${selected.input.connector.preset ?? selected.input.connector.type}` : ""})
            </span>
            {selected.input.meaning ? <span className="mt-1 block italic text-ink">“{selected.input.meaning}”</span> : null}
          </p>
          <p>
            <span className="text-inkSoft">Operator </span>
            <span className="font-mono text-slate">{selected.op.op}</span>
            <span className="block text-ink">
              {info?.description ?? (catalogue === null ? "Loading the operator description…" : "No catalogue description for this operator.")}
            </span>
          </p>
          {params.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {params.map(([k, v]) => (
                <span key={k} className="rounded-lg bg-washedSky/80 px-2 py-0.5 font-mono text-[11px] text-ink">
                  {k} {typeof v === "object" ? JSON.stringify(v) : String(v)}
                </span>
              ))}
            </div>
          )}
          <p className="border-t border-softAqua/60 pt-2">
            {selected.role === "override" ? (
              <>
                Applied after the weighted sum:{" "}
                {overrideRadius !== null ? `within ${overrideRadius} m of each active point` : "within its radius"}, score = max(score, 1.0) until
                cleared. Overrides always win.
              </>
            ) : (
              <>
                Normalised as layer ÷ max(p99, noise floor{typeof selected.op.noise_floor === "number" ? ` ${selected.op.noise_floor}` : ""}), then
                added with weight <span className="font-mono text-slate">{fmtW(selected.op.weight)}</span>.
              </>
            )}
          </p>
          <p className="text-[12.5px] text-inkSoft">{TIER_NOTE[selected.input.tier]}</p>
        </div>
      )}
    </aside>
  );
}
