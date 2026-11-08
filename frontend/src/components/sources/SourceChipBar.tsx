// Persistent bottom bar: committed sources as chips, a count, and Continue (disabled until valid).
import { ArrowRight, LoaderCircle } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import type { CompileInput } from "../../api/types";
import { SourceChip } from "./SourceChip";

interface Props {
  sources: CompileInput[];
  problems: string[];
  compiling: boolean;
  progress: string;
  cooldownS: number;
  onRename: (index: number, id: string) => void;
  onRemove: (index: number) => void;
  onContinue: () => void;
}

export function SourceChipBar({ sources, problems, compiling, progress, cooldownS, onRename, onRemove, onContinue }: Props) {
  const reduce = useReducedMotion();
  const ids = sources.map((s) => s.id);
  const disabled = compiling || problems.length > 0 || cooldownS > 0;
  const status = compiling
    ? progress
    : cooldownS > 0
      ? `model budget busy · retry in ${cooldownS}s`
      : problems[0] ?? "Ready to compile. The app is not activated until you confirm.";

  const continueButton = (
    <motion.button
      type="button"
      disabled={disabled}
      onClick={onContinue}
      whileHover={disabled || reduce ? undefined : { y: -2 }}
      whileTap={disabled || reduce ? undefined : { scale: 0.97 }}
      transition={{ type: "spring", stiffness: 400, damping: 26 }}
      className="flex shrink-0 items-center gap-2 self-start rounded-full border border-white/60 bg-[#4F80B0] px-5 py-2.5 text-sm font-semibold text-cream shadow-[0_8px_22px_-10px_rgba(47,88,114,0.9)] transition-[background-color,opacity] hover:bg-[#46739F] disabled:cursor-not-allowed disabled:bg-inkSoft/50 disabled:shadow-none"
    >
      {compiling ? <LoaderCircle size={16} className="animate-spin" /> : null}
      {compiling ? "Compiling" : "Continue"}
      {!compiling && <ArrowRight size={16} />}
    </motion.button>
  );

  return (
    <div className="fixed inset-x-0 bottom-0 z-20 px-3 pb-3 sm:px-6 sm:pb-5">
      <div className="mx-auto flex max-w-5xl flex-col gap-1.5 rounded-[26px] border border-white/70 bg-cream/80 px-4 py-3 shadow-[0_18px_50px_-24px_rgba(53,89,107,0.7)] backdrop-blur-md sm:px-5">
        <div className="flex items-center gap-3">
          <span
            className="shrink-0 self-start rounded-full bg-paleWater/70 px-2.5 py-1 font-mono text-xs text-slate sm:mt-0.5"
            aria-live="polite"
          >
            {sources.length} source{sources.length === 1 ? "" : "s"}
          </span>
          <div className="min-w-0 flex-1">
            {sources.length === 0 ? (
              <span className="block truncate text-sm italic text-inkSoft">Sources you add appear here.</span>
            ) : (
              <ul className="chip-scroll flex max-h-[5.5rem] flex-wrap items-center gap-2 overflow-y-auto overflow-x-hidden">
                <AnimatePresence initial={false} mode="popLayout">
                  {sources.map((s, i) => (
                    <SourceChip
                      key={`${s.meaning}#${sources.slice(0, i).filter((x) => x.meaning === s.meaning).length}`}
                      source={s}
                      duplicate={ids.indexOf(s.id) !== i}
                      disabled={compiling}
                      onRename={(id) => onRename(i, id)}
                      onRemove={() => onRemove(i)}
                    />
                  ))}
                </AnimatePresence>
              </ul>
            )}
          </div>
          <div className="hidden sm:block">{continueButton}</div>
        </div>
        <div className="flex items-center gap-3">
          <p
            className={`min-w-0 flex-1 truncate pl-1 text-xs ${
              compiling ? "font-mono text-ink" : problems.length ? "text-inkSoft" : "text-mutedTeal"
            }`}
            title={status}
            aria-live="polite"
          >
            {status}
          </p>
          <div className="sm:hidden">{continueButton}</div>
        </div>
      </div>
    </div>
  );
}
