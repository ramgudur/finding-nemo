// DEMO_MODE only: a hidden-by-default drawer with the sim clock and the ground-truth error chart.
// Renders nothing unless GET /v1/debug/clock answers (it 404s outside DEMO_MODE), so production
// consoles never show it. Ground truth needs the authority key too (the server enforces it).
import { Bug, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";
import { api } from "../../api/client";
import { usePrefersReducedMotion } from "../../hooks/usePrefersReducedMotion";
import { DemoTools } from "../DemoTools/DemoTools";

export default function DebugDrawer({ appId, authorityKey, active }: { appId: string; authorityKey: string | null; active: boolean }) {
  const reduced = usePrefersReducedMotion();
  const [demo, setDemo] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    api
      .debugClock()
      .then(() => alive && setDemo(true))
      .catch(() => alive && setDemo(false));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  if (!demo) return null;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-expanded={open}
        className="fixed bottom-5 right-5 z-30 flex items-center gap-1.5 rounded-full border border-softAqua/70 bg-cream/85 px-3 py-1.5 font-mono text-[11px] text-ink shadow-sm backdrop-blur-sm transition hover:-translate-y-px hover:bg-cream"
      >
        <Bug size={13} /> demo tools
      </button>
      <AnimatePresence>
        {open && (
          <>
            <motion.div
              key="scrim"
              className="fixed inset-0 z-40 bg-slate/10"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setOpen(false)}
            />
            <motion.aside
              key="drawer"
              role="dialog"
              aria-label="Demo tools"
              className="fixed inset-y-0 right-0 z-50 flex w-full max-w-[520px] flex-col gap-4 overflow-y-auto border-l border-softAqua/70 bg-mist/95 p-5 shadow-[-12px_0_40px_rgba(53,89,107,0.12)] backdrop-blur-sm"
              initial={reduced ? { opacity: 0 } : { x: "100%" }}
              animate={reduced ? { opacity: 1 } : { x: 0 }}
              exit={reduced ? { opacity: 0 } : { x: "100%" }}
              transition={{ duration: reduced ? 0.01 : 0.35, ease: [0.22, 0.8, 0.3, 1] }}
            >
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-semibold text-slate">Demo tools</p>
                  <p className="font-mono text-[11px] text-inkSoft">DEMO_MODE only · never shown in production</p>
                </div>
                <button type="button" onClick={() => setOpen(false)} aria-label="close demo tools"
                  className="rounded-full p-2 text-ink transition hover:bg-washedSky">
                  <X size={16} />
                </button>
              </div>
              {active && authorityKey ? (
                <DemoTools appId={appId} authorityKey={authorityKey} />
              ) : (
                <p className="rounded-2xl border border-dashed border-softAqua bg-washedSky/50 p-4 text-sm text-ink">
                  The sim clock and ground-truth error chart need an active app and its authority key.
                </p>
              )}
            </motion.aside>
          </>
        )}
      </AnimatePresence>
    </>
  );
}
