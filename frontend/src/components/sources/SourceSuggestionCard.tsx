// One way to connect a source. Push and pull read differently at a glance: push is the dev's app
// sending to us (an arrow in), pull is the platform polling a connector (a refresh loop).
import { ArrowDownToLine, MessageSquare, RefreshCw } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import type { SourceOption } from "../../api/types";
import { OPTION_KIND, presetOf } from "./sourceUtils";

export function KindBadge({ kind, small }: { kind: "push" | "pull"; small?: boolean }) {
  const Icon = kind === "push" ? ArrowDownToLine : RefreshCw;
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 rounded-full font-mono uppercase tracking-wider ${
        small ? "px-1.5 py-px text-[9.5px]" : "px-2 py-0.5 text-[10.5px]"
      } ${kind === "push" ? "bg-[#4F80B0]/15 text-[#3F6E9C]" : "bg-mutedTeal/15 text-mutedTeal"}`}
    >
      <Icon size={small ? 9 : 11} strokeWidth={2.4} />
      {kind}
    </span>
  );
}

interface Props {
  option: SourceOption;
  selected: boolean;
  index: number;
  onPick: () => void;
}

export function SourceSuggestionCard({ option, selected, index, onPick }: Props) {
  const kind = OPTION_KIND[option.type];
  const preset = presetOf(option);
  const reduce = useReducedMotion();
  return (
    <motion.button
      type="button"
      onClick={onPick}
      aria-pressed={selected}
      initial={reduce ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, delay: reduce ? 0 : index * 0.05, ease: [0.22, 1, 0.36, 1] }}
      whileHover={reduce ? undefined : { y: -2 }}
      className={`suggestion-card flex w-full flex-col gap-1.5 rounded-2xl border px-4 py-3 text-left transition-[background-color,border-color,box-shadow] duration-200 ${
        selected
          ? "border-mutedTeal/60 bg-white/75 shadow-[0_10px_30px_-18px_rgba(53,89,107,0.7)]"
          : "border-white/70 bg-cream/55 hover:bg-cream/80 hover:shadow-[0_10px_30px_-20px_rgba(53,89,107,0.6)]"
      }`}
    >
      <span className="flex items-center gap-2">
        <span className="text-[15px] font-semibold text-slate">{option.label}</span>
        <span className="ml-auto">
          <KindBadge kind={kind} />
        </span>
      </span>
      <span className="text-[13px] leading-snug text-ink">{option.why}</span>
      {preset && <span className="font-mono text-[11px] text-inkSoft">preset · {preset}</span>}
    </motion.button>
  );
}

/** SMS is not a platform source type yet; shown so the dev knows it's planned, never selectable. */
export function SmsCardDisabled() {
  return (
    <div
      aria-disabled
      className="flex w-full cursor-not-allowed flex-col gap-1.5 rounded-2xl border border-dashed border-inkSoft/30 bg-white/15 px-4 py-3 text-left opacity-60"
    >
      <span className="flex items-center gap-2">
        <MessageSquare size={14} className="text-inkSoft" />
        <span className="text-[15px] font-semibold text-ink">SMS / phone</span>
        <span className="ml-auto font-mono text-[10.5px] uppercase tracking-wider text-inkSoft">later</span>
      </span>
      <span className="text-[13px] leading-snug text-inkSoft">Not available yet.</span>
    </div>
  );
}
