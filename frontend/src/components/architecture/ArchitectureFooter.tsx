// Save Project (client-side Past Projects record) and Go Back to Editing (Sources, draft intact).
import { ArrowLeft, Check, Save } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { recordProject, saveSpecSnapshot } from "../../api/authorityKey";
import type { AppSpec } from "../../api/types";

export default function ArchitectureFooter({ spec, active }: { spec: AppSpec; active: boolean }) {
  const navigate = useNavigate();
  const [toast, setToast] = useState(false);

  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast(false), 2600);
    return () => clearTimeout(id);
  }, [toast]);

  const save = () => {
    recordProject({ appId: spec.app_id, intent: spec.intent, status: active ? "active" : "proposed", updatedAt: new Date().toISOString() });
    if (!active) saveSpecSnapshot(spec);
    setToast(true);
  };

  return (
    <footer className="relative flex flex-col-reverse items-stretch gap-3 rounded-3xl border border-softAqua/50 bg-cream/55 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-6">
      <button
        type="button"
        onClick={() => navigate("/new")}
        className="flex items-center justify-center gap-2 rounded-full px-4 py-2 text-sm text-slate transition hover:-translate-y-px hover:bg-washedSky/80"
      >
        <ArrowLeft size={15} /> Go Back to Editing
      </button>
      <p className="hidden text-center font-mono text-[11px] text-inkSoft md:block">
        {spec.app_id} · {active ? "active" : "pending confirmation"}
      </p>
      <button
        type="button"
        onClick={save}
        className="flex items-center justify-center gap-2 rounded-full bg-[#4F80B0] px-5 py-2 text-sm font-semibold text-cream shadow-[0_4px_14px_rgba(79,128,176,0.28)] ring-1 ring-white/40 transition hover:-translate-y-0.5 hover:shadow-[0_8px_20px_rgba(79,128,176,0.32)] active:translate-y-0"
      >
        <Save size={15} /> Save Project
      </button>
      <AnimatePresence>
        {toast && (
          <div className="pointer-events-none fixed inset-x-0 bottom-6 z-40 flex justify-center">
          <motion.div
            role="status"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 6 }}
            transition={{ duration: 0.25 }}
            className="flex items-center gap-2 rounded-full bg-slate/95 px-4 py-2 text-sm text-cream shadow-lg"
          >
            <Check size={15} /> Saved to Past Projects on this device
          </motion.div>
          </div>
        )}
      </AnimatePresence>
    </footer>
  );
}
