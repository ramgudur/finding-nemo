// One operator layer in the stack: id, operator, weight, tier, visibility, status and a deterministic description.
// Click toggles selection. A locked weight is plain text beside a lock, never an editable-looking control.
import { Eye, EyeOff, Lock } from "lucide-react";
import { motion } from "motion/react";
import type { ReactNode } from "react";
import type { OperatorInfo } from "../../api/types";
import { LayerDescription } from "./LayerDescription";
import { fmtWeight, type SpecLayer } from "./specLayers";
import { TIER_SURFACE, TierBadge } from "./TierBadge";

export interface LayerRowProps {
  layer: SpecLayer;
  info?: OperatorInfo | null;
  /** The live weight when known, else the spec's. */
  weight: number;
  /** Largest weight in the stack, for the bar. */
  maxWeight: number;
  status: string;
  selected: boolean;
  onToggle: () => void;
  /** Extra detail shown under a selected row. */
  children?: ReactNode;
}

const STATUS_DOT: Record<string, string> = {
  live: "bg-seaGlass",
  pinned: "bg-coral",
  proposed: "bg-inkSoft/60",
  override: "bg-slate/70",
};

export function LayerRow({ layer, info, weight, maxWeight, status, selected, onToggle, children }: LayerRowProps) {
  const { layerId, input, op, visibility } = layer;
  const tier = input.tier;
  const isOverride = info ? info.role === "override" : op.op === "override";
  const pct = isOverride ? 100 : maxWeight > 0 ? Math.max(3, Math.min(100, (weight / maxWeight) * 100)) : 0;

  return (
    <motion.li layout="position" variants={rowVariants} className="relative list-none">
      <button
        type="button"
        onClick={onToggle}
        aria-pressed={selected}
        className={`group relative w-full overflow-hidden rounded-2xl border px-3.5 py-3 text-left shadow-[0_1px_2px_rgba(53,89,107,0.06)] transition-[box-shadow,transform] duration-200 hover:-translate-y-px hover:shadow-[0_6px_18px_-8px_rgba(53,89,107,0.35)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mutedTeal/60 ${TIER_SURFACE[tier]}`}
      >
        {selected && (
          <motion.span
            layoutId="layer-selection"
            aria-hidden
            className="pointer-events-none absolute inset-0 rounded-2xl ring-2 ring-mutedTeal/70 [background:linear-gradient(100deg,rgba(240,235,221,0.75),rgba(240,235,221,0.15))]"
            transition={{ type: "spring", stiffness: 420, damping: 36 }}
          />
        )}
        <span className="relative flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="min-w-0 break-all font-mono text-[13px] font-medium text-slate">{layerId}</span>
          <TierBadge tier={tier} />
          <span className="ml-auto inline-flex items-center gap-1.5 text-[11px] text-inkSoft">
            <span aria-hidden className={`h-1.5 w-1.5 rounded-full ${STATUS_DOT[status] ?? "bg-inkSoft/60"}`} />
            {status}
          </span>
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
            <span className="text-inkSoft">{isOverride ? "after Σ" : "w"}</span>
            <span className="font-mono tabular-nums text-slate">{isOverride ? "override" : fmtWeight(weight)}</span>
          </span>
        </span>

        <span aria-hidden className="relative mt-2 block h-1 overflow-hidden rounded-full bg-white/50">
          <span
            className={`block h-full rounded-full transition-[width] duration-500 ${
              tier === "locked"
                ? "bg-[repeating-linear-gradient(135deg,#A79F88_0_4px,#C9C2AE_4px_8px)]"
                : tier === "stiff"
                  ? "bg-seaGlass"
                  : "bg-[#7FC3C8]"
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
          transition={{ duration: 0.22 }}
          className="mx-2 -mt-2 rounded-b-2xl border border-t-0 border-paleWater bg-cream/60 px-3.5 pb-3 pt-4"
        >
          {children}
        </motion.div>
      )}
    </motion.li>
  );
}

export const rowVariants = {
  hidden: { opacity: 0, y: 8 },
  show: { opacity: 1, y: 0, transition: { duration: 0.32, ease: [0.22, 1, 0.36, 1] } },
};

export default LayerRow;
