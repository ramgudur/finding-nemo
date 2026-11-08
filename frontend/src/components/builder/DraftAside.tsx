// "Your app so far": a quiet side panel on the Sources step, read straight from the draft (the intent, the
// app's own area drawn to scale with its destinations, the grid size) plus what happens after this step.
// It never holds state of its own and never shows a place the dev didn't pick.
import { Layers, Sparkles } from "lucide-react";
import type { BBox, DestinationPoint } from "../../api/types";
import { areaSize } from "../sources/AreaPicker";
import { gridCells } from "../sources/sourceUtils";

const fmt = (v: number) => v.toFixed(4).replace("-", "−");

function AreaSketch({ bbox, points }: { bbox: BBox; points: DestinationPoint[] }) {
  // Fit the area into a 280 × 150 frame at its real aspect ratio (metres, not degrees).
  const [rows, cols] = gridCells(bbox, 1);
  const pad = 22;
  const maxW = 280 - pad * 2;
  const maxH = 150 - pad * 2;
  const scale = Math.min(maxW / Math.max(cols, 1), maxH / Math.max(rows, 1));
  const w = Math.max(24, cols * scale);
  const h = Math.max(24, rows * scale);
  const x0 = (280 - w) / 2;
  const y0 = (150 - h) / 2;
  const at = (p: DestinationPoint) => ({
    x: x0 + ((p.lon - bbox.min_lon) / (bbox.max_lon - bbox.min_lon || 1)) * w,
    y: y0 + (1 - (p.lat - bbox.min_lat) / (bbox.max_lat - bbox.min_lat || 1)) * h,
  });
  return (
    <svg viewBox="0 0 280 150" className="block h-auto w-full rounded-[10px]" role="img" aria-label="Your area, to scale">
      <rect width="280" height="150" className="fill-paleWater" />
      <rect width="280" height="150" fill="url(#draft-caustic)" />
      <defs>
        <pattern id="draft-caustic" width="60" height="60" patternUnits="userSpaceOnUse">
          <path
            d="M0 10C8 7 13 13 20 9S33 5 40 11 52 15 60 10M10 0C14 8 7 15 12 22S18 35 11 42 7 55 10 60M40 0C37 7 45 14 41 21S35 35 42 41 45 55 40 60"
            fill="none"
            stroke="white"
            strokeOpacity={0.45}
          />
        </pattern>
      </defs>
      <rect x={x0} y={y0} width={w} height={h} rx={4} className="fill-deep/[0.08] stroke-deep" strokeWidth={1.5} strokeDasharray="5 4" />
      {points.map((p, i) => {
        const { x, y } = at(p);
        return (
          <g key={i}>
            <circle cx={x} cy={y} r={9} className="fill-none stroke-deep/35" />
            <circle cx={x} cy={y} r={4.5} className="fill-deep" />
          </g>
        );
      })}
    </svg>
  );
}

export function DraftAside({
  intent,
  bbox,
  cellM,
  destinationPoints,
}: {
  intent: string;
  bbox: BBox | null;
  cellM: number;
  destinationPoints: DestinationPoint[];
}) {
  const label = "mb-2 block text-[13px] font-semibold text-ink";
  const [rows, cols] = bbox ? gridCells(bbox, cellM) : [0, 0];
  return (
    <aside aria-label="Your app so far" className="wp-sheet flex flex-col divide-y divide-slate/[0.12] lg:sticky lg:top-24">
      <section className="p-5">
        <span className={label}>What it’s for</span>
        <p className="text-[15px] font-semibold leading-relaxed text-slate [overflow-wrap:anywhere]">{intent.trim()}</p>
      </section>
      {bbox && (
        <section className="p-5">
          <span className={label}>Area</span>
          <AreaSketch bbox={bbox} points={destinationPoints} />
          <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[13px]">
            <dt className="text-ink">South-west</dt>
            <dd className="text-right font-mono text-[12.5px] text-slate">
              {fmt(bbox.min_lat)}, {fmt(bbox.min_lon)}
            </dd>
            <dt className="text-ink">North-east</dt>
            <dd className="text-right font-mono text-[12.5px] text-slate">
              {fmt(bbox.max_lat)}, {fmt(bbox.max_lon)}
            </dd>
            <dt className="text-ink">Size</dt>
            <dd className="text-right text-slate">{areaSize(bbox)}</dd>
            <dt className="text-ink">Cells</dt>
            <dd className="text-right font-mono text-[12.5px] text-slate">
              {cellM} m · {cols} × {rows}
            </dd>
            <dt className="text-ink">Destinations</dt>
            <dd className="text-right font-mono text-[12.5px] text-slate">{destinationPoints.length}</dd>
          </dl>
        </section>
      )}
      <section className="p-5">
        <span className={label}>What happens next</span>
        <ul className="grid gap-2.5 text-[13px] leading-snug text-ink">
          <li className="grid grid-cols-[18px_1fr] gap-2">
            <Layers size={14} className="mt-0.5 text-deep" aria-hidden />
            <span>Each source becomes a map layer with its own weight.</span>
          </li>
          <li className="grid grid-cols-[18px_1fr] gap-2">
            <Sparkles size={14} className="mt-0.5 text-deep" aria-hidden />
            <span>Nemotron proposes the plan. Nothing goes live until you confirm it.</span>
          </li>
        </ul>
      </section>
    </aside>
  );
}
