// Area step body: the Leaflet map (always open), "use this view" as the area, optional fixed destinations
// placed by clicking (warned and blocked when outside the area), and the cell size behind "Map detail" with the live cell count vs MAX_GRID_CELLS.
import L from "leaflet";
import { ChevronDown, Crosshair, MapPin, Scan, TriangleAlert, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { MAX_GRID_CELLS } from "../../api/types";
import type { BBox, DestinationPoint } from "../../api/types";
import { useHelp } from "../builder/Help";
import { CELL_SIZE_DETAIL, DESTINATION_DETAIL } from "../builder/explain";
import { gridCells, round6 } from "./sourceUtils";

interface Props {
  bbox: BBox | null;
  onBbox: (b: BBox) => void;
  cellM: number;
  onCellM: (m: number) => void;
  points: DestinationPoint[];
  onAddPoint: (p: DestinationPoint) => void;
  onUpdatePoint: (index: number, patch: Partial<DestinationPoint>) => void;
  onRemovePoint: (index: number) => void;
}

function LeafletArea({
  bbox,
  points,
  onPick,
  mapRef,
}: {
  bbox: BBox | null;
  points: DestinationPoint[];
  onPick: ((lat: number, lon: number) => void) | null;
  mapRef: React.MutableRefObject<L.Map | null>;
}) {
  const el = useRef<HTMLDivElement>(null);
  const rect = useRef<L.Rectangle | null>(null);
  const marks = useRef<L.LayerGroup | null>(null);
  const initialBbox = useRef(bbox);

  useEffect(() => {
    if (!el.current) return;
    const m = L.map(el.current, { zoomControl: true, attributionControl: true });
    const b = initialBbox.current;
    if (b) m.fitBounds([[b.min_lat, b.min_lon], [b.max_lat, b.max_lon]], { padding: [24, 24] });
    else m.setView([20, 0], 2);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(m);
    mapRef.current = m;
    marks.current = L.layerGroup().addTo(m);
    return () => {
      m.remove();
      mapRef.current = null;
    };
  }, [mapRef]);

  useEffect(() => {
    const m = mapRef.current;
    if (!m) return;
    rect.current?.remove();
    rect.current = bbox
      ? L.rectangle(
          [
            [bbox.min_lat, bbox.min_lon],
            [bbox.max_lat, bbox.max_lon],
          ],
          { color: "#4F80B0", weight: 2, fillColor: "#A9D2D5", fillOpacity: 0.12, dashArray: "6 6" },
        ).addTo(m)
      : null;
  }, [bbox, mapRef]);

  useEffect(() => {
    const g = marks.current;
    if (!g) return;
    g.clearLayers();
    for (const p of points) {
      L.circleMarker([p.lat, p.lon], { radius: 7, color: "#F0EBDD", weight: 2, fillColor: "#638F91", fillOpacity: 1 })
        .bindTooltip(p.label || "(no label yet)", { permanent: true, direction: "right", className: "sources-tooltip" })
        .addTo(g);
    }
  }, [points]);

  useEffect(() => {
    const m = mapRef.current;
    if (!m || !onPick) return;
    const click = (e: L.LeafletMouseEvent) => onPick(round6(e.latlng.lat), round6(e.latlng.lng));
    m.on("click", click);
    m.getContainer().style.cursor = "crosshair";
    return () => {
      m.off("click", click);
      m.getContainer().style.cursor = "";
    };
  }, [onPick, mapRef]);

  return <div ref={el} className="sources-map h-80 w-full overflow-hidden rounded-2xl sm:h-[26rem]" />;
}

export function AreaPicker(props: Props) {
  const { bbox, onBbox, cellM, onCellM, points, onAddPoint, onUpdatePoint, onRemovePoint } = props;
  const [placing, setPlacing] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const mapRef = useRef<L.Map | null>(null);
  const destHelp = useHelp("fixed destinations", DESTINATION_DETAIL);
  const cellHelp = useHelp("cell size", CELL_SIZE_DETAIL);

  const dims = bbox && cellM > 0 ? gridCells(bbox, cellM) : null;
  const cells = dims ? dims[0] * dims[1] : 0;
  const over = cells > MAX_GRID_CELLS;
  // Advanced opens itself when the cell size is the reason the step can't continue.
  const showAdvanced = advanced || over || !(cellM > 0);
  const outside = (p: DestinationPoint) =>
    !!bbox && (p.lat < bbox.min_lat || p.lat > bbox.max_lat || p.lon < bbox.min_lon || p.lon > bbox.max_lon);

  const onPick = useCallback((lat: number, lon: number) => onAddPoint({ lat, lon, label: "" }), [onAddPoint]);

  const useView = () => {
    const b = mapRef.current?.getBounds();
    if (!b) return;
    onBbox({
      min_lat: round6(b.getSouth()),
      min_lon: round6(b.getWest()),
      max_lat: round6(b.getNorth()),
      max_lon: round6(b.getEast()),
    });
  };

  const outsideCount = points.filter(outside).length;
  const them = outsideCount === 1 ? "it" : "them";

  return (
    <div className="flex flex-col gap-8">
      <section aria-label="Area map" className="flex flex-col gap-3">
        <LeafletArea bbox={bbox} points={points} onPick={placing ? onPick : null} mapRef={mapRef} />
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <button type="button" onClick={useView} className={`bld-btn tap ${bbox ? "bld-btn-secondary" : "bld-btn-primary"}`}>
            <Scan size={15} aria-hidden /> {bbox ? "Use this view instead" : "Use this view as the area"}
          </button>
          <p className="text-sm text-ink" aria-live="polite">
            {bbox ? (
              <>
                Area set: <span className="font-semibold text-slate">{areaSize(bbox)}</span>
              </>
            ) : (
              "Pan and zoom until the map shows the whole area, then use the view."
            )}
          </p>
        </div>
        {bbox && (
          <p className="font-mono text-[11px] text-ink">
            {bbox.min_lat}, {bbox.min_lon} → {bbox.max_lat}, {bbox.max_lon}
          </p>
        )}
      </section>

      <section aria-labelledby="dest-h" className="bld-card flex flex-col gap-3 px-5 py-4">
        <div className="flex flex-wrap items-start gap-3">
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <div className="flex items-center gap-1.5">
              <h2 id="dest-h" className="bld-h3">
                Fixed destinations <span className="font-normal text-ink">optional</span>
              </h2>
              {destHelp.button}
            </div>
            <p className="bld-hint">Places end users can be sent to, such as a meeting point. They are used exactly where you put them.</p>
          </div>
          <button
            type="button"
            onClick={() => setPlacing((p) => !p)}
            aria-pressed={placing}
            className={`bld-btn bld-btn-sm tap ${placing ? "bld-btn-primary" : "bld-btn-secondary"}`}
          >
            <Crosshair size={15} aria-hidden /> {placing ? "Done placing" : "Place on the map"}
          </button>
        </div>
        {destHelp.panel}
        {placing && (
          <p className="bld-note font-semibold">Click or tap the map to drop a destination. Choose Done placing when you have them all.</p>
        )}
        {points.length > 0 && (
          <ul className="flex flex-col gap-2">
            {points.map((p, k) => (
              <li key={k} className="flex flex-wrap items-center gap-2">
                <MapPin size={15} className={outside(p) ? "text-danger" : "text-mutedTeal"} aria-hidden />
                <input
                  aria-label={`Label for destination ${k + 1}`}
                  aria-invalid={!p.label.trim()}
                  placeholder="e.g. North gate"
                  value={p.label}
                  onChange={(e) => onUpdatePoint(k, { label: e.target.value })}
                  className="bld-field is-sm min-w-0 flex-1"
                />
                <span className={`font-mono text-[11px] ${outside(p) ? "font-semibold text-danger" : "text-ink"}`}>
                  {p.lat}, {p.lon}
                  {outside(p) && " · outside the area"}
                </span>
                <button
                  type="button"
                  onClick={() => onRemovePoint(k)}
                  aria-label={`Remove destination ${p.label || k + 1}`}
                  className="tap flex items-center justify-center rounded-full p-1.5 text-ink transition-colors duration-150 hover:bg-white/60 hover:text-slate"
                >
                  <X size={14} aria-hidden />
                </button>
              </li>
            ))}
          </ul>
        )}
        {outsideCount > 0 && (
          <p role="alert" className="bld-warn flex items-start gap-2">
            <TriangleAlert size={15} className="mt-0.5 shrink-0" aria-hidden />
            <span>
              {outsideCount === 1 ? "One destination is" : `${outsideCount} destinations are`} outside the area, so the
              platform would reject {them}. Remove {them}, or frame a bigger area that includes {them}.
            </span>
          </p>
        )}
      </section>

      <section className="flex flex-col gap-3">
        <button
          type="button"
          onClick={() => setAdvanced((a) => !a)}
          aria-expanded={showAdvanced}
          aria-controls="area-advanced"
          className="tap flex items-center gap-1.5 self-start rounded-full px-2 py-1 text-left text-sm font-bold text-slate transition-colors duration-150 hover:bg-white/45"
        >
          <ChevronDown
            size={16}
            aria-hidden
            className={`transition-transform duration-200 ${showAdvanced ? "rotate-180" : ""}`}
          />
          Map detail
          {!showAdvanced && (
            <span className="font-normal text-ink">
              · cells of {cellM} m{dims ? `, ${cells.toLocaleString()} in all` : ""}
            </span>
          )}
        </button>
        {showAdvanced && (
          <div id="area-advanced" className="bld-card-quiet flex flex-col gap-2 px-5 py-4">
            <div className="flex items-center gap-1.5">
              <label htmlFor="cell-m" className="bld-label">
                Cell size
              </label>
              {cellHelp.button}
            </div>
            {cellHelp.panel}
            <div className="flex flex-wrap items-center gap-3">
              <span className="flex items-center gap-2">
                <input
                  id="cell-m"
                  type="number"
                  min={1}
                  value={Number.isFinite(cellM) ? cellM : ""}
                  aria-invalid={over || !(cellM > 0)}
                  onChange={(e) => onCellM(Number(e.target.value))}
                  className="bld-field is-mono is-sm w-24"
                />
                <span className="text-sm text-ink">metres per side</span>
              </span>
              {dims && (
                <span className={`font-mono text-xs ${over ? "font-semibold text-danger" : "text-ink"}`}>
                  {dims[0]} × {dims[1]} = {cells.toLocaleString()} of {MAX_GRID_CELLS.toLocaleString()} cells
                </span>
              )}
            </div>
            <p className={`bld-hint ${over ? "font-semibold text-danger" : ""}`}>
              {over
                ? "Too many cells: use bigger cells or a smaller area."
                : "Smaller cells give sharper hotspots; a bigger area needs bigger cells."}
            </p>
          </div>
        )}
      </section>
    </div>
  );
}

/** "about 1.2 km × 0.8 km" for a bbox, from the same grid maths as the server. */
export function areaSize(b: BBox): string {
  const [h, w] = gridCells(b, 1);
  const fmt = (m: number) => (m >= 1000 ? `${(m / 1000).toFixed(m >= 10_000 ? 0 : 1)} km` : `${m} m`);
  return `about ${fmt(w)} × ${fmt(h)}`;
}
