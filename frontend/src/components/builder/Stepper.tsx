// The visible step track: 1 About · 2 Area · 3 Sources · 4 Review & compile. A step is a link once every
// step before it is complete, so the dev can jump back (or forward over finished work) but never skip ahead.
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
      <ol className="flex flex-wrap items-center gap-x-1 gap-y-2 text-sm">
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
              <span className={isCurrent ? "font-semibold text-slate" : "text-ink"}>{step.label}</span>
              {done && <span className="sr-only">(done)</span>}
            </>
          );
          return (
            <li key={step.id} className="flex items-center gap-1">
              {i > 0 && <span aria-hidden className="mx-1 h-px w-4 bg-inkSoft/40 sm:w-8" />}
              {isCurrent ? (
                <span aria-current="step" className="flex items-center gap-2 rounded-full px-1.5 py-1">
                  {body}
                </span>
              ) : reachable ? (
                <Link
                  to={`/new/${step.id}`}
                  className="flex items-center gap-2 rounded-full px-1.5 py-1 transition-colors duration-150 hover:bg-white/45"
                >
                  {body}
                </Link>
              ) : (
                <span className="flex cursor-not-allowed items-center gap-2 rounded-full px-1.5 py-1 opacity-60" aria-disabled>
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
