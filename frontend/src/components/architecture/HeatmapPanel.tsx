// The console's hero: the live score map (WS stream, REST fallback) from the real MapResponse.
// Rule 8: what is drawn is exactly what the server returned for this key; without a key that is
// public layers and public hotspots only. `spec` is null in the public view (the spec is authority-only),
// so the geometry then comes from the MapResponse itself.
import { useMemo } from "react";
import type { AppSpec, GeoJsonBBox, MapResponse } from "../../api/types";
import type { LiveAppData } from "../../hooks/useLiveAppData";
import { Map } from "../Map/Map";
import { HEAT_GRADIENT_CSS, fmtLatLon, gridShape } from "../Map/geo";
import { HeatmapStats } from "./HeatmapStats";

export interface HeatmapPanelProps {
  spec: AppSpec | null;
  live: LiveAppData;
  selectedLayerId: string | null;
  className?: string;
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

export default function HeatmapPanel({ spec, live, selectedLayerId, className }: HeatmapPanelProps) {
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

  return (
    <section
      aria-label="Live map"
      className={`flex min-w-0 flex-col gap-3 rounded-3xl bg-washedSky/70 p-4 shadow-[0_8px_30px_-12px_rgba(53,89,107,0.3)] ${className ?? ""}`}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-[15px] font-bold text-slate">Live map</h2>
        <span className="font-mono text-[12px] text-ink">showing {shown ?? "nothing yet"}</span>
      </div>

      {live.error && !live.keyRejected && (
        <p role="alert" className="rounded-lg bg-coral/20 px-3 py-2 text-[13px] text-[#7A4232]">
          {live.error}
        </p>
      )}
      {selectedHidden && (
        <p className="font-mono text-[12px] text-ink">
          {selectedLayerId} isn't visible with the current key{shown ? `; showing ${shown}` : ""}.
        </p>
      )}

      <div className="relative">
        {bbox ? (
          <Map bbox={bbox} layers={map?.layers ?? {}} visible={shown ? [shown] : []} hotspots={hotspots?.hotspots ?? []} pois={pois ?? []} destinationLabels={destinationLabels} cellM={cellM ?? undefined} height="clamp(300px, 52vh, 480px)" />
        ) : (
          <div aria-hidden className="h-[clamp(300px,52vh,480px)] rounded-2xl bg-mist/70 motion-safe:animate-pulse" />
        )}
        {bbox && (
          <>
            <div className="pointer-events-none absolute bottom-2 left-2 z-[500] rounded-md bg-cream/90 px-2 py-1 font-mono text-[10.5px] text-slate shadow-sm">
              SW {fmtLatLon(bbox[1], bbox[0])}
            </div>
            <div className="pointer-events-none absolute right-2 top-2 z-[500] rounded-md bg-cream/90 px-2 py-1 font-mono text-[10.5px] text-slate shadow-sm">
              NE {fmtLatLon(bbox[3], bbox[2])}
            </div>
          </>
        )}
        {(map === null || emptyShown || shown === null) && (
          <div className="pointer-events-none absolute inset-0 z-[500] flex items-center justify-center">
            <p className="rounded-lg bg-mist/95 px-3 py-1.5 font-mono text-[12px] text-slate shadow-sm">
              {map === null
                ? "Waiting for the first tick"
                : shown === null
                  ? "No layers are public. Unlock the authority view to see them."
                  : "No cells above 0.01 yet: waiting for events"}
            </p>
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
