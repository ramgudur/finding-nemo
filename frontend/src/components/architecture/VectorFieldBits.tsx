// Visible pieces for vector-field layers (#284): the map-corner arrow, the row summary and the note
// on the layer it shifts. All text comes from the spec's params and the latest reading.
import { MoveRight } from "lucide-react";
import type { VectorState } from "../../hooks/useVectorReadings";
import { compass, driftM, fmtDistance, fmtSpeed } from "./vectorField";

/** An arrow pointing the way the field moves things (degrees clockwise from north). */
export function VectorArrow({ deg, className }: { deg: number; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden className={className}>
      {/* drawn pointing north; rotation eases so a changed direction turns instead of jumping */}
      <g style={{ transform: `rotate(${deg}deg)`, transformOrigin: "12px 12px", transition: "transform 400ms cubic-bezier(0.23, 1, 0.32, 1)" }}>
        <path d="M12 3.5 L17 11 H13.6 V20.5 H10.4 V11 H7 Z" fill="currentColor" />
      </g>
    </svg>
  );
}

/** "3.1 m/s from NE · drifts smoke_reports.density ~1.9 km SW", or why there is no reading yet. */
export function vectorSummary({ layer, reading }: VectorState): string {
  if (!reading) return `${layer.name} (vector field) · no reading yet`;
  const parts = [`${layer.name} (vector field)`, `${fmtSpeed(reading.speed)} from ${compass(reading.fromDeg)}`];
  if (layer.shiftLayer) parts.push(`drifts ${layer.shiftLayer} ~${fmtDistance(driftM(layer, reading))} ${compass(reading.towardDeg)}`);
  return parts.join(" · ");
}

/** The line on a vector-field row: arrow + summary. */
export function VectorRowSummary({ state }: { state: VectorState }) {
  const { layer, reading } = state;
  return (
    <span className="flex items-start gap-1.5 rounded-lg bg-white/55 px-2 py-1.5 text-[12px] leading-snug text-slate ring-1 ring-paleWater/80">
      {reading ? (
        <VectorArrow deg={reading.towardDeg} className="mt-px h-4 w-4 shrink-0 text-primary" />
      ) : (
        <MoveRight aria-hidden className="mt-px h-4 w-4 shrink-0 text-inkSoft" />
      )}
      <span className="min-w-0">
        <span className="font-semibold">{vectorSummary(state)}</span>
        {layer.shiftOnly && (
          <span className="block text-ink">
            Weight 0: it adds no cells of its own{layer.shiftLayer ? `; it moves ${layer.shiftLayer} before scoring, over ${Math.round(layer.horizonS / 60)} min` : ""}.
          </span>
        )}
      </span>
    </span>
  );
}

/** The note on a layer another layer shifts: "shifted ~1.9 km SW by open_meteo". */
export function shiftNoteFor(states: VectorState[], layerId: string): string | null {
  const by = states.filter((s) => s.layer.shiftLayer === layerId);
  if (!by.length) return null;
  return by
    .map(({ layer, reading }) =>
      reading
        ? `Shifted ~${fmtDistance(driftM(layer, reading))} ${compass(reading.towardDeg)} along ${layer.inputId} before scoring`
        : `Shifted along ${layer.inputId} before scoring`,
    )
    .join("; ");
}

/** Map corner: which way the field runs and how fast. */
export function MapVectorBadge({ state }: { state: VectorState }) {
  const { layer, reading } = state;
  if (!reading) return null;
  return (
    <div
      className="pointer-events-none flex items-center gap-2 rounded-xl bg-cream/95 px-2 py-1.5 text-slate shadow-[0_4px_14px_-6px_rgba(53,89,107,0.45)]"
      title={`${vectorSummary(state)} (latest ${layer.inputId} reading, ${new Date(reading.ts).toLocaleTimeString()})`}
    >
      <span className="grid h-8 w-8 place-items-center rounded-full bg-white/80 ring-1 ring-paleWater">
        <VectorArrow deg={reading.towardDeg} className="h-5 w-5 text-primary" />
      </span>
      <span className="leading-tight">
        <span className="block font-mono text-[12px] font-semibold tabular-nums">{fmtSpeed(reading.speed)}</span>
        <span className="block text-[10.5px] text-ink">
          {layer.name.toLowerCase()} from {compass(reading.fromDeg)}
        </span>
      </span>
    </div>
  );
}
