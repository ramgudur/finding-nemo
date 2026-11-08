// The console's hero: the live score map (WS stream, REST fallback) from the real MapResponse.
// Rule 8: what is drawn is exactly what the server returned for this key; without a key that is
// public layers and public hotspots only. `spec` is null in the public view (the spec is authority-only),
// so the geometry then comes from the MapResponse itself.
import { Hourglass } from "lucide-react";
import { useMemo, type ReactNode } from "react";
import type { AppSpec, GeoJsonBBox, MapResponse } from "../../api/types";
import type { LiveAppData } from "../../hooks/useLiveAppData";
import type { VectorState } from "../../hooks/useVectorReadings";
import { Map } from "../Map/Map";
import { HEAT_GRADIENT_CSS, fmtLatLon, gridShape } from "../Map/geo";
import { HeatmapStats } from "./HeatmapStats";
import { MapVectorBadge } from "./VectorFieldBits";

export interface HeatmapPanelProps {
  spec: AppSpec | null;
  live: LiveAppData;
  selectedLayerId: string | null;
  className?: string;
  /** Map click handler while a point is being picked (Test your app). */
  onPick?: ((lat: number, lon: number) => void) | null;
  marker?: { lat: number; lon: number; radius_m: number } | null;
  /** Shown under the "no activity yet" state: how to make something happen (authority view). */
  emptyHint?: ReactNode;
  /** Vector-field layers with their latest reading (authority view): drawn as a corner arrow. */
  vectors?: VectorState[];
}

function maxValue(m: MapResponse | null, id: string): number | null {
  const fc = m?.layers[id];
  if (!fc) return null;
  let mx = 0;
  for (const f of fc.features) if (f.properties.value > mx) mx = f.properties.value;
  return mx;
}

/** The layer drawn: the selected one if this key can see it, else score, else the first visible one. */
export function shownLayer(map: MapResponse | null, selectedLayerId: string | null): string | null {
  const ids = map ? Object.keys(map.layers) : [];
  if (selectedLayerId !== null && ids.includes(selectedLayerId)) return selectedLayerId;
  return ids.includes("score") ? "score" : (ids[0] ?? null);
}

export default function HeatmapPanel({ spec, live, selectedLayerId, className, onPick, marker, emptyHint, vectors = [] }: HeatmapPanelProps) {
  const { map, hotspots, pois } = live;
  // Destination labels come from the spec (authority view); without it no pin is marked as a destination.
  const labelSet = spec?.outputs?.poi_labels?.label_set;
  const destinationLabels = useMemo(
    () => (labelSet ? new Set(labelSet.filter((l) => l.destination).map((l) => l.label)) : null),
    [labelSet],
  );
  const poiCount = pois ? pois.filter((p) => p.status === "active").length : null;
  const ids = map ? Object.keys(map.layers) : [];
  const shown = shownLayer(map, selectedLayerId);
  const selectedHidden = selectedLayerId !== null && map !== null && !ids.includes(selectedLayerId);

  const specBbox: GeoJsonBBox | null = spec
    ? [spec.grid.bbox.min_lon, spec.grid.bbox.min_lat, spec.grid.bbox.max_lon, spec.grid.bbox.max_lat]
    : null;
  const bbox: GeoJsonBBox | null = map?.bbox ?? specBbox;
  const cellM = map?.cell_m ?? spec?.grid.cell_m ?? null;
  const shape = bbox && cellM ? gridShape(bbox, cellM) : null;
  const maxScore = maxValue(map, "score");
  const hsCount = hotspots?.hotspots.length ?? null;
  const emptyShown = map !== null && shown !== null && (map.layers[shown]?.features.length ?? 0) === 0;
  const threshold = spec?.outputs?.hotspot_threshold ?? 0.5;
  const shownVector = vectors.find((v) => v.layer.layerId === shown && v.layer.shiftOnly) ?? null;
  const badges = vectors.filter((v) => v.reading);
  // Three distinct pre-data states: loading (no answer yet), waiting (active, no tick yet), quiet (ticks, no cells).
  const phase: "loading" | "waiting" | "unavailable" | "noPublic" | "quiet" | "data" =
    map === null ? (live.notTicked ? "waiting" : live.settled ? "unavailable" : "loading") : shown === null ? "noPublic" : emptyShown ? "quiet" : "data";

  return (
    <section
      aria-label="Live map"
      className={`wp-sheet flex min-w-0 flex-col gap-4 p-4 sm:p-5 ${className ?? ""}`}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h2 className="text-[18px] font-bold text-slate">Live map</h2>
          <p className="mt-1 text-[13.5px] text-ink">{shown === "score" || shown === null ? "The combined score, updated every engine tick." : "One layer before weighting. Pick score in Layers to see the combined map."}</p>
        </div>
        <span className="font-mono text-[12px] text-ink">showing {shown ?? "nothing yet"}</span>
      </div>

      {live.error && !live.keyRejected && (
        <p role="alert" className="rounded-lg bg-attnBg px-3 py-2 text-[13px] font-semibold text-attn">
          {live.error}
        </p>
      )}
      {selectedHidden && (
        <p className="font-mono text-[12px] text-ink">
          {selectedLayerId} isn't visible with the current key{shown ? `; showing ${shown}` : ""}.
        </p>
      )}

      {/* The map is a window into the pond, framed in deep water. */}
      <div className="relative overflow-hidden rounded-[20px] bg-deep2 p-1.5 shadow-[var(--shadow-2)] [&_.leaflet-container]:rounded-[15px]">
        {bbox ? (
          <Map
            bbox={bbox}
            layers={map?.layers ?? {}}
            visible={shown ? [shown] : []}
            hotspots={hotspots?.hotspots ?? []}
            pois={pois ?? []}
            destinationLabels={destinationLabels}
            cellM={cellM ?? undefined}
            height="clamp(320px, 56vh, 540px)"
            onPick={onPick}
            marker={marker}
          />
        ) : (
          <div aria-hidden className="h-[clamp(320px,56vh,540px)] rounded-xl bg-mist/70 motion-safe:animate-pulse" />
        )}
        {bbox && (
          <>
            <div className="pointer-events-none absolute bottom-2 left-2 z-[500] rounded-lg bg-cream/95 px-2.5 py-1.5 font-mono text-[11px] text-slate shadow-[var(--shadow-1)]">
              SW {fmtLatLon(bbox[1], bbox[0])}
            </div>
            <div className="pointer-events-none absolute right-2 top-2 z-[500] rounded-lg bg-cream/95 px-2.5 py-1.5 font-mono text-[11px] text-slate shadow-[var(--shadow-1)]">
              NE {fmtLatLon(bbox[3], bbox[2])}
            </div>
            {badges.length > 0 && (
              <div className="pointer-events-none absolute right-2 top-10 z-[500] flex flex-col items-end gap-1.5">
                {badges.map((v) => (
                  <MapVectorBadge key={v.layer.layerId} state={v} />
                ))}
              </div>
            )}
          </>
        )}
        {phase === "loading" && (
          <div role="status" className="pointer-events-none absolute inset-0 z-[500] flex flex-col items-center justify-center gap-3 rounded-xl bg-mist/55 motion-safe:animate-pulse">
            <div aria-hidden className="grid grid-cols-6 gap-1 opacity-60">
              {Array.from({ length: 18 }, (_, i) => (
                <span key={i} className="h-5 w-5 rounded-[4px] bg-paleWater" />
              ))}
            </div>
            <p className="font-mono text-[12px] text-slate">Loading map…</p>
          </div>
        )}
        {phase === "waiting" && (
          <div role="status" className="pointer-events-none absolute inset-0 z-[500] flex items-center justify-center rounded-xl bg-mist/80">
            <div className="max-w-[34ch] space-y-1 rounded-2xl bg-cream/95 px-4 py-3 text-center shadow-[var(--shadow-1)]">
              <p className="flex items-center justify-center gap-1.5 text-[14px] font-bold text-slate">
                <Hourglass aria-hidden className="h-4 w-4 text-mutedTeal" /> Waiting for the first tick
              </p>
              <p className="text-[12.5px] text-ink">The engine draws a new map every 2 s once it picks the app up. If this stays, the backend may be restarting.</p>
            </div>
          </div>
        )}
        {phase === "unavailable" && (
          <div className="pointer-events-none absolute inset-0 z-[500] flex items-center justify-center rounded-xl bg-mist/70">
            <p className="max-w-[34ch] rounded-2xl bg-cream/95 px-4 py-3 text-center text-[13px] text-slate shadow-sm">The map couldn't be loaded. It retries every 2 s.</p>
          </div>
        )}
        {phase === "noPublic" && (
          <div role="status" className="pointer-events-none absolute inset-0 z-[500] flex items-center justify-center rounded-xl bg-mist/70">
            <p className="max-w-[34ch] rounded-2xl bg-cream/95 px-4 py-3 text-center text-[13px] text-slate shadow-sm">No layer of this app is public. Unlock the authority view to see the map.</p>
          </div>
        )}
        {phase === "quiet" && (
          <div role="status" className="absolute inset-x-0 bottom-9 z-[500] flex justify-center px-3">
            <div className="max-w-[44ch] rounded-2xl bg-cream/95 px-3.5 py-2 text-center shadow-[var(--shadow-1)]">
              <p className="flex items-center justify-center gap-1.5 text-[13px] font-semibold text-slate">
                <span aria-hidden className="wp-dot is-live" />
                {shownVector
                  ? `${shown} is a vector field, so it draws no cells`
                  : shown === "score"
                    ? "Live — no activity yet in your area"
                    : `Live — ${shown} has no cells yet`}
              </p>
              <p className="text-[12px] text-ink">
                {shownVector
                  ? shownVector.layer.shiftLayer
                    ? `It moves ${shownVector.layer.shiftLayer} before scoring; the arrow shows which way. Pick that layer to see the moved cells.`
                    : "Its direction and speed are shown by the arrow."
                  : (emptyHint ?? "Cells appear here when events land inside the area.")}
              </p>
            </div>
          </div>
        )}
      </div>

      <div className="flex flex-col gap-1 font-mono text-[11px] text-ink">
        <div className="flex items-center gap-2">
          <span>0</span>
          <span className="h-1.5 min-w-0 flex-1 rounded-full" style={{ background: HEAT_GRADIENT_CSS }} aria-hidden />
          <span>1</span>
        </div>
        <p className="break-words">
          {shown === "score" ? "score = clamp(Σ wᵢ·norm(layerᵢ), 0, 1), authority overrides applied after" : `${shown ?? "layer"}: normalized layer, before weights`}
        </p>
      </div>

      <HeatmapStats
        items={[
          {
            label: "max score",
            value: maxScore === null ? "not visible" : maxScore.toFixed(2),
            tone: maxScore === null ? "muted" : maxScore >= threshold ? "warm" : "default",
            title: maxScore === null ? "The score layer isn't visible with the current key" : undefined,
          },
          { label: "hotspots", value: hsCount === null ? "…" : String(hsCount), tone: hsCount ? "warm" : "default" },
          {
            label: "POIs",
            value: poiCount === null ? "…" : String(poiCount),
            title: live.authority ? undefined : "Public POIs only; unlock the authority view to see the rest",
          },
          {
            label: "cell",
            value: shape && cellM ? `${cellM} m · ${shape.rows} x ${shape.cols}` : "…",
            title: shape ? `${(shape.rows * shape.cols).toLocaleString()} cells` : undefined,
          },
          {
            label: "bbox (sw → ne)",
            value: bbox ? `${fmtLatLon(bbox[1], bbox[0])} → ${fmtLatLon(bbox[3], bbox[2])}` : "…",
            wide: true,
          },
        ]}
      />
    </section>
  );
}
