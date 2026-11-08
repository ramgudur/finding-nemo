// Persistent bottom bar for each step: Back on the left, what's still missing (or a ready line) in the middle,
// and the step's primary action on the right. The action stays disabled until the step is valid. With several
// blocking problems the bar shows the first and a "+N more" toggle that opens the full list above the bar.
import { ArrowLeft, ArrowRight, ChevronDown } from "lucide-react";
import { useEffect, useId, useState } from "react";
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
  /** Announce status changes (off when another live region already narrates them). */
  live?: boolean;
}

export function StepFooter({ back, summary, problems, readyText, action, next, live = true }: Props) {
  const blocked = problems.length > 0;
  const more = problems.length - 1;
  const [open, setOpen] = useState(false);
  const listId = useId();
  const showList = open && more > 0;
  useEffect(() => {
    if (more <= 0) setOpen(false);
  }, [more]);
  return (
    <div
      className="fixed inset-x-0 bottom-0 z-20 px-4 pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:px-6 sm:pb-5"
      onKeyDown={(e) => {
        if (e.key === "Escape" && showList) {
          e.stopPropagation();
          setOpen(false);
        }
      }}
    >
      {showList && (
        <div className="mx-auto mb-2 max-w-3xl rounded-[18px] border border-white/70 bg-cream/95 px-4 py-3 shadow-[0_18px_50px_-24px_rgba(53,89,107,0.7)] backdrop-blur-md">
          <p className="text-xs font-semibold text-slate">Still to fix before you continue</p>
          <ul id={listId} className="mt-1.5 max-h-[40vh] list-disc space-y-1 overflow-y-auto pl-4 text-[13px] text-ink marker:text-inkSoft">
            {problems.map((x) => (
              <li key={x} className="[overflow-wrap:anywhere]">
                {x}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="mx-auto flex max-w-3xl items-center gap-3 rounded-[24px] border border-white/70 bg-cream/90 px-2 py-2 sm:px-3 sm:py-2.5 shadow-[0_18px_50px_-24px_rgba(53,89,107,0.7)] backdrop-blur-md sm:px-4">
        {back ? (
          <Link
            to={back.to}
            className="tap flex shrink-0 items-center justify-center gap-1.5 rounded-full px-3 py-2 text-sm font-semibold text-slate transition-colors duration-150 hover:bg-white/60"
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
            className={`truncate text-xs max-sm:line-clamp-2 max-sm:whitespace-normal ${blocked ? "text-ink" : "text-mutedTeal"} ${summary ? "" : "text-sm"}`}
            title={blocked ? problems[0] : readyText}
            aria-live={live ? "polite" : "off"}
          >
            {blocked ? problems[0] : readyText}
          </p>
          {more > 0 && (
            <button
              type="button"
              aria-expanded={showList}
              aria-controls={showList ? listId : undefined}
              onClick={() => setOpen((o) => !o)}
              className="hit relative flex w-fit items-center gap-0.5 rounded-md text-xs font-semibold text-slate underline underline-offset-[3px] hover:text-primaryHover"
            >
              {showList ? "Hide the list" : `+${more} more`}
              <ChevronDown
                size={12}
                aria-hidden
                className={`transition-transform duration-150 motion-reduce:transition-none ${showList ? "" : "rotate-180"}`}
              />
            </button>
          )}
        </div>
        {action}
        {next && (
          <button
            type="button"
            disabled={blocked}
            onClick={next.onClick}
            className="tap flex shrink-0 items-center gap-2 whitespace-nowrap rounded-full border border-white/60 bg-primary px-5 py-2.5 max-sm:px-4 text-sm font-semibold text-cream shadow-[0_8px_22px_-10px_rgba(47,88,114,0.9)] transition-[background-color,transform] duration-150 hover:-translate-y-px hover:bg-primaryHover disabled:translate-y-0 disabled:cursor-not-allowed disabled:bg-ink/45 disabled:shadow-none motion-reduce:hover:translate-y-0"
          >
            {next.label} <ArrowRight size={16} aria-hidden />
          </button>
        )}
      </div>
    </div>
  );
}
