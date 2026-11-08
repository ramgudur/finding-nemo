// One committed source: kind badge, editable id, shape, remove.
import { Pencil, X } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { forwardRef, useEffect, useRef, useState } from "react";
import type { CompileInput } from "../../api/types";
import { KindBadge } from "./SourceSuggestionCard";
import { SLUG } from "./sourceUtils";

interface Props {
  source: CompileInput;
  duplicate: boolean;
  disabled?: boolean;
  onRename: (id: string) => void;
  onRemove: () => void;
}

export const SourceChip = forwardRef<HTMLLIElement, Props>(function SourceChip(
  { source, duplicate, disabled, onRename, onRemove },
  ref,
) {
  const reduce = useReducedMotion();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(source.id);
  const input = useRef<HTMLInputElement>(null);
  const bad = !SLUG.test(source.id) || duplicate;

  useEffect(() => {
    if (editing) input.current?.select();
  }, [editing]);
  useEffect(() => setText(source.id), [source.id]);

  const commit = () => {
    setEditing(false);
    const v = text.trim().toLowerCase();
    if (v && v !== source.id) onRename(v);
    else setText(source.id);
  };

  return (
    <motion.li
      ref={ref}
      layout={!reduce}
      initial={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.85, y: 8 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      exit={reduce ? { opacity: 0 } : { opacity: 0, scale: 0.85, transition: { duration: 0.18 } }}
      transition={{ type: "spring", stiffness: 380, damping: 30 }}
      title={source.meaning}
      className={`flex min-w-0 max-w-full items-center gap-1.5 rounded-full border py-1 pl-1.5 pr-1 shadow-[0_4px_14px_-8px_rgba(53,89,107,0.6)] ${
        bad ? "border-coral/70 bg-[#F6E3DA]/90" : "border-white/80 bg-cream/90"
      }`}
    >
      <KindBadge kind={source.kind} small />
      {editing ? (
        <input
          ref={input}
          value={text}
          aria-label="source id"
          onChange={(e) => setText(e.target.value.toLowerCase().replace(/\s+/g, "_"))}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
            if (e.key === "Escape") {
              setText(source.id);
              setEditing(false);
            }
          }}
          className="w-32 bg-transparent font-mono text-[13px] text-slate outline-none"
        />
      ) : (
        <button
          type="button"
          disabled={disabled}
          onClick={() => setEditing(true)}
          className="group flex min-w-0 items-center gap-1 rounded-full px-1 font-mono text-[13px] text-slate"
          aria-label={`edit id ${source.id}`}
        >
          <span className="truncate">{source.id}</span>
          <Pencil size={11} className="shrink-0 text-inkSoft opacity-60 transition group-hover:opacity-100" />
        </button>
      )}
      <span className="hidden font-mono text-[10.5px] text-inkSoft sm:inline">{source.shape}</span>
      {source.connector && (
        <span className="hidden font-mono text-[10.5px] text-mutedTeal sm:inline">
          {source.connector.preset ?? source.connector.type}
        </span>
      )}
      <button
        type="button"
        disabled={disabled}
        onClick={onRemove}
        aria-label={`remove ${source.id}`}
        className="shrink-0 rounded-full p-1 text-ink transition hover:bg-white/70 disabled:opacity-40"
      >
        <X size={13} />
      </button>
    </motion.li>
  );
});
