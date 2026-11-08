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
      title={kind === "push" ? "Your app sends this to the platform" : "The platform fetches this on a schedule"}
      className={`inline-flex shrink-0 items-center gap-1 rounded-full font-semibold ${
        small ? "px-1.5 py-px text-[11px]" : "px-2 py-0.5 text-xs"
      } ${kind === "push" ? "bg-primary/15 text-primary" : "bg-mutedTeal/20 text-[#3C6668]"}`}
    >
      <Icon size={small ? 10 : 11} strokeWidth={2.4} aria-hidden />
      {kind === "push" ? "sent by your app" : "fetched"}
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
      transition={{ duration: 0.22, delay: reduce ? 0 : index * 0.04, ease: [0.22, 1, 0.36, 1] }}
      whileHover={reduce ? undefined : { y: -2 }}
      className={`suggestion-card flex w-full flex-col gap-1.5 rounded-[14px] px-4 py-3 text-left transition-[background-color,box-shadow] duration-200 ${
        selected
          ? "bg-[#FBFAF6] shadow-[inset_0_0_0_2px_#35596B,0_10px_26px_-18px_rgba(53,89,107,0.7)]"
          : "bg-white/45 shadow-[inset_0_0_0_1px_rgba(99,143,145,0.3)] hover:bg-white/75 hover:shadow-[inset_0_0_0_1px_rgba(99,143,145,0.55),0_10px_26px_-20px_rgba(53,89,107,0.6)]"
      }`}
    >
      <span className="flex items-center gap-2">
        <span className="text-[15px] font-bold text-slate">{option.label}</span>
        <span className="ml-auto">
          <KindBadge kind={kind} />
        </span>
      </span>
      <span className="text-[13px] leading-snug text-ink">{option.why}</span>
      {preset && <span className="text-[11.5px] text-ink">ready-made setup: <span className="font-mono">{preset}</span></span>}
      {selected && kind === "pull" && <span className="text-[11.5px] font-semibold text-slate">Settings are open below. Choose it again to close them.</span>}
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
        <span className="ml-auto text-[11px] font-semibold text-ink">coming later</span>
      </span>
      <span className="text-[13px] leading-snug text-ink">Receiving by text message isn't available yet.</span>
    </div>
  );
}
