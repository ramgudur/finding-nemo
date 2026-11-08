// The map's "colour by source" legend (authority view): a swatch, the input id and a thin weight bar
// per input. For ~10 s after a weight Change the bar keeps a ghost tick at the old value and reads
// "old → new". On phones it collapses to a small button so it never sits on top of the pins.
import { ArrowUpRight, ChevronDown, Layers } from "lucide-react";
import { useEffect, useState } from "react";
import type { WeightGhost } from "../../hooks/useMapLearning";
import type { SourceInput } from "../Map/sourceColours";

const fmt = (w: number) => w.toFixed(2);

function useWide(): boolean {
  const q = "(min-width: 640px)";
  const [wide, setWide] = useState(() => typeof window === "undefined" || window.matchMedia(q).matches);
  useEffect(() => {
    const mq = window.matchMedia(q);
    const on = () => setWide(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return wide;
}

export function SourceLegend({ inputs, ghosts, distrustedCount }: { inputs: SourceInput[]; ghosts: Record<string, WeightGhost>; distrustedCount: number }) {
  const wide = useWide();
  const [open, setOpen] = useState(wide);
  useEffect(() => setOpen(wide), [wide]);
  if (!inputs.length) return null;
  const scale = Math.max(1, ...inputs.map((i) => Math.max(i.weight, ghosts[i.inputId]?.before ?? 0)));

  if (!open)
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-expanded={false}
        className="tap wp-press pointer-events-auto inline-flex h-9 items-center gap-1.5 rounded-full bg-cream/95 px-3 text-[12px] font-bold text-slate shadow-[var(--shadow-1)]"
      >
        <span aria-hidden className="flex -space-x-1">
          {inputs
            .filter((i) => i.hue)
            .slice(0, 4)
            .map((i) => (
              <span key={i.inputId} className="h-3 w-3 rounded-[3px] ring-1 ring-cream" style={{ background: i.hue! }} />
            ))}
        </span>
        Sources
      </button>
    );

  return (
    <div className="pointer-events-auto w-[min(290px,calc(100vw-56px))] rounded-xl bg-cream/95 p-2.5 pb-2 shadow-[var(--shadow-1)]">
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 text-[11.5px] font-bold text-ink">
          <Layers aria-hidden className="h-3.5 w-3.5" /> Coloured by the source that adds most
        </span>
        {!wide && (
          <button type="button" onClick={() => setOpen(false)} aria-expanded className="tap -m-1 rounded p-1 text-ink hover:text-slate" aria-label="Collapse the legend">
            <ChevronDown aria-hidden className="h-4 w-4" />
          </button>
        )}
      </div>
      <ul className="space-y-1">
        {inputs.map((i) => {
          const g = ghosts[i.inputId];
          return (
            <li key={i.inputId} className="grid grid-cols-[14px_minmax(0,1fr)_64px_auto] items-center gap-x-2">
              {i.hue ? (
                <span aria-hidden className="h-3 w-3 rounded-[3px]" style={{ background: i.hue }} />
              ) : (
                <ArrowUpRight aria-hidden className="h-3.5 w-3.5 rotate-90 text-mutedTeal" />
              )}
              <span className="truncate font-mono text-[12px] font-semibold text-slate" title={i.hue ? i.inputId : `${i.inputId}: a vector field, drawn as arrows, never tints cells`}>
                {i.inputId}
              </span>
              <span aria-hidden className="relative h-1 rounded-full bg-slate/[0.12]">
                <span
                  className="absolute inset-y-0 left-0 rounded-full transition-[width] duration-500 ease-wp"
                  style={{ width: `${(i.weight / scale) * 100}%`, background: i.hue ?? "rgb(var(--c-muted-teal))" }}
                />
                {g && <span className="absolute -top-1 h-3 w-[2px] rounded-full bg-slate/70" style={{ left: `calc(${(g.before / scale) * 100}% - 1px)` }} />}
              </span>
              <span className="whitespace-nowrap text-right font-mono text-[11.5px] tabular-nums text-slate">
                {g ? (
                  <>
                    <span className="text-ink">{fmt(g.before)} → </span>
                    {fmt(g.after)}
                  </>
                ) : (
                  fmt(i.weight)
                )}
              </span>
            </li>
          );
        })}
      </ul>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-slate/[0.12] pt-1.5 text-[11px] font-semibold text-ink">
        <span>Stronger = higher score</span>
        <span className="inline-flex items-center gap-1">
          <svg aria-hidden width="10" height="10">
            <circle cx="5" cy="5" r="4" fill="rgb(var(--c-deep))" fillOpacity="0.8" />
          </svg>
          Place
        </span>
        <span className="inline-flex items-center gap-1">
          <svg aria-hidden width="11" height="11">
            <circle cx="5.5" cy="5.5" r="4.5" fill="none" stroke="#6E7B81" strokeWidth="1.2" strokeDasharray="2.5 2" />
          </svg>
          Lowered trust{distrustedCount ? ` (${distrustedCount})` : ""}
        </span>
      </div>
    </div>
  );
}
