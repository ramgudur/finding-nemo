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
    <dl className="grid grid-cols-2 gap-x-4 gap-y-3 leading-tight sm:grid-cols-3">
      {items.map((s) => (
        <div key={s.label} className={`min-w-0 ${s.wide ? "col-span-2" : ""}`} title={s.title}>
          <dt className="text-[13px] font-semibold text-ink first-letter:uppercase">{s.label}</dt>
          <dd
            className={`mt-1 break-words font-mono text-[15px] font-semibold ${
              s.tone === "warm" ? "text-heat5" : s.tone === "muted" ? "text-inkSoft" : "text-slate"
            }`}
          >
            {s.value}
          </dd>
        </div>
      ))}
    </dl>
  );
}
