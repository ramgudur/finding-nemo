// Persistent bottom bar for each step: Back on the left, what's still missing (or a ready line) in the middle,
// and the step's primary action on the right. The action stays disabled until the step is valid.
import { ArrowLeft, ArrowRight } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

interface Props {
  back?: { to: string; label: string };
  /** Shown left of the status line (e.g. "2 sources added"). */
  summary?: ReactNode;
  problems: string[];
  readyText: string;
  action?: ReactNode;
  next?: { label: string; onClick: () => void };
}

export function StepFooter({ back, summary, problems, readyText, action, next }: Props) {
  const blocked = problems.length > 0;
  return (
    <div className="fixed inset-x-0 bottom-0 z-20 px-3 pb-3 sm:px-6 sm:pb-5">
      <div className="mx-auto flex max-w-3xl items-center gap-3 rounded-[24px] border border-white/70 bg-cream/90 px-3 py-2.5 shadow-[0_18px_50px_-24px_rgba(53,89,107,0.7)] backdrop-blur-md sm:px-4">
        {back ? (
          <Link
            to={back.to}
            className="flex shrink-0 items-center gap-1.5 rounded-full px-3 py-2 text-sm font-semibold text-slate transition-colors duration-150 hover:bg-white/60"
          >
            <ArrowLeft size={16} aria-hidden /> <span className="hidden sm:inline">{back.label}</span>
            <span className="sr-only sm:hidden">{back.label}</span>
          </Link>
        ) : (
          <span className="w-2" />
        )}
        <div className="flex min-w-0 flex-1 flex-col">
          {summary && <span className="truncate text-sm font-semibold text-slate">{summary}</span>}
          <p
            className={`truncate text-xs ${blocked ? "text-ink" : "text-mutedTeal"} ${summary ? "" : "text-sm"}`}
            title={blocked ? problems[0] : readyText}
            aria-live="polite"
          >
            {blocked ? problems[0] : readyText}
          </p>
        </div>
        {action}
        {next && (
          <button
            type="button"
            disabled={blocked}
            onClick={next.onClick}
            className="flex shrink-0 items-center gap-2 rounded-full border border-white/60 bg-primary px-5 py-2.5 text-sm font-semibold text-cream shadow-[0_8px_22px_-10px_rgba(47,88,114,0.9)] transition-[background-color,transform] duration-150 hover:-translate-y-px hover:bg-primaryHover disabled:translate-y-0 disabled:cursor-not-allowed disabled:bg-ink/45 disabled:shadow-none motion-reduce:hover:translate-y-0"
          >
            {next.label} <ArrowRight size={16} aria-hidden />
          </button>
        )}
      </div>
    </div>
  );
}
