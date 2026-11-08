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
        className="relative cursor-text rounded-[20px] bg-sheet px-5 py-4 shadow-[var(--shadow-2),inset_0_0_0_1px_rgba(255,255,255,0.6)] transition-shadow duration-200 ease-wp focus-within:shadow-[0_0_0_2px_rgb(var(--c-deep)),var(--shadow-2)] sm:px-7 sm:py-6"
        onClick={() => ref.current?.focus()}
      >
        {/* One grid cell holds both, so a long placeholder grows the box instead of spilling out of it. */}
        <div className="grid">
        {empty && (
          <div
            aria-hidden
            className="pointer-events-none col-start-1 row-start-1 select-none text-[18px] italic leading-[1.45] text-inkSoft sm:text-[20px]"
          >
            {placeholder}
            <span className="ml-[2px] inline-block animate-cursor-blink not-italic font-light text-deep">|</span>
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
          className="col-start-1 row-start-1 block w-full resize-none overflow-hidden bg-transparent text-[18px] font-semibold leading-[1.45] text-slate focus-visible:outline-none disabled:cursor-not-allowed sm:text-[20px]"
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
                className="tap wp-press flex items-center gap-2 rounded-full bg-deep py-1 pl-1.5 pr-3 font-semibold text-cream shadow-[0_6px_14px_-8px_rgb(var(--c-deep-2)/0.7)] hover:bg-primaryHover"
              >
                <kbd className="flex items-center gap-1 rounded-full bg-white/15 px-2 py-0.5 font-mono text-[11px]">
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
