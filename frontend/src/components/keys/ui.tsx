// Small building blocks for the keys page: numbered sections, a disclosure, language tabs that every
// example shares, a copyable code block, and a compact reference table.
import { ChevronRight } from "lucide-react";
import { useId, useState, type KeyboardEvent, type ReactNode } from "react";
import { CopyButton } from "../app/AppShell";
import { LANGS, type Lang } from "./snippets";
import "./keys.css";

export function Section(props: { n: number; id: string; title: string; lead?: ReactNode; children: ReactNode; tone?: "card" | "plain" }) {
  const { n, id, title, lead, children, tone = "plain" } = props;
  return (
    <section
      aria-labelledby={id}
      className={`space-y-3 ${tone === "card" ? "rounded-3xl bg-cream/80 p-5 shadow-[0_10px_30px_-16px_rgba(53,89,107,0.45)] sm:p-6" : ""}`}
    >
      <div className="flex items-baseline gap-2.5">
        <span aria-hidden className="font-mono text-[12px] font-semibold tabular-nums text-mutedTeal">
          {String(n).padStart(2, "0")}
        </span>
        <div className="min-w-0 space-y-0.5">
          <h2 id={id} className="text-[17px] font-bold text-slate">
            {title}
          </h2>
          {lead && <p className="max-w-[68ch] text-[13.5px] text-ink">{lead}</p>}
        </div>
      </div>
      {children}
    </section>
  );
}

/** A disclosure with a full-width header button; the body fades in (no height animation). */
export function Disclosure(props: { summary: ReactNode; meta?: ReactNode; defaultOpen?: boolean; children: ReactNode }) {
  const [open, setOpen] = useState(props.defaultOpen ?? false);
  const body = useId();
  return (
    <article className="rounded-2xl bg-white/50 ring-1 ring-paleWater/70">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={body}
        onClick={() => setOpen((o) => !o)}
        className="keys-press tap flex w-full items-center gap-2 rounded-2xl px-4 py-3 text-left hover:bg-white/40"
      >
        <ChevronRight aria-hidden className="keys-chevron h-4 w-4 shrink-0 text-mutedTeal" />
        <span className="min-w-0 flex-1">{props.summary}</span>
        {props.meta && <span className="shrink-0 text-[12px] text-ink">{props.meta}</span>}
      </button>
      {open && (
        <div id={body} className="keys-reveal space-y-4 border-t border-paleWater/70 px-4 pb-4 pt-3">
          {props.children}
        </div>
      )}
    </article>
  );
}

/** A small uppercase label that heads a block inside a disclosure. */
export function Label({ children }: { children: ReactNode }) {
  return <h4 className="text-[11.5px] font-semibold uppercase tracking-[0.06em] text-inkSoft">{children}</h4>;
}

export function LangTabs({ lang, onChange, idPrefix }: { lang: Lang; onChange: (l: Lang) => void; idPrefix: string }) {
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const i = LANGS.findIndex((l) => l.id === lang);
    const next = LANGS[(i + (e.key === "ArrowRight" ? 1 : LANGS.length - 1)) % LANGS.length]!.id;
    onChange(next);
    document.getElementById(`${idPrefix}-${next}`)?.focus();
  };
  return (
    <div role="tablist" aria-label="Example language" onKeyDown={onKey} className="inline-flex flex-wrap gap-1 rounded-full bg-white/60 p-1">
      {LANGS.map(({ id, label }) => {
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
            className={`keys-press rounded-full px-3 py-1 text-[12.5px] font-semibold ${
              on ? "bg-cream text-slate shadow-sm ring-1 ring-paleWater" : "text-ink hover:text-slate"
            }`}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}

export function CodeBlock({ code, label }: { code: string; label: string }) {
  return (
    <div className="relative">
      <pre
        aria-label={label}
        tabIndex={0}
        className="max-h-[360px] overflow-auto rounded-xl bg-slate/95 p-3 pr-20 font-mono text-[12px] leading-relaxed text-cream"
      >
        {code}
      </pre>
      <CopyButton value={code} className="absolute right-2 top-2" />
    </div>
  );
}

/** One line of copyable mono text (an endpoint, a header). */
export function CopyLine({ children, value }: { children: ReactNode; value: string }) {
  return (
    <div className="flex items-center gap-2">
      <code className="min-w-0 flex-1 break-all rounded-lg bg-white/75 px-2.5 py-1.5 font-mono text-[12.5px] text-slate">{children}</code>
      <CopyButton value={value} />
    </div>
  );
}

/** A compact reference table; scrolls sideways inside itself on narrow screens. */
export function RefTable({ head, rows, label }: { head: string[]; rows: ReactNode[][]; label: string }) {
  return (
    <div className="overflow-x-auto rounded-xl ring-1 ring-paleWater/70">
      <table aria-label={label} className="w-full border-collapse text-left text-[12.5px]">
        <thead className="bg-white/60 text-inkSoft">
          <tr>
            {head.map((h) => (
              <th key={h} scope="col" className="px-3 py-1.5 font-semibold">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-paleWater/60 align-top">
              {r.map((c, j) => (
                <td key={j} className={`px-3 py-1.5 ${j === 0 ? "whitespace-nowrap font-mono text-slate" : "text-ink"}`}>
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

export const mono = "font-mono text-[12px] text-slate";
