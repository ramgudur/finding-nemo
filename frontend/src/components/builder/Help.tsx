// Inline "?" disclosures: a small round button next to a label that opens a short plain-language note
// below it. A real button with aria-expanded, so it works on touch and keyboard (no hover-only tooltips).
import { HelpCircle } from "lucide-react";
import { useId, useState } from "react";
import type { ReactNode } from "react";

/** `button` goes next to the label; `panel` goes where the note should open (usually right under it). */
export function useHelp(topic: string, body: ReactNode) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const button = (
    <button
      type="button"
      aria-expanded={open}
      aria-controls={id}
      onClick={() => setOpen((o) => !o)}
      className={`bld-help-btn hit relative ${open ? "is-open" : ""}`}
    >
      <HelpCircle aria-hidden size={15} strokeWidth={2.2} />
      <span className="sr-only">{open ? `Hide help on ${topic}` : `What is ${topic}?`}</span>
    </button>
  );
  const panel = open ? (
    <div id={id} className="bld-help-panel">
      {body}
    </div>
  ) : null;
  return { button, panel, open };
}

/** A label row with its "?" and the note under it; children are the control. */
export function Labeled({
  label,
  htmlFor,
  help,
  topic,
  optional,
  children,
  className = "",
}: {
  label: ReactNode;
  htmlFor?: string;
  help?: ReactNode;
  /** Words for the screen-reader name of the "?" ("cell size"). */
  topic?: string;
  optional?: boolean;
  children?: ReactNode;
  className?: string;
}) {
  const h = useHelp(topic ?? (typeof label === "string" ? label.toLowerCase() : "this setting"), help);
  const Tag = htmlFor ? "label" : "span";
  return (
    <div className={`flex flex-col gap-1.5 ${className}`}>
      <div className="flex items-center gap-1.5">
        <Tag {...(htmlFor ? { htmlFor } : {})} className="bld-label">
          {label}
          {optional && <span className="bld-optional"> optional</span>}
        </Tag>
        {help != null && h.button}
      </div>
      {h.panel}
      {children}
    </div>
  );
}

/** The step's heading block: a Fredoka title, then "Step N of 4 · what this step is for". */
export function StepIntro({ step, title, lead, children }: { step: number; title: string; lead: string; children?: ReactNode }) {
  return (
    <header className="flex flex-col gap-2">
      <h1 className="bld-title">{title}</h1>
      <p className="bld-lead">
        <span className="bld-step-no">Step {step} of 4</span> · {lead}
      </p>
      {children}
    </header>
  );
}
