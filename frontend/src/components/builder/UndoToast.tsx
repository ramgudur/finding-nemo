// A single "removed · Undo" toast. Removing something applies at once; Undo puts it back. When the toast
// times out (or a new one replaces it), `onExpire` runs so callers can finish any deferred cleanup.
import { Undo2, X } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useCallback, useEffect, useRef, useState } from "react";

interface Toast {
  id: number;
  message: string;
  undo: () => void;
  onExpire?: () => void;
}

const LIFETIME_MS = 6000;

export function useUndoToast() {
  const [toast, setToast] = useState<Toast | null>(null);
  const seq = useRef(0);
  const current = useRef<Toast | null>(null);

  const expire = useCallback(() => {
    current.current?.onExpire?.();
    current.current = null;
    setToast(null);
  }, []);

  const show = useCallback(
    (message: string, undo: () => void, onExpire?: () => void) => {
      current.current?.onExpire?.(); // the previous removal is now final
      const t = { id: ++seq.current, message, undo, onExpire };
      current.current = t;
      setToast(t);
    },
    [],
  );

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(expire, LIFETIME_MS);
    return () => clearTimeout(timer);
  }, [toast, expire]);

  // Leaving the page makes the pending removal final.
  useEffect(() => () => current.current?.onExpire?.(), []);

  const doUndo = () => {
    const t = current.current;
    current.current = null;
    setToast(null);
    t?.undo();
  };

  const node = <UndoToastView toast={toast} onUndo={doUndo} onClose={expire} />;
  return { show, node };
}

function UndoToastView({ toast, onUndo, onClose }: { toast: Toast | null; onUndo: () => void; onClose: () => void }) {
  const reduce = useReducedMotion();
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-28 z-40 flex justify-center px-4" aria-live="polite">
      <AnimatePresence>
        {toast && (
          <motion.div
            key={toast.id}
            role="status"
            initial={reduce ? { opacity: 0 } : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, transition: { duration: 0.15 } }}
            transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
            className="pointer-events-auto flex items-center gap-3 rounded-full border border-white/70 bg-slate py-2 pl-4 pr-2 text-sm text-cream shadow-[0_12px_30px_-14px_rgba(30,55,70,0.8)]"
          >
            <span className="max-w-[60vw] truncate">{toast.message}</span>
            <button
              type="button"
              onClick={onUndo}
              className="flex items-center gap-1 rounded-full bg-cream/15 px-3 py-1 font-semibold transition-colors duration-150 hover:bg-cream/25"
            >
              <Undo2 size={14} /> Undo
            </button>
            <button
              type="button"
              onClick={onClose}
              aria-label="Dismiss"
              className="rounded-full p-1 text-cream/80 transition-colors duration-150 hover:bg-cream/15 hover:text-cream"
            >
              <X size={14} />
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
