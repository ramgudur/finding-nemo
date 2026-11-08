// Pre-activation placeholder for the heatmap: the grid the engine WILL use, sized from spec.grid.
// It shows geometry only (bbox, cell size, rows x cols, the spec's own destination points) and never
// a single fabricated value: there is no data until the dev confirms and activates the spec.
import type { AppSpec, GeoJsonBBox } from "../../api/types";
import { usePrefersReducedMotion } from "../../hooks/usePrefersReducedMotion";
import { fmtLatLon, gridShape, lineStride } from "../Map/geo";
import { HeatmapStats } from "./HeatmapStats";

const MAX_LINES = 40; // per axis; bigger grids draw one square per k x k cells
const VIEW_W = 1000;

function fmtMeters(m: number): string {
  return m >= 1000 ? `${(m / 1000).toFixed(m >= 10_000 ? 0 : 2)} km` : `${Math.round(m)} m`;
}

export default function ProposedSchematic({
  spec,
  selectedLayerId,
}: {
  spec: AppSpec;
  selectedLayerId: string | null;
}) {
  const reduced = usePrefersReducedMotion();
  const { bbox: b, cell_m } = spec.grid;
  const bbox: GeoJsonBBox = [b.min_lon, b.min_lat, b.max_lon, b.max_lat];
  const s = gridShape(bbox, cell_m);
  // the grid as the engine builds it (ceil), so the drawn extent may run a little past the bbox
  const gridW = s.cols * cell_m;
  const gridH = s.rows * cell_m;
  const aspect = Math.min(Math.max(gridH / gridW, 0.35), 1.6);
  const viewH = Math.round(VIEW_W * aspect);
  const sx = VIEW_W / gridW; // svg units per meter (x)
  const sy = viewH / gridH;
  const k = lineStride(Math.max(s.rows, s.cols), MAX_LINES);
  const colLines = Math.floor(s.cols / k);
  const rowLines = Math.floor(s.rows / k);
  const stepX = k * cell_m * sx;
  const stepY = k * cell_m * sy;
  const bboxW = s.widthM * sx;
  const bboxH = s.heightM * sy;

  const toXY = (lat: number, lon: number) => ({
    x: (lon - b.min_lon) * s.mPerDegLon * sx,
    y: viewH - (lat - b.min_lat) * s.mPerDegLat * sy,
  });
  const dests = (spec.routing?.destination_points ?? []).filter(
    (d) => d.lat >= b.min_lat && d.lat <= b.max_lat && d.lon >= b.min_lon && d.lon <= b.max_lon,
  );

  const declared = new Set<string>(["score"]);
  for (const inp of spec.inputs ?? [])
    for (const o of inp.operators ?? []) declared.add(o.layer_id ?? `${inp.id}.${o.op}`);
  const selectedKnown = selectedLayerId ? declared.has(selectedLayerId) : false;

  const fade = (i: number) =>
    reduced ? undefined : { animation: `hm-schematic-in 600ms ease both`, animationDelay: `${Math.min(i * 14, 520)}ms` };

  return (
    <div className="flex flex-col gap-3">
      <div
        role="status"
        className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-dashed border-coral/60 bg-cream/70 px-3 py-2 text-[13px] text-slate"
      >
        <span className="inline-flex items-center gap-1.5 font-semibold">
          <span className="h-1.5 w-1.5 rounded-full bg-coral" aria-hidden />
          Proposed, not live yet
        </span>
        <span className="text-ink">Confirm the spec to activate it. Values appear only once real events arrive.</span>
      </div>

      <div className="relative flex items-center justify-center overflow-hidden rounded-xl border border-mutedTeal/20 bg-[linear-gradient(160deg,#DCEEF1_0%,#D5E8EB_55%,#C9E2E5_100%)] px-6 pb-8 pt-9">
        <style>{`@keyframes hm-schematic-in { from { opacity: 0 } to { opacity: 1 } }`}</style>
        <div
          className="pointer-events-none absolute left-3 top-2.5 font-mono text-[11px] text-ink"
          aria-hidden
        >
          {k === 1 ? `1 square = 1 cell (${cell_m} m)` : `1 square = ${k}x${k} cells (${fmtMeters(k * cell_m)})`}
        </div>
        {/* fitted to the grid's real aspect ratio, so HTML labels can sit on its corners */}
        <div
          className="relative w-full"
          style={{ aspectRatio: `${VIEW_W} / ${viewH}`, maxWidth: `${Math.round((380 * VIEW_W) / viewH)}px` }}
        >
          <svg
            viewBox={`0 0 ${VIEW_W} ${viewH}`}
            preserveAspectRatio="none"
            className="absolute inset-0 h-full w-full overflow-visible"
            role="img"
            aria-label={`Proposed grid: ${s.rows} rows by ${s.cols} columns of ${cell_m} m cells. No data yet.`}
          >
            <defs>
              <pattern id="hm-hatch" width="14" height="14" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <line x1="0" y1="0" x2="0" y2="14" stroke="#8DB9B7" strokeWidth="1.2" strokeOpacity="0.22" />
              </pattern>
            </defs>
            <rect x={0} y={0} width={VIEW_W} height={viewH} fill="#F0EBDD" fillOpacity={0.25} />
            <rect x={0} y={0} width={VIEW_W} height={viewH} fill="url(#hm-hatch)" />
            {/* grid lines: every cell boundary, or every k-th on large grids */}
            <g stroke="#638F91" strokeOpacity={0.3} strokeWidth={1} vectorEffect="non-scaling-stroke">
              {Array.from({ length: colLines + 1 }, (_, j) => (
                <line key={`c${j}`} x1={j * stepX} y1={0} x2={j * stepX} y2={viewH} style={fade(j)} vectorEffect="non-scaling-stroke" />
              ))}
              {Array.from({ length: rowLines + 1 }, (_, i) => (
                <line
                  key={`r${i}`}
                  x1={0}
                  y1={viewH - i * stepY}
                  x2={VIEW_W}
                  y2={viewH - i * stepY}
                  style={fade(i)}
                  vectorEffect="non-scaling-stroke"
                />
              ))}
            </g>
            {/* engine grid extent (ceil'd to whole cells) and the bbox the dev drew */}
            <rect x={0} y={0} width={VIEW_W} height={viewH} fill="none" stroke="#638F91" strokeOpacity={0.45} strokeWidth={1} vectorEffect="non-scaling-stroke" />
            <rect
              x={0}
              y={viewH - bboxH}
              width={bboxW}
              height={bboxH}
              fill="none"
              stroke="#35596B"
              strokeOpacity={0.6}
              strokeWidth={1.4}
              strokeDasharray="6 5"
              vectorEffect="non-scaling-stroke"
            />
          </svg>
          {/* the spec's own routing destinations (spec data, not live values) */}
          {dests.map((d) => {
            const p = toXY(d.lat, d.lon);
            return (
              <div
                key={`${d.lat},${d.lon},${d.label}`}
                className="pointer-events-none absolute flex -translate-x-1.5 -translate-y-1.5 items-center gap-1"
                style={{ left: `${(p.x / VIEW_W) * 100}%`, top: `${(p.y / viewH) * 100}%` }}
              >
                <span className="h-3 w-3 rounded-full border-[1.5px] border-mutedTeal bg-cream" />
                <span className="rounded bg-cream/80 px-1 font-mono text-[10px] text-ink">{d.label}</span>
              </div>
            );
          })}
          <span className="pointer-events-none absolute -bottom-6 left-0 whitespace-nowrap font-mono text-[10px] text-ink">
            SW {fmtLatLon(b.min_lat, b.min_lon)}
          </span>
          <span className="pointer-events-none absolute -top-6 right-0 whitespace-nowrap font-mono text-[10px] text-ink">
            NE {fmtLatLon(b.max_lat, b.max_lon)}
          </span>
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
            <div className="rounded-lg bg-mist/90 px-4 py-2 text-center shadow-sm">
              <p className="font-mono text-[12px] text-slate">no data: not live yet</p>
              {selectedLayerId && (
                <p className="mt-0.5 font-mono text-[11px] text-ink">
                  selected: {selectedLayerId}
                  {selectedKnown ? "" : " (not in this spec)"}
                </p>
              )}
            </div>
          </div>
        </div>
      </div>

      <HeatmapStats
        items={[
          {
            label: "state",
            value: spec.status === "pending_confirmation" ? "awaiting confirm" : (spec.status ?? "draft"),
            tone: "warm",
            title: spec.status ?? "draft",
          },
          { label: "cell", value: `${cell_m} m` },
          { label: "grid", value: `${s.rows} x ${s.cols}`, title: `${(s.rows * s.cols).toLocaleString()} cells` },
          { label: "extent", value: `${fmtMeters(s.widthM)} x ${fmtMeters(s.heightM)}` },
          { label: "max score", value: "n/a", tone: "muted", title: "No values before activation" },
          { label: "hotspots", value: "n/a", tone: "muted", title: "No values before activation" },
        ]}
      />
    </div>
  );
}
