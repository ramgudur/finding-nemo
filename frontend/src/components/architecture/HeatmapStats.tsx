// Small mono stats strip shared by the live heatmap and the pre-activation schematic.
import type { ReactNode } from "react";

export interface StatItem {
  label: string;
  value: ReactNode;
  title?: string;
  tone?: "default" | "warm" | "muted";
  /** take two columns (long values such as a bbox) */
  wide?: boolean;
}

export function HeatmapStats({ items }: { items: StatItem[] }) {
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-2.5 font-mono text-[11px] leading-tight sm:grid-cols-3">
      {items.map((s) => (
        <div key={s.label} className={`min-w-0 ${s.wide ? "col-span-2" : ""}`} title={s.title}>
          <dt className="uppercase tracking-[0.08em] text-inkSoft">{s.label}</dt>
          <dd
            className={`mt-0.5 break-words ${
              s.tone === "warm" ? "text-[#C46F52]" : s.tone === "muted" ? "text-inkSoft" : "text-slate"
            }`}
          >
            {s.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}
