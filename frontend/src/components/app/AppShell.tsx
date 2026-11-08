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
      <div className="watercolor-grain relative min-h-screen overflow-x-hidden">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0"
          style={{
            background:
              "radial-gradient(1200px 520px at 12% -6%, rgba(240,235,221,0.75), transparent 60%), radial-gradient(900px 600px at 100% 30%, rgba(169,210,213,0.45), transparent 65%), linear-gradient(180deg, #DCEEF1 0%, #D5E8EB 55%, #DCEEF1 100%)",
          }}
        />
        <motion.main
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
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
          <Link to="/projects" className="rounded underline-offset-2 transition-colors hover:text-slate hover:underline">
            Projects
          </Link>
        </li>
        <li aria-hidden>
          <ChevronRight className="h-3.5 w-3.5 text-inkSoft" />
        </li>
        <li className="font-mono">
          {page && appHref ? (
            <Link to={appHref} className="rounded underline-offset-2 transition-colors hover:text-slate hover:underline">
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
    <span className="inline-flex items-center gap-1.5 rounded-full bg-seaGlass/35 px-2.5 py-0.5 text-xs font-semibold text-slate ring-1 ring-seaGlass/70">
      <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-mutedTeal" />
      Live
    </span>
  ) : (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-cream px-2.5 py-0.5 text-xs font-semibold text-slate ring-1 ring-softStone">
      <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-coral" />
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
      className={`inline-flex shrink-0 items-center gap-1 rounded-lg bg-white/75 px-2.5 py-1 text-[12px] font-semibold text-slate ring-1 ring-paleWater transition-[background-color,box-shadow] duration-150 hover:bg-white hover:shadow-sm ${className}`}
    >
      {copied ? <Check aria-hidden className="h-3.5 w-3.5 text-mutedTeal" /> : <Copy aria-hidden className="h-3.5 w-3.5" />}
      <span aria-live="polite">{copied ? "Copied" : label}</span>
    </button>
  );
}

export function Centered({ children }: { children: ReactNode }) {
  return (
    <main className="watercolor-grain flex min-h-screen flex-col items-center justify-center px-6 pt-20 text-center">{children}</main>
  );
}

export const primaryBtn =
  "inline-flex items-center justify-center gap-1.5 rounded-full bg-[#4F80B0] px-5 py-2 text-sm font-semibold text-cream shadow-[0_4px_14px_rgba(79,128,176,0.28)] ring-1 ring-white/40 transition-[transform,box-shadow,opacity] duration-150 hover:-translate-y-px hover:shadow-[0_8px_20px_rgba(79,128,176,0.32)] active:translate-y-0 disabled:translate-y-0 disabled:cursor-not-allowed disabled:opacity-45 disabled:shadow-none";

export const secondaryBtn =
  "inline-flex items-center justify-center gap-1.5 rounded-full bg-white/80 px-4 py-2 text-sm font-semibold text-slate ring-1 ring-seaGlass transition-[transform,box-shadow,opacity] duration-150 hover:-translate-y-px hover:shadow-sm disabled:translate-y-0 disabled:cursor-not-allowed disabled:opacity-45 disabled:shadow-none";

export const ghostBtn =
  "inline-flex items-center justify-center gap-1.5 rounded-full px-3 py-2 text-sm font-semibold text-ink transition-colors duration-150 hover:bg-white/50 hover:text-slate disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:bg-transparent";
