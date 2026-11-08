// The one large horizontal input. Step 1 captures the app's intent, then the same field describes a
// source. The placeholder ends in a bar that blinks like a caret; typing hides it and the field grows.
import { CornerDownLeft, LoaderCircle } from "lucide-react";
import { useEffect, useLayoutEffect, useRef } from "react";

interface Props {
  mode: "intent" | "source";
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  disabled?: boolean;
  busy?: boolean;
  autoFocus?: boolean;
}

const PLACEHOLDER = { intent: "describe intent for application", source: "describe a source" } as const;
const HINT = { intent: "to set the intent", source: "to find ways to connect it" } as const;

export function IntentInput({ mode, value, onChange, onSubmit, disabled, busy, autoFocus }: Props) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const empty = value.length === 0;

  useLayoutEffect(() => {
    const t = ref.current;
    if (!t) return;
    t.style.height = "auto";
    t.style.height = `${t.scrollHeight}px`;
  }, [value, mode]);

  useEffect(() => {
    if (autoFocus && !disabled) ref.current?.focus();
  }, [autoFocus, disabled, mode]);

  const canSubmit = !disabled && !busy && value.trim().length > 0;

  return (
    <div className={`flex flex-col gap-2 transition-opacity ${disabled ? "opacity-50" : ""}`}>
      <div
        className="big-input group relative rounded-[28px] border border-white/70 bg-cream/60 px-7 py-6 shadow-[0_18px_60px_-30px_rgba(53,89,107,0.6),inset_0_1px_0_rgba(255,255,255,0.7)] backdrop-blur-[3px] transition-[background-color,box-shadow] duration-300 focus-within:bg-cream/80 focus-within:shadow-[0_22px_70px_-28px_rgba(53,89,107,0.65),inset_0_1px_0_rgba(255,255,255,0.8)] sm:px-9 sm:py-7"
        onClick={() => ref.current?.focus()}
      >
        {empty && (
          <div
            aria-hidden
            className="pointer-events-none absolute inset-x-7 top-6 select-none text-[22px] leading-[1.35] text-inkSoft/80 sm:inset-x-9 sm:top-7 sm:text-[28px]"
          >
            {PLACEHOLDER[mode]}
            <span className="ml-[1px] inline-block animate-cursor-blink font-light text-ink">|</span>
          </div>
        )}
        <textarea
          ref={ref}
          rows={1}
          value={value}
          disabled={disabled}
          aria-label={PLACEHOLDER[mode]}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              if (canSubmit) onSubmit();
            }
          }}
          spellCheck
          style={{ caretColor: empty ? "transparent" : undefined }}
          className="block w-full resize-none overflow-hidden bg-transparent text-[22px] leading-[1.35] text-slate outline-none disabled:cursor-not-allowed sm:text-[28px]"
        />
      </div>
      <div className="flex min-h-[20px] items-center justify-end gap-2 px-3 text-xs text-inkSoft">
        {busy ? (
          <span className="flex items-center gap-1.5">
            <LoaderCircle size={13} className="animate-spin" /> looking for ways to connect it…
          </span>
        ) : (
          <button
            type="button"
            disabled={!canSubmit}
            onClick={onSubmit}
            className={`flex items-center gap-1.5 rounded-full px-2 py-0.5 transition ${
              canSubmit ? "text-ink hover:bg-white/40" : "opacity-0"
            }`}
          >
            <kbd className="flex items-center gap-1 rounded-md border border-inkSoft/30 bg-white/40 px-1.5 py-px font-mono text-[11px]">
              Enter <CornerDownLeft size={11} />
            </kbd>
            {HINT[mode]}
          </button>
        )}
      </div>
    </div>
  );
}
