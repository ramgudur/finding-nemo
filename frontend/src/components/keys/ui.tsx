// Building blocks for the keys page. One type scale only: the page title, a step heading (18/700), body
// (15, or 13.5 for help lines) and mono (13) for code. The setup guide is a numbered step list shown one
// step at a time; examples sit in one tabbed code block; secondary detail hides behind "More detail".
import { ChevronDown, ChevronRight } from "lucide-react";
import { useId, useState, type KeyboardEvent, type ReactNode } from "react";
import { CopyButton } from "../app/AppShell";
import { LANGS, type Lang } from "./snippets";
import "./keys.css";

/** One numbered step of the setup guide. Collapsed, it shows its title and a one-line summary. */
export function GuideStep(props: {
  n: number;
  id: string;
  title: string;
  summary?: ReactNode;
  open: boolean;
  onToggle: () => void;
  last?: boolean;
  children: ReactNode;
}) {
  const { n, id, title, summary, open, onToggle, last, children } = props;
  const body = useId();
  return (
    <li className="relative grid grid-cols-[32px_minmax(0,1fr)] gap-x-3 sm:gap-x-4">
      {/* the thread between steps */}
      {!last && <span aria-hidden className="absolute bottom-0 left-[15px] top-12 w-px bg-slate/15" />}
      <span
        aria-hidden
        className={`relative z-[1] mt-3 flex h-8 w-8 items-center justify-center rounded-full font-mono text-[13px] font-semibold transition-colors duration-200 ease-wp ${
          open ? "bg-deep text-cream shadow-[0_0_0_4px_rgb(var(--c-deep)/0.14)]" : "bg-sheet text-deep shadow-[inset_0_0_0_1.5px_rgb(var(--c-deep)/0.3)]"
        }`}
      >
        {n}
      </span>
      <section aria-labelledby={id} className="min-w-0 pb-5">
        <button
          type="button"
          aria-expanded={open}
          aria-controls={body}
          onClick={onToggle}
          className="wp-press tap flex w-full items-start gap-3 rounded-xl px-2 py-3 text-left hover:bg-slate/[0.05]"
        >
          <span className="min-w-0 flex-1">
            <h2 id={id} className="text-[18px] font-bold leading-snug text-slate">
              {title}
            </h2>
            {summary && <span className="mt-0.5 block text-[13.5px] text-ink">{summary}</span>}
          </span>
          <ChevronDown aria-hidden className={`keys-chevron-down mt-1 h-5 w-5 shrink-0 text-ink ${open ? "is-open" : ""}`} />
        </button>
        {open && (
          <div id={body} className="keys-reveal mt-2 space-y-5 px-2">
            {children}
          </div>
        )}
      </section>
    </li>
  );
}

/** A disclosure: per-input guides (card) and "More detail" (quiet). The body fades in (no height animation). */
export function Disclosure(props: { summary: ReactNode; meta?: ReactNode; defaultOpen?: boolean; quiet?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(props.defaultOpen ?? false);
  const body = useId();
  if (props.quiet) {
    return (
      <div>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={body}
          onClick={() => setOpen((o) => !o)}
          className="wp-press tap inline-flex items-center gap-1.5 rounded-full py-1 pl-1.5 pr-3 text-[15px] font-bold text-slate hover:bg-slate/[0.07]"
        >
          <ChevronRight aria-hidden className="keys-chevron h-4 w-4 shrink-0 text-ink" />
          {props.summary}
        </button>
        {open && (
          <div id={body} className="keys-reveal mt-3 space-y-5 rounded-xl bg-sheet2 p-4">
            {props.children}
          </div>
        )}
      </div>
    );
  }
  return (
    <article className="overflow-hidden rounded-xl bg-sheet shadow-[inset_0_0_0_1px_rgb(var(--c-slate)/0.12)]">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={body}
        onClick={() => setOpen((o) => !o)}
        className="wp-press tap flex w-full items-center gap-2.5 px-4 py-3 text-left hover:bg-slate/[0.04]"
      >
        <ChevronRight aria-hidden className="keys-chevron h-4 w-4 shrink-0 text-ink" />
        <span className="min-w-0 flex-1">{props.summary}</span>
        {props.meta && <span className="shrink-0 text-[13.5px] text-ink max-sm:hidden">{props.meta}</span>}
      </button>
      {open && (
        <div id={body} className="keys-reveal space-y-5 border-t border-slate/10 px-4 pb-4 pt-4">
          {props.children}
        </div>
      )}
    </article>
  );
}

/** Heads a block inside a step: body size, bold, sentence case (no tiny uppercase labels). */
export function Label({ children }: { children: ReactNode }) {
  return <h3 className="text-[15px] font-bold text-slate">{children}</h3>;
}

const onDeepCopy = "!bg-white/10 !text-onDeep !shadow-none hover:!bg-white/20";

/** One code block for every example: the language tabs are its header, the copy button rides along. */
export function CodeTabs(props: { lang: Lang; onChange: (l: Lang) => void; idPrefix: string; code: string; label: string }) {
  const { lang, onChange, idPrefix, code, label } = props;
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const i = LANGS.findIndex((l) => l.id === lang);
    const next = LANGS[(i + (e.key === "ArrowRight" ? 1 : LANGS.length - 1)) % LANGS.length]!.id;
    onChange(next);
    document.getElementById(`${idPrefix}-${next}`)?.focus();
  };
  return (
    <div className="overflow-hidden rounded-xl bg-deep2 shadow-[var(--shadow-1)]">
      <div className="flex items-center gap-2 border-b border-white/10 px-2">
        <div role="tablist" aria-label="Example language" onKeyDown={onKey} className="flex min-w-0 flex-1 overflow-x-auto [scrollbar-width:none]">
          {LANGS.map(({ id, label: l }) => {
            const on = lang === id;
            return (
              <button
                key={id}
                id={`${idPrefix}-${id}`}
                role="tab"
                type="button"
                aria-selected={on}
                tabIndex={on ? 0 : -1}
                onClick={() => onChange(id)}
                className={`relative h-11 shrink-0 px-3 text-[13.5px] font-bold transition-colors duration-150 ease-wp ${
                  on ? "text-onDeep" : "text-onDeep2/75 hover:text-onDeep"
                }`}
              >
                {l}
                {on && <span aria-hidden className="absolute inset-x-2 bottom-0 h-[2px] rounded-full bg-onDeep" />}
              </button>
            );
          })}
        </div>
        <CopyButton value={code} className={onDeepCopy} />
      </div>
      <pre aria-label={label} tabIndex={0} className="max-h-[360px] overflow-auto p-4 font-mono text-[13px] leading-relaxed text-onDeep">
        {code}
      </pre>
    </div>
  );
}

/** A static code block (a sample payload): the same look as CodeTabs, without tabs. */
export function CodeBlock({ code, label }: { code: string; label: string }) {
  return (
    <div className="relative overflow-hidden rounded-xl bg-deep2 shadow-[var(--shadow-1)]">
      <pre aria-label={label} tabIndex={0} className="max-h-[360px] overflow-auto p-4 pr-24 font-mono text-[13px] leading-relaxed text-onDeep">
        {code}
      </pre>
      <CopyButton value={code} className={`absolute right-2 top-2 ${onDeepCopy}`} />
    </div>
  );
}

/** One line of copyable mono text (an endpoint, a header). */
export function CopyLine({ children, value }: { children: ReactNode; value: string }) {
  return (
    <div className="flex items-center gap-2">
      <code className="min-w-0 flex-1 break-all rounded-[10px] bg-sheet px-3 py-2 font-mono text-[13px] text-slate shadow-[inset_0_0_0_1px_rgb(var(--c-slate)/0.18)]">
        {children}
      </code>
      <CopyButton value={value} />
    </div>
  );
}

/** A compact reference table; scrolls sideways inside itself on narrow screens. */
export function RefTable({ head, rows, label }: { head: string[]; rows: ReactNode[][]; label: string }) {
  return (
    <div className="overflow-x-auto rounded-xl bg-sheet shadow-[inset_0_0_0_1px_rgb(var(--c-slate)/0.12)]">
      <table aria-label={label} className="w-full border-collapse text-left text-[13.5px]">
        <thead className="text-ink">
          <tr>
            {head.map((h) => (
              <th key={h} scope="col" className="px-3 py-2 font-bold">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-slate/10 align-top">
              {r.map((c, j) => (
                <td key={j} className={`px-3 py-2 ${j === 0 ? "whitespace-nowrap font-mono text-[13px] text-slate" : "text-ink"}`}>
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export const mono = "font-mono text-[13px] text-slate";
