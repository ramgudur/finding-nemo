// LEFT panel of the Architecture page. Before activation: the proposed grid schematic (no values).
// After activation: the live score map (WS stream, REST fallback) from the real MapResponse.
// Rule 8: what is drawn is exactly what the server returned for this key; without a key that is
// public layers and public hotspots only.
import type { ReactNode } from "react";
import type { AppSpec, GeoJsonBBox, MapResponse } from "../../api/types";
import { useLiveAppData } from "../../hooks/useLiveAppData";
import { Map } from "../Map/Map";
import { HEAT_GRADIENT_CSS, fmtLatLon, gridShape } from "../Map/geo";
import { HeatmapStats } from "./HeatmapStats";
import ProposedSchematic from "./ProposedSchematic";

export interface HeatmapPanelProps {
  spec: AppSpec;
  appId: string;
  authorityKey: string | null;
  active: boolean;
  selectedLayerId: string | null;
  /** Called once when the server rejects the authority key (401/403 or auth_ok: false). */
  onAuthError?: () => void;
  className?: string;
}

export default function HeatmapPanel({
  spec,
  appId,
  authorityKey,
  active,
  selectedLayerId,
  onAuthError,
  className,
}: HeatmapPanelProps) {
  return (
    <section
      aria-label="Heatmap"
      className={`flex min-w-0 flex-col gap-3 rounded-2xl border border-mutedTeal/15 bg-washedSky/70 p-4 shadow-[0_8px_30px_rgba(53,89,107,0.08)] ${className ?? ""}`}
    >
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-[15px] font-semibold text-slate">Heatmap</h2>
        <span className="font-mono text-[11px] text-inkSoft">{appId}</span>
      </header>
      {active ? (
        <LiveHeatmap
          spec={spec}
          appId={appId}
          authorityKey={authorityKey}
          selectedLayerId={selectedLayerId}
          onAuthError={onAuthError}
        />
      ) : (
        <ProposedSchematic spec={spec} selectedLayerId={selectedLayerId} />
      )}
    </section>
  );
}

function maxValue(m: MapResponse | null, id: string): number | null {
  const fc = m?.layers[id];
  if (!fc) return null;
  let mx = 0;
  for (const f of fc.features) if (f.properties.value > mx) mx = f.properties.value;
  return mx;
}

function Pill({ children, tone }: { children: ReactNode; tone: "live" | "warn" | "neutral" }) {
  const cls =
    tone === "live"
      ? "bg-seaGlass/25 text-mutedTeal"
      : tone === "warn"
        ? "bg-coral/20 text-[#B4664C]"
        : "bg-softStone/50 text-ink";
  return <span className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 font-mono text-[11px] ${cls}`}>{children}</span>;
}

function LiveHeatmap({
  spec,
  appId,
  authorityKey,
  selectedLayerId,
  onAuthError,
}: {
  spec: AppSpec;
  appId: string;
  authorityKey: string | null;
  selectedLayerId: string | null;
  onAuthError?: () => void;
}) {
  const live = useLiveAppData(appId, authorityKey, { onAuthError });
  const { map, hotspots } = live;
  const ids = map ? Object.keys(map.layers) : [];

  // Which layer to draw: the selected one if this key can see it, else score, else the first visible one.
  const selectedVisible = selectedLayerId !== null && ids.includes(selectedLayerId);
  const shown = selectedVisible ? selectedLayerId : ids.includes("score") ? "score" : (ids[0] ?? null);
  const selectedHidden = selectedLayerId !== null && map !== null && !selectedVisible;

  const bbox: GeoJsonBBox = map?.bbox ?? [spec.grid.bbox.min_lon, spec.grid.bbox.min_lat, spec.grid.bbox.max_lon, spec.grid.bbox.max_lat];
  const cellM = map?.cell_m ?? spec.grid.cell_m;
  const shape = gridShape(bbox, cellM);
  const maxScore = maxValue(map, "score");
  const hsCount = hotspots?.hotspots.length ?? null;
  const updated = live.lastUpdate ? new Date(live.lastUpdate).toLocaleTimeString() : null;
  const emptyShown = map !== null && shown !== null && (map.layers[shown]?.features.length ?? 0) === 0;

  const conn =
    live.streamState === "open" ? "live (ws)" : live.streamState === "connecting" ? "connecting" : "polling (rest)";

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Pill tone={live.connected ? "live" : "neutral"}>
          <span
            aria-hidden
            className={`h-1.5 w-1.5 rounded-full ${live.connected ? "bg-mutedTeal motion-safe:animate-pulse" : "bg-inkSoft"}`}
          />
          {conn}
        </Pill>
        <Pill tone={live.authority ? "live" : "neutral"}>{live.authority ? "authority view" : "public view"}</Pill>
        {shown && <Pill tone="neutral">showing: {shown}</Pill>}
      </div>

      {live.keyRejected && (
        <p role="alert" className="rounded-lg bg-coral/15 px-3 py-2 text-[13px] text-[#9A5440]">
          Authority key rejected. Showing the public view only.
        </p>
      )}
      {live.error && !live.keyRejected && (
        <p role="alert" className="rounded-lg bg-coral/15 px-3 py-2 text-[13px] text-[#9A5440]">
          {live.error}
        </p>
      )}
      {selectedHidden && (
        <p className="font-mono text-[11px] text-ink">
          {selectedLayerId} isn't visible with the current key{shown ? `; showing ${shown}` : ""}.
        </p>
      )}

      <div className="relative">
        <Map
          bbox={bbox}
          layers={map?.layers ?? {}}
          visible={shown ? [shown] : []}
          hotspots={hotspots?.hotspots ?? []}
          cellM={cellM}
          height={440}
        />
        <div className="pointer-events-none absolute bottom-2 left-2 z-[500] rounded-md bg-cream/85 px-2 py-1 font-mono text-[10px] text-ink shadow-sm">
          SW {fmtLatLon(bbox[1], bbox[0])}
        </div>
        <div className="pointer-events-none absolute right-2 top-2 z-[500] rounded-md bg-cream/85 px-2 py-1 font-mono text-[10px] text-ink shadow-sm">
          NE {fmtLatLon(bbox[3], bbox[2])}
        </div>
        {(map === null || emptyShown || shown === null) && (
          <div className="pointer-events-none absolute inset-0 z-[500] flex items-center justify-center">
            <p className="rounded-lg bg-mist/90 px-3 py-1.5 font-mono text-[12px] text-ink shadow-sm">
              {map === null
                ? "waiting for the first tick"
                : shown === null
                  ? "no layers visible with the current key"
                  : "no cells above 0.01 yet: waiting for events"}
            </p>
          </div>
        )}
      </div>

      <div className="flex flex-col gap-1 font-mono text-[10px] text-inkSoft">
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
            tone: maxScore === null ? "muted" : maxScore >= (spec.outputs?.hotspot_threshold ?? 0.5) ? "warm" : "default",
            title: maxScore === null ? "The score layer isn't visible with the current key" : undefined,
          },
          { label: "hotspots", value: hsCount === null ? "..." : String(hsCount), tone: hsCount ? "warm" : "default" },
          { label: "updated", value: updated ?? "...", title: live.lastUpdate ?? undefined },
          { label: "connection", value: conn, tone: live.connected ? "default" : "muted" },
          { label: "cell", value: `${cellM} m · ${shape.rows} x ${shape.cols}`, title: `${(shape.rows * shape.cols).toLocaleString()} cells` },
          {
            label: "bbox (sw → ne)",
            value: `${fmtLatLon(bbox[1], bbox[0])} → ${fmtLatLon(bbox[3], bbox[2])}`,
            wide: true,
          },
          { label: "state", value: live.authority ? "active · authority" : "active · public" },
        ]}
      />
    </div>
  );
}
