// The one large horizontal input (DESIGN.md §4). Each step uses it for exactly one thing: the app's intent on
// About, a source description on Sources. The placeholder ends in a bar that blinks like a caret; typing hides
// it and the field grows with its content. Not a chat box: no send bubble, no history.
import { CornerDownLeft, LoaderCircle } from "lucide-react";
import { useEffect, useLayoutEffect, useRef } from "react";

interface Props {
  id: string;
  label: string; // accessible name (the visible label lives above, in the step)
  placeholder: string;
  value: string;
  onChange: (v: string) => void;
  /** Enter (without Shift). Omit for a field where Enter only adds a line. */
  onSubmit?: () => void;
  submitHint?: string;
  busy?: boolean;
  busyText?: string;
  disabled?: boolean;
  autoFocus?: boolean;
  describedBy?: string;
}

export function BigInput({
  id,
  label,
  placeholder,
  value,
  onChange,
  onSubmit,
  submitHint,
  busy,
  busyText,
  disabled,
  autoFocus,
  describedBy,
}: Props) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const empty = value.length === 0;

  useLayoutEffect(() => {
    const t = ref.current;
    if (!t) return;
    t.style.height = "auto";
    t.style.height = `${t.scrollHeight}px`;
  }, [value]);

  useEffect(() => {
    if (autoFocus && !disabled) ref.current?.focus({ preventScroll: true });
  }, [autoFocus, disabled]);

  const canSubmit = !!onSubmit && !disabled && !busy && value.trim().length > 0;

  return (
    <div className={`flex flex-col gap-2 ${disabled ? "opacity-60" : ""}`}>
      <div
        className="relative rounded-[28px] border border-white/70 bg-cream/65 px-6 py-5 shadow-[0_18px_60px_-30px_rgba(53,89,107,0.6),inset_0_1px_0_rgba(255,255,255,0.7)] transition-[background-color,box-shadow] duration-200 focus-within:bg-cream/90 focus-within:shadow-[0_0_0_2px_#35596B,0_22px_70px_-28px_rgba(53,89,107,0.65)] sm:px-8 sm:py-6"
        onClick={() => ref.current?.focus()}
      >
        {empty && (
          <div
            aria-hidden
            className="pointer-events-none absolute inset-x-6 top-5 select-none font-display text-[22px] leading-[1.35] text-inkSoft sm:inset-x-8 sm:top-6 sm:text-[27px]"
          >
            {placeholder}
            <span className="ml-[1px] inline-block animate-cursor-blink font-light text-slate">|</span>
          </div>
        )}
        <textarea
          ref={ref}
          id={id}
          rows={1}
          value={value}
          disabled={disabled}
          aria-label={label}
          aria-describedby={describedBy}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (onSubmit && e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              if (canSubmit) onSubmit();
            }
          }}
          spellCheck
          style={{ caretColor: empty ? "transparent" : undefined }}
          // The container draws the focus ring (focus-within), so the bare textarea needs none of its own.
          className="block w-full resize-none overflow-hidden bg-transparent font-display text-[22px] leading-[1.35] text-slate focus-visible:outline-none disabled:cursor-not-allowed sm:text-[27px]"
        />
      </div>
      {onSubmit && (
        <div className="flex min-h-[24px] items-center justify-end gap-2 px-3 text-xs text-ink" aria-live="polite">
          {busy ? (
            <span className="flex items-center gap-1.5">
              <LoaderCircle size={13} className="animate-spin motion-reduce:animate-none" /> {busyText}
            </span>
          ) : (
            canSubmit && (
              <button
                type="button"
                onClick={onSubmit}
                className="flex items-center gap-1.5 rounded-full px-2 py-0.5 transition-colors duration-150 hover:bg-white/50"
              >
                <kbd className="flex items-center gap-1 rounded-md border border-inkSoft/40 bg-white/50 px-1.5 py-px font-mono text-[11px]">
                  Enter <CornerDownLeft size={11} />
                </kbd>
                {submitHint}
              </button>
            )
          )}
        </div>
      )}
    </div>
  );
}
