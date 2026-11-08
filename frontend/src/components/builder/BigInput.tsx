// The one large horizontal input (DESIGN.md §4). Each step uses it for exactly one thing: the app's intent on
// About, a source description on Sources. The placeholder is an italic example in a lighter ink (so it never
// reads as typed text) ending in a blinking caret bar; typing hides it and the field grows with its content.
// Not a chat box: no send bubble, no history.
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
        className="relative cursor-text rounded-[20px] border border-[rgba(99,143,145,0.32)] bg-[#FBFAF6]/95 px-5 py-4 shadow-[0_1px_0_rgba(255,255,255,0.8)_inset,0_16px_40px_-26px_rgba(53,89,107,0.6)] transition-[border-color,box-shadow] duration-200 hover:border-[rgba(99,143,145,0.6)] focus-within:border-mutedTeal focus-within:shadow-[0_0_0_3px_rgba(99,143,145,0.28),0_16px_40px_-26px_rgba(53,89,107,0.6)] sm:px-6 sm:py-5"
        onClick={() => ref.current?.focus()}
      >
        {/* One grid cell holds both, so a long placeholder grows the box instead of spilling out of it. */}
        <div className="grid">
        {empty && (
          <div
            aria-hidden
            className="pointer-events-none col-start-1 row-start-1 select-none text-[18px] italic leading-[1.45] text-[#5A747E] sm:text-[20px]"
          >
            {placeholder}
            <span className="ml-[2px] inline-block animate-cursor-blink not-italic font-light text-mutedTeal">|</span>
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
          className="col-start-1 row-start-1 block w-full resize-none overflow-hidden bg-transparent text-[18px] font-medium leading-[1.45] text-slate focus-visible:outline-none disabled:cursor-not-allowed sm:text-[20px]"
        />
        </div>
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
                className="tap flex items-center gap-1.5 rounded-full px-2 py-0.5 font-semibold text-slate transition-colors duration-150 hover:bg-white/55"
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
