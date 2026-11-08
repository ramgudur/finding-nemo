// One operator layer in the stack: id, operator, weight, tier, visibility, status and a deterministic description.
// Click toggles selection. A locked weight is plain text beside a lock, never an editable-looking control.
import { Eye, EyeOff, Lock, MoveRight } from "lucide-react";
import { motion } from "motion/react";
import type { ReactNode } from "react";
import type { OperatorInfo } from "../../api/types";
import type { VectorState } from "../../hooks/useVectorReadings";
import { LayerDescription } from "./LayerDescription";
import { fmtWeight, type SpecLayer } from "./specLayers";
import { TIER_SURFACE, TierBadge } from "./TierBadge";
import { VectorRowSummary } from "./VectorFieldBits";

export interface LayerRowProps {
  layer: SpecLayer;
  info?: OperatorInfo | null;
  /** The live weight when known, else the spec's. */
  weight: number;
  /** Largest weight in the stack, for the bar. */
  maxWeight: number;
  /** Short freshness or role text ("10 s ago", "no data yet", "override"); empty hides it. */
  status: string;
  /** Picks the dot colour; error text is always written out as well. */
  tone?: "fresh" | "quiet" | "waiting" | "empty" | "error" | "pinned" | "override";
  selected: boolean;
  onToggle: () => void;
  /** Set when this layer is a vector field (speed + direction): its latest reading and what it moves. */
  vector?: VectorState | null;
  /** Set when a vector field carries this layer before scoring ("Shifted ~1.9 km SW along …"). */
  shiftNote?: string | null;
  /** Extra detail shown under a selected row. */
  children?: ReactNode;
}

const STATUS_DOT: Record<NonNullable<LayerRowProps["tone"]>, string> = {
  fresh: "bg-mutedTeal",
  quiet: "bg-seaGlass/70",
  waiting: "bg-softStone",
  empty: "bg-inkSoft/45",
  error: "bg-coral",
  pinned: "bg-coral",
  override: "bg-slate/70",
};

export function LayerRow({ layer, info, weight, maxWeight, status, tone, selected, onToggle, vector, shiftNote, children }: LayerRowProps) {
  const { layerId, input, op, visibility } = layer;
  const tier = input.tier;
  const isOverride = info ? info.role === "override" : op.op === "override";
  // weight 0 and a vector field: it only moves another layer, so the bar and weight would read "empty"
  const shiftOnly = !isOverride && !!vector?.layer.shiftOnly && weight === 0;
  const pct = isOverride ? 100 : maxWeight > 0 ? Math.max(3, Math.min(100, (weight / maxWeight) * 100)) : 0;

  return (
    <motion.li layout="position" variants={rowVariants} className="relative list-none">
      <button
        type="button"
        onClick={onToggle}
        aria-pressed={selected}
        className={`wp-press group relative w-full overflow-hidden rounded-xl border px-3.5 py-3 text-left hover:shadow-[var(--shadow-1)] ${TIER_SURFACE[tier]}`}
      >
        {selected && (
          <motion.span
            layoutId="layer-selection"
            aria-hidden
            className="pointer-events-none absolute inset-0 rounded-xl bg-deep/[0.06] ring-[1.5px] ring-inset ring-deep/50"
            transition={{ type: "spring", stiffness: 420, damping: 36 }}
          />
        )}
        <span className="relative flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="min-w-0 break-all font-mono text-[13.5px] font-semibold text-slate">{layerId}</span>
          <TierBadge tier={tier} />
          {status && (
            <span className={`ml-auto inline-flex items-center gap-1.5 text-[11px] ${tone === "error" ? "font-semibold text-danger" : "text-inkSoft"}`} title="When this layer's input last received data">
              <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${tone ? STATUS_DOT[tone] : "bg-inkSoft/60"}`} />
              {status}
            </span>
          )}
        </span>

        <span className="relative mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-ink">
          <span>
            <span className="text-inkSoft">op </span>
            <span className="font-mono">{op.op}</span>
          </span>
          <span className="inline-flex items-center gap-1" title={visibility === "public" ? "on the public map" : "authority view only"}>
            {visibility === "public" ? <Eye aria-hidden className="h-3 w-3" /> : <EyeOff aria-hidden className="h-3 w-3" />}
            {visibility}
          </span>
          <span className="ml-auto inline-flex items-center gap-1.5">
            {tier === "locked" && <Lock aria-label="locked weight" className="h-3 w-3 text-slate/70" />}
            <span className="text-inkSoft">{isOverride ? "after Σ" : shiftOnly ? "before Σ" : "w"}</span>
            <span className="font-mono text-[14px] font-semibold tabular-nums text-slate">{isOverride ? "override" : shiftOnly ? "moves a layer" : fmtWeight(weight)}</span>
          </span>
        </span>

        {vector && (
          <span className="relative mt-2 block">
            <VectorRowSummary state={vector} />
          </span>
        )}
        {shiftNote && (
          <span className="relative mt-1.5 flex items-center gap-1 text-[11.5px] font-medium text-primary">
            <MoveRight aria-hidden className="h-3 w-3 shrink-0" />
            {shiftNote}
          </span>
        )}

        <span aria-hidden className={`relative mt-2 h-1 ${shiftOnly ? "hidden" : "block"} overflow-hidden rounded-full bg-slate/10`}>
          <span
            className={`block h-full rounded-full transition-[width] duration-500 ${
              tier === "locked"
                ? "bg-[repeating-linear-gradient(90deg,rgb(var(--c-tier-locked)/0.45)_0_2px,transparent_2px_6px)]"
                : tier === "stiff"
                  ? "bg-tierStiff"
                  : "bg-tierFlex"
            }`}
            style={{ width: `${pct}%` }}
          />
        </span>

        <span className="relative mt-2 block">
          <LayerDescription input={input} op={op} info={info} showMeaning={selected} />
        </span>
      </button>
      {selected && children && (
        <motion.div
          initial={{ opacity: 0, y: -4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.18 }}
          className="mx-2 -mt-2 rounded-b-xl bg-sheet2 px-3.5 pb-3 pt-4"
        >
          {children}
        </motion.div>
      )}
    </motion.li>
  );
}

export const rowVariants = {
  hidden: { opacity: 0, y: 8 },
  show: { opacity: 1, y: 0, transition: { duration: 0.2, ease: [0.16, 1, 0.3, 1] } },
};

export default LayerRow;
