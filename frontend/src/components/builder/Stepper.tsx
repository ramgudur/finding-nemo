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

/** The logo's double wave, as the path between two steps: solid once walked, dotted ahead. */
function StepWave({ on }: { on: boolean }) {
  return (
    <svg aria-hidden viewBox="0 0 40 10" className="h-2.5 w-4 shrink-0 fill-none sm:mx-1 sm:w-10">
      <path
        d="M2 5C7 1 12 1 17 5S27 9 32 5 37 2 38 4"
        strokeWidth={1.8}
        strokeLinecap="round"
        className={on ? "stroke-deep/55" : "stroke-inkSoft/45"}
        strokeDasharray={on ? undefined : "2 4"}
      />
    </svg>
  );
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
                  ? "bg-deep text-cream shadow-[0_0_0_4px_rgb(var(--c-deep)/0.14)]"
                  : done
                    ? "bg-deep/10 text-deep"
                    : "text-inkSoft ring-[1.5px] ring-inset ring-slate/25"
              }`}
            >
              {done ? <Check size={13} strokeWidth={2.6} aria-hidden /> : i + 1}
            </span>
          );
          const body = (
            <>
              {dot}
              <span className={isCurrent ? "whitespace-nowrap font-extrabold text-slate" : `font-semibold max-sm:sr-only ${done ? "text-ink" : "text-inkSoft"}`}>{step.label}</span>
              {done && <span className="sr-only">(done)</span>}
            </>
          );
          return (
            <li key={step.id} className="flex items-center gap-1">
              {i > 0 && <StepWave on={complete[STEPS[i - 1]!.id] && i <= currentIndex} />}
              {isCurrent ? (
                <span aria-current="step" className="tap flex items-center gap-2 rounded-full px-1.5 py-1">
                  {body}
                </span>
              ) : reachable ? (
                <Link
                  to={`/new/${step.id}`}
                  className="tap wp-press flex items-center justify-center gap-2 rounded-full px-1.5 py-1 hover:bg-slate/[0.06]"
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
