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
  /** Match a wide step's content width (Sources). */
  wide?: boolean;
}

export function StepFooter({ back, summary, problems, readyText, action, next, live = true, wide = false }: Props) {
  const width = wide ? "max-w-[1120px]" : "max-w-3xl";
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
      className="fixed inset-x-0 bottom-0 z-20"
      onKeyDown={(e) => {
        if (e.key === "Escape" && showList) {
          e.stopPropagation();
          setOpen(false);
        }
      }}
    >
      {showList && (
        <div className={`mx-4 mb-4 rounded-2xl bg-sheet px-4 py-3 shadow-[var(--shadow-2)] sm:mx-auto ${width}`}>
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
      {/* The dock: deep water under the sunlit work, the logo's wave along its top edge. */}
      <div className="wp-dock pb-[env(safe-area-inset-bottom)]">
      <div className={`mx-auto flex items-center gap-3 px-3 py-3 sm:px-6 sm:py-3.5 ${width}`}>
        {back ? (
          <Link
            to={back.to}
            className="bld-btn tap shrink-0 px-3 text-onDeep hover:bg-white/10"
          >
            <ArrowLeft size={16} aria-hidden /> <span className="hidden sm:inline">{back.label}</span>
            <span className="sr-only sm:hidden">{back.label}</span>
          </Link>
        ) : (
          <span className="w-2" />
        )}
        <div className="flex min-w-0 flex-1 flex-col">
          {summary && <span className="truncate text-[15px] font-extrabold text-onDeep">{summary}</span>}
          <p
            className={`truncate text-[13px] max-sm:line-clamp-2 max-sm:whitespace-normal ${blocked ? "text-attnOnDeep" : "text-okOnDeep"} ${summary ? "" : "text-sm"}`}
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
              className="hit relative flex w-fit items-center gap-0.5 rounded-md text-xs font-semibold text-onDeep2 underline underline-offset-[3px] hover:text-onDeep"
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
            className="bld-btn tap shrink-0 bg-cream px-5 py-3 text-[15px] text-deep2 shadow-[0_6px_16px_-8px_rgba(0,0,0,0.5)] hover:bg-onDeep disabled:bg-cream/30 disabled:text-onDeep/70 disabled:shadow-none max-sm:px-4"
          >
            {next.label} <ArrowRight size={16} aria-hidden />
          </button>
        )}
      </div>
      </div>
    </div>
  );
}
