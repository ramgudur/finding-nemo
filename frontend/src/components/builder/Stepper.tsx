// The visible step track: 1 About · 2 Area · 3 Sources · 4 Review & compile. A step is a link once every
// step before it is complete, so the dev can jump back (or forward over finished work) but never skip ahead.
// On phones it is compact: numbered dots (44 px targets) with only the current step's label shown.
import { Check } from "lucide-react";
import { Link } from "react-router-dom";
import { STEPS } from "../sources/sourceUtils";
import type { StepId } from "../sources/sourceUtils";

interface Props {
  current: StepId;
  /** Per step: has no problems. */
  complete: Record<StepId, boolean>;
}

export function Stepper({ current, complete }: Props) {
  const currentIndex = STEPS.findIndex((s) => s.id === current);
  return (
    <nav aria-label="Build steps">
      <ol className="flex items-center gap-x-1 gap-y-2 text-sm sm:flex-wrap">
        {STEPS.map((step, i) => {
          const reachable = STEPS.slice(0, i).every((s) => complete[s.id]);
          const isCurrent = step.id === current;
          const done = complete[step.id] && i < currentIndex;
          const dot = (
            <span
              className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold transition-colors duration-200 ${
                isCurrent
                  ? "bg-primary text-cream"
                  : done
                    ? "bg-mutedTeal/20 text-slate ring-1 ring-mutedTeal/50"
                    : "bg-white/50 text-ink ring-1 ring-inkSoft/30"
              }`}
            >
              {done ? <Check size={13} strokeWidth={2.6} aria-hidden /> : i + 1}
            </span>
          );
          const body = (
            <>
              {dot}
              <span className={isCurrent ? "whitespace-nowrap font-semibold text-slate" : "text-ink max-sm:sr-only"}>{step.label}</span>
              {done && <span className="sr-only">(done)</span>}
            </>
          );
          return (
            <li key={step.id} className="flex items-center gap-1">
              {i > 0 && <span aria-hidden className="h-px w-3 bg-inkSoft/40 sm:mx-1 sm:w-8" />}
              {isCurrent ? (
                <span aria-current="step" className="tap flex items-center gap-2 rounded-full px-1.5 py-1">
                  {body}
                </span>
              ) : reachable ? (
                <Link
                  to={`/new/${step.id}`}
                  className="tap flex items-center justify-center gap-2 rounded-full px-1.5 py-1 transition-colors duration-150 hover:bg-white/45"
                >
                  {body}
                </Link>
              ) : (
                <span className="tap flex cursor-not-allowed items-center justify-center gap-2 rounded-full px-1.5 py-1 opacity-60" aria-disabled>
                  {body}
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
