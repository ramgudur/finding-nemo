// Shared frame for the per-app pages (review, keys, console): the watercolor wash, one short entry
// fade (200 ms, none with reduced motion), the "Projects › <app>" breadcrumb and the status chip.
// One back-control pattern for all three pages: the breadcrumb.
import { Check, ChevronRight, Copy } from "lucide-react";
import { MotionConfig, motion } from "motion/react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

export function AppShell({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  return (
    <MotionConfig reducedMotion="user">
      <div className="wp-ground relative min-h-screen overflow-x-hidden">
        {/* Caustic light across the top of the page only. */}
        <div aria-hidden className="wp-caustic" />
        <motion.main
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.2, ease: [0.23, 1, 0.32, 1] }}
          className={`relative mx-auto flex flex-col gap-5 px-4 pb-12 pt-24 sm:px-8 ${wide ? "max-w-[1440px]" : "max-w-[1180px]"}`}
        >
          {children}
        </motion.main>
      </div>
    </MotionConfig>
  );
}

/** Projects › <app> [› page]. The only back control on these pages. */
export function Breadcrumb({ appId, page, appHref }: { appId: string; page?: string; appHref?: string }) {
  return (
    <nav aria-label="Breadcrumb" className="text-[13px] text-ink">
      <ol className="flex flex-wrap items-center gap-1">
        <li>
          <Link to="/projects" className="hit relative rounded underline-offset-2 transition-colors hover:text-slate hover:underline">
            Projects
          </Link>
        </li>
        <li aria-hidden>
          <ChevronRight className="h-3.5 w-3.5 text-inkSoft" />
        </li>
        <li className="font-mono">
          {page && appHref ? (
            <Link to={appHref} className="hit relative rounded underline-offset-2 transition-colors hover:text-slate hover:underline">
              {appId}
            </Link>
          ) : (
            <span aria-current={page ? undefined : "page"} className="text-slate">
              {appId}
            </span>
          )}
        </li>
        {page && (
          <>
            <li aria-hidden>
              <ChevronRight className="h-3.5 w-3.5 text-inkSoft" />
            </li>
            <li aria-current="page" className="text-slate">
              {page}
            </li>
          </>
        )}
      </ol>
    </nav>
  );
}

/** The two app states as the dev sees them: Proposed (before confirmation) and Live (after). */
export function StatusChip({ live }: { live: boolean }) {
  return live ? (
    <span className="inline-flex h-7 items-center gap-2.5 rounded-full bg-live/10 px-3 text-[13px] font-bold text-live">
      <span aria-hidden className="wp-dot is-live" />
      Live
    </span>
  ) : (
    <span className="inline-flex h-7 items-center gap-2.5 rounded-full bg-attnBg px-3 text-[13px] font-bold text-attn">
      <span aria-hidden className="wp-dot is-attn" />
      Proposed
    </span>
  );
}

export function CopyButton({ value, label = "Copy", className = "" }: { value: string; label?: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      // clipboard unavailable (insecure origin): the value stays selectable
    }
  };
  return (
    <button
      type="button"
      onClick={() => void copy()}
      className={`tap wp-press inline-flex shrink-0 items-center justify-center gap-1.5 rounded-full bg-sheet px-3 py-1 text-[12.5px] font-bold text-slate shadow-[inset_0_0_0_1px_rgb(var(--c-slate)/0.24)] hover:shadow-[inset_0_0_0_1px_rgb(var(--c-ink-soft))] ${className}`}
    >
      {copied ? <Check aria-hidden className="h-3.5 w-3.5 text-live" /> : <Copy aria-hidden className="h-3.5 w-3.5" />}
      <span aria-live="polite">{copied ? "Copied" : label}</span>
    </button>
  );
}

export function Centered({ children }: { children: ReactNode }) {
  return (
    <main className="wp-ground flex min-h-screen flex-col items-center justify-center px-6 pt-20 text-center">{children}</main>
  );
}

// One button vocabulary for the per-app pages (matches the builder's .bld-btn): deep primary, sheet secondary
// with a hairline ring, quiet ghost. Every one answers a press with scale(0.97) (.wp-press).
export const primaryBtn =
  "tap wp-press inline-flex h-10 items-center justify-center gap-2 rounded-full bg-deep px-5 text-sm font-bold text-cream shadow-[inset_0_1px_0_rgba(255,255,255,0.18),0_1px_2px_rgba(23,53,71,0.2),0_6px_14px_-8px_rgba(23,53,71,0.55)] hover:bg-primaryHover disabled:cursor-not-allowed disabled:opacity-45 disabled:shadow-none";

export const secondaryBtn =
  "tap wp-press inline-flex h-10 items-center justify-center gap-2 rounded-full bg-sheet px-4 text-sm font-bold text-slate shadow-[inset_0_0_0_1px_rgb(var(--c-slate)/0.24)] hover:shadow-[inset_0_0_0_1px_rgb(var(--c-ink-soft))] disabled:cursor-not-allowed disabled:opacity-45";

export const ghostBtn =
  "tap wp-press inline-flex h-10 items-center justify-center gap-2 rounded-full px-3 text-sm font-bold text-slate hover:bg-slate/[0.07] disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-transparent";
