// Verification methods in the spec's priority order, with their current value and whether they have data.
import type { VerificationView } from "../../api/types";

export function VerificationScores({ view }: { view: VerificationView }) {
  const methods = [...view.methods].sort((a, b) => a.priority - b.priority);
  return (
    <div className="space-y-2">
      {methods.length === 0 ? (
        <p className="text-[12.5px] text-inkSoft">Not enough ticks cached yet.</p>
      ) : (
        <ol className="space-y-1.5">
          {methods.map((m) => {
            const v = m.has_data && m.value !== null ? m.value : null;
            return (
              <li key={m.method} className="rounded-xl border border-paleWater bg-white/35 px-3 py-2">
                <div className="flex items-baseline gap-2">
                  <span className="font-mono text-[11px] text-inkSoft">{m.priority}</span>
                  <span className="break-all font-mono text-[12.5px] text-slate">{m.method}</span>
                  <span className="ml-auto font-mono text-[12.5px] tabular-nums text-slate">
                    {v !== null ? v.toFixed(3) : <span className="text-inkSoft">no data</span>}
                  </span>
                </div>
                <div aria-hidden className="mt-1.5 h-1 overflow-hidden rounded-full bg-paleWater/60">
                  <div
                    className="h-full rounded-full bg-seaGlass transition-[width] duration-500"
                    style={{ width: `${v !== null ? Math.max(2, Math.min(100, v * 100)) : 0}%` }}
                  />
                </div>
              </li>
            );
          })}
        </ol>
      )}
      <p className={`text-[12px] ${view.cold_start ? "text-attn" : "text-ink"}`}>
        {view.cold_start
          ? "Cold start: flexible weight changes are rejected until two methods have data."
          : `${view.ticks} ticks in the last ${Math.round(view.window_s)} s`}
      </p>
    </div>
  );
}
