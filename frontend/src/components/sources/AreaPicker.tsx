// Collapsible area panel (ported from Builder.tsx): Leaflet bbox from the current view, fixed destination
// points placed by clicking, and the cell size with the resulting cell count vs MAX_GRID_CELLS.
import L from "leaflet";
import { ChevronDown, Crosshair, MapPin, Scan, X } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { MAX_GRID_CELLS } from "../../api/types";
import type { BBox, DestinationPoint } from "../../api/types";
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

  return <div ref={el} className="sources-map h-72 w-full overflow-hidden rounded-2xl sm:h-80" />;
}

export function AreaPicker(props: Props) {
  const { bbox, onBbox, cellM, onCellM, points, onAddPoint, onUpdatePoint, onRemovePoint } = props;
  const [open, setOpen] = useState(false); // collapsed by default so the big input stays above the fold
  const [placing, setPlacing] = useState(false);
  const mapRef = useRef<L.Map | null>(null);
  const reduce = useReducedMotion();

  const dims = bbox && cellM > 0 ? gridCells(bbox, cellM) : null;
  const cells = dims ? dims[0] * dims[1] : 0;
  const over = cells > MAX_GRID_CELLS;
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

  const summary = bbox
    ? `${dims?.[0]} × ${dims?.[1]} cells at ${cellM} m${points.length ? ` · ${points.length} destination${points.length > 1 ? "s" : ""}` : ""}`
    : null;

  return (
    <section className="rounded-3xl border border-white/60 bg-cream/55 shadow-[0_10px_40px_-24px_rgba(53,89,107,0.55)] backdrop-blur-[2px]">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-3 rounded-3xl px-5 py-3.5 text-left transition-colors hover:bg-white/20"
      >
        <Scan size={18} className="shrink-0 text-mutedTeal" />
        <span className="text-sm font-semibold text-slate">Area</span>
        {summary ? (
          <span className={`truncate font-mono text-xs ${over ? "text-coral" : "text-inkSoft"}`}>{summary}</span>
        ) : (
          <span className="truncate text-xs text-ink">
            not set · <span className="underline decoration-inkSoft/50 underline-offset-2">choose it on the map</span>
          </span>
        )}
        <ChevronDown
          size={18}
          className={`ml-auto shrink-0 text-ink transition-transform duration-300 ${open ? "rotate-180" : ""}`}
        />
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="area-body"
            initial={reduce ? { opacity: 0 } : { height: 0, opacity: 0 }}
            animate={reduce ? { opacity: 1 } : { height: "auto", opacity: 1 }}
            exit={reduce ? { opacity: 0 } : { height: 0, opacity: 0 }}
            transition={{ duration: reduce ? 0 : 0.32, ease: [0.22, 1, 0.36, 1] }}
            onAnimationComplete={() => {
              // The map mounted while the panel was still 0 px tall: resize it, then re-frame the area.
              const m = mapRef.current;
              if (!m) return;
              m.invalidateSize();
              if (bbox) m.fitBounds([[bbox.min_lat, bbox.min_lon], [bbox.max_lat, bbox.max_lon]], { padding: [24, 24] });
            }}
            className="overflow-hidden"
          >
            <div className="flex flex-col gap-4 px-5 pb-5">
              <LeafletArea bbox={bbox} points={points} onPick={placing ? onPick : null} mapRef={mapRef} />

              <div className="flex flex-wrap items-center gap-2">
                <button type="button" onClick={useView} className="pill-btn pill-primary">
                  <Scan size={15} /> Use this view as the area
                </button>
                <button
                  type="button"
                  onClick={() => setPlacing((p) => !p)}
                  aria-pressed={placing}
                  className={`pill-btn ${placing ? "pill-active" : "pill-quiet"}`}
                >
                  <Crosshair size={15} /> {placing ? "Click the map to drop a destination" : "Place fixed destinations"}
                </button>
                <span className="font-mono text-[11px] text-inkSoft">
                  {bbox
                    ? `${bbox.min_lat}, ${bbox.min_lon} → ${bbox.max_lat}, ${bbox.max_lon}`
                    : "Pan and zoom to the area, then use the view."}
                </span>
              </div>

              <div className="flex flex-wrap items-center gap-3">
                <label className="flex items-center gap-2 text-sm text-ink">
                  Cell size
                  <input
                    type="number"
                    min={1}
                    value={Number.isFinite(cellM) ? cellM : ""}
                    onChange={(e) => onCellM(Number(e.target.value))}
                    className="w-20 rounded-xl border border-white/70 bg-white/50 px-2.5 py-1 font-mono text-sm text-slate outline-none focus:bg-white/80"
                  />
                  <span className="text-inkSoft">m</span>
                </label>
                {dims && (
                  <span className={`font-mono text-xs ${over ? "text-coral" : "text-inkSoft"}`}>
                    {cells.toLocaleString()} / {MAX_GRID_CELLS.toLocaleString()} cells
                    {over && " · use a smaller area or bigger cells"}
                  </span>
                )}
              </div>

              {points.length > 0 && (
                <ul className="flex flex-col gap-2">
                  {points.map((p, k) => (
                    <li key={k} className="flex flex-wrap items-center gap-2">
                      <MapPin size={15} className="text-mutedTeal" />
                      <input
                        aria-label="destination label"
                        placeholder="label, e.g. the name of the place"
                        value={p.label}
                        onChange={(e) => onUpdatePoint(k, { label: e.target.value })}
                        className={`min-w-0 flex-1 rounded-xl border bg-white/50 px-3 py-1 text-sm text-slate outline-none focus:bg-white/80 ${
                          p.label.trim() ? "border-white/70" : "border-coral/60"
                        }`}
                      />
                      <span className={`font-mono text-[11px] ${outside(p) ? "text-coral" : "text-inkSoft"}`}>
                        {p.lat}, {p.lon}
                        {outside(p) && " · outside the area"}
                      </span>
                      <button
                        type="button"
                        onClick={() => onRemovePoint(k)}
                        aria-label="remove destination"
                        className="rounded-full p-1 text-ink transition hover:bg-white/50"
                      >
                        <X size={14} />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <p className="text-xs text-inkSoft">
                Fixed destinations are optional places end users can be routed to, used exactly as placed.
              </p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}
