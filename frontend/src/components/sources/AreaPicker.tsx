// Area step body: the Leaflet map (always open), the area set from the current view or drawn as a box (toggle or
// shift-drag; plain pointer events, no plugin), optional fixed destinations placed by clicking (warned and blocked
// when outside the area), and the cell size behind "Map detail": automatic for the area unless the dev picks one,
// with the live cell count vs MAX_GRID_CELLS.
import L from "leaflet";
import { ChevronDown, Crosshair, MapPin, MousePointerSquareDashed, Scan, TriangleAlert, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { MAX_GRID_CELLS } from "../../api/types";
import type { BBox, DestinationPoint } from "../../api/types";
import { useHelp } from "../builder/Help";
import { CELL_SIZE_DETAIL, DESTINATION_DETAIL } from "../builder/explain";
import {
  AUTO_CELL_TARGET,
  CELL_SIZES,
  areaTooLarge,
  autoCellSize,
  cellSizeFits,
  cellSizeForArea,
  gridCells,
  round6,
} from "./sourceUtils";

interface Props {
  bbox: BBox | null;
  onBbox: (b: BBox) => void;
  cellM: number;
  /** True when the dev picked the cell size by hand (otherwise it follows the area). */
  cellManual: boolean;
  onCellM: (m: number) => void;
  onCellAuto: () => void;
  points: DestinationPoint[];
  onAddPoint: (p: DestinationPoint) => void;
  onUpdatePoint: (index: number, patch: Partial<DestinationPoint>) => void;
  onRemovePoint: (index: number) => void;
}

/** A colour token from tokens.css as "rgb(r, g, b)" (Leaflet path options take no CSS variables). */
function tokenRgb(el: Element, name: string, fallback: string): string {
  const v = getComputedStyle(el).getPropertyValue(name).trim();
  return v ? `rgb(${v.split(/\s+/).join(", ")})` : fallback;
}

/** A bbox from two corners in any order, rounded like "use this view". */
function cornersToBbox(a: L.LatLng, b: L.LatLng): BBox {
  return {
    min_lat: round6(Math.min(a.lat, b.lat)),
    min_lon: round6(Math.min(a.lng, b.lng)),
    max_lat: round6(Math.max(a.lat, b.lat)),
    max_lon: round6(Math.max(a.lng, b.lng)),
  };
}

/** A drag shorter than this (screen pixels on both axes) is a stray click, not an area. */
const MIN_DRAW_PX = 8;

function LeafletArea({
  bbox,
  points,
  onPick,
  mapRef,
  drawing,
  onDrawn,
  onDrawEnd,
  preview,
}: {
  bbox: BBox | null;
  points: DestinationPoint[];
  onPick: ((lat: number, lon: number) => void) | null;
  mapRef: React.MutableRefObject<L.Map | null>;
  /** Draw mode: a drag (mouse or touch) draws the area instead of panning. Shift-drag draws at any time. */
  drawing: boolean;
  onDrawn: (b: BBox) => void;
  /** A draw gesture finished or was cancelled (or Escape in draw mode), so the toggle turns itself off. */
  onDrawEnd: () => void;
  /** One line for the live box: its size and the cells it would give. */
  preview: (b: BBox) => string;
}) {
  const el = useRef<HTMLDivElement>(null);
  const rect = useRef<L.Rectangle | null>(null);
  const marks = useRef<L.LayerGroup | null>(null);
  const initialBbox = useRef(bbox);
  const [live, setLive] = useState<string | null>(null);
  // The native listeners are bound once; they read the latest props from here.
  const latest = useRef({ drawing, onDrawn, onDrawEnd, preview });
  latest.current = { drawing, onDrawn, onDrawEnd, preview };

  useEffect(() => {
    if (!el.current) return;
    // boxZoom off: shift-drag draws the area here instead of zooming to a box.
    const m = L.map(el.current, { zoomControl: true, attributionControl: true, boxZoom: false });
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

  // Drawing: pointer events on the map container in the capture phase, so Leaflet's pan never sees a draw drag.
  useEffect(() => {
    const m = mapRef.current;
    const box = el.current;
    if (!m || !box) return;
    let start: { latlng: L.LatLng; x: number; y: number; id: number } | null = null;
    let shape: L.Rectangle | null = null;
    let swallowClick = false;

    const stop = (e: Event) => {
      e.stopImmediatePropagation();
      e.stopPropagation();
    };
    const onControl = (e: Event) => !!(e.target as Element | null)?.closest?.(".leaflet-control");
    const finish = (end: L.LatLng | null) => {
      const s = start;
      if (!s) return;
      start = null;
      shape?.remove();
      shape = null;
      setLive(null);
      if (box.hasPointerCapture?.(s.id)) box.releasePointerCapture(s.id);
      if (end) {
        const nb = cornersToBbox(s.latlng, end);
        latest.current.onDrawn(nb);
        m.fitBounds(
          [
            [nb.min_lat, nb.min_lon],
            [nb.max_lat, nb.max_lon],
          ],
          { padding: [24, 24] },
        );
      }
      latest.current.onDrawEnd();
    };

    const down = (e: PointerEvent) => {
      if (!start) swallowClick = false; // a drag that ended without a click must not eat this one
      if (start || e.button !== 0 || onControl(e)) return;
      if (!(latest.current.drawing || (e.pointerType === "mouse" && e.shiftKey))) return;
      stop(e);
      e.preventDefault(); // also suppresses the compatibility mousedown Leaflet would pan on
      start = { latlng: m.mouseEventToLatLng(e), x: e.clientX, y: e.clientY, id: e.pointerId };
      swallowClick = true;
      box.setPointerCapture?.(e.pointerId);
      const deep = tokenRgb(box, "--c-deep", "rgb(43, 86, 112)");
      shape = L.rectangle(L.latLngBounds(start.latlng, start.latlng), {
        color: deep,
        weight: 2,
        fillColor: deep,
        fillOpacity: 0.1,
        interactive: false,
      }).addTo(m);
    };
    const move = (e: PointerEvent) => {
      if (!start || e.pointerId !== start.id) return;
      stop(e);
      const here = m.mouseEventToLatLng(e);
      shape?.setBounds(L.latLngBounds(start.latlng, here));
      setLive(latest.current.preview(cornersToBbox(start.latlng, here)));
    };
    const up = (e: PointerEvent) => {
      if (!start || e.pointerId !== start.id) return;
      stop(e);
      const far = Math.abs(e.clientX - start.x) >= MIN_DRAW_PX || Math.abs(e.clientY - start.y) >= MIN_DRAW_PX;
      finish(far ? m.mouseEventToLatLng(e) : null);
    };
    const cancel = (e: PointerEvent) => {
      if (start && e.pointerId === start.id) finish(null);
    };
    // Leaflet also listens to mouse and touch events: keep them away from it while drawing is possible.
    const guard = (e: MouseEvent | TouchEvent) => {
      if (onControl(e)) return;
      if (start || latest.current.drawing || (e instanceof MouseEvent && e.shiftKey)) stop(e);
    };
    // The click that ends a drag must not drop a destination.
    const click = (e: MouseEvent) => {
      if (!swallowClick) return;
      swallowClick = false;
      stop(e);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (start) finish(null);
      else if (latest.current.drawing) latest.current.onDrawEnd();
    };

    const cap = { capture: true } as const;
    const capPassive = { capture: true, passive: true } as const;
    box.addEventListener("pointerdown", down, cap);
    box.addEventListener("pointermove", move, cap);
    box.addEventListener("pointerup", up, cap);
    box.addEventListener("pointercancel", cancel, cap);
    box.addEventListener("mousedown", guard, cap);
    box.addEventListener("touchstart", guard, capPassive);
    box.addEventListener("click", click, cap);
    window.addEventListener("keydown", key);
    return () => {
      box.removeEventListener("pointerdown", down, cap);
      box.removeEventListener("pointermove", move, cap);
      box.removeEventListener("pointerup", up, cap);
      box.removeEventListener("pointercancel", cancel, cap);
      box.removeEventListener("mousedown", guard, cap);
      box.removeEventListener("touchstart", guard, capPassive);
      box.removeEventListener("click", click, cap);
      window.removeEventListener("keydown", key);
      shape?.remove();
    };
  }, [mapRef]);

  // Draw mode: no panning, and a touch drag draws instead of scrolling the page.
  useEffect(() => {
    const m = mapRef.current;
    if (!m || !drawing) return;
    const c = m.getContainer();
    m.dragging.disable();
    c.style.touchAction = "none";
    c.style.cursor = "crosshair";
    return () => {
      m.dragging.enable();
      c.style.touchAction = "";
      c.style.cursor = "";
    };
  }, [drawing, mapRef]);

  useEffect(() => {
    const m = mapRef.current;
    if (!m) return;
    rect.current?.remove();
    const c = m.getContainer();
    rect.current = bbox
      ? L.rectangle(
          [
            [bbox.min_lat, bbox.min_lon],
            [bbox.max_lat, bbox.max_lon],
          ],
          {
            color: tokenRgb(c, "--c-primary", "rgb(59, 104, 149)"),
            weight: 2,
            fillColor: tokenRgb(c, "--c-soft-aqua", "rgb(169, 210, 213)"),
            fillOpacity: 0.12,
            dashArray: "6 6",
            interactive: false,
          },
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
    if (!m || !onPick || drawing) return;
    const click = (e: L.LeafletMouseEvent) => onPick(round6(e.latlng.lat), round6(e.latlng.lng));
    m.on("click", click);
    m.getContainer().style.cursor = "crosshair";
    return () => {
      m.off("click", click);
      m.getContainer().style.cursor = "";
    };
  }, [onPick, drawing, mapRef]);

  const tip = live ?? (drawing ? "Drag a box over the area · Esc cancels" : null);
  return (
    <div className="relative">
      <div ref={el} className="sources-map h-80 w-full overflow-hidden rounded-2xl sm:h-[26rem]" />
      {tip && (
        <p
          className={`pointer-events-none absolute left-1/2 top-3 z-[1000] max-w-[calc(100%-1.5rem)] -translate-x-1/2 rounded-full px-3 py-1 text-center text-xs shadow-md ${
            live ? "bg-deep font-mono text-onDeep" : "bg-sheet font-semibold text-slate"
          }`}
        >
          {tip}
        </p>
      )}
    </div>
  );
}

/** "Auto: 50 m · 180 × 210 cells" (cols × rows, i.e. width × height like areaSize). */
export function cellSummary(b: BBox, cellM: number, manual: boolean): string {
  const [rows, cols] = gridCells(b, cellM);
  return `${manual ? "Set by hand" : "Auto"}: ${cellM} m · ${cols.toLocaleString()} × ${rows.toLocaleString()} cells`;
}

export function AreaPicker(props: Props) {
  const { bbox, onBbox, cellM, cellManual, onCellM, onCellAuto, points, onAddPoint, onUpdatePoint, onRemovePoint } = props;
  const [placing, setPlacing] = useState(false);
  const [drawing, setDrawing] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const mapRef = useRef<L.Map | null>(null);
  const destHelp = useHelp("fixed destinations", DESTINATION_DETAIL);
  const cellHelp = useHelp("cell size", CELL_SIZE_DETAIL);

  const dims = bbox && cellM > 0 ? gridCells(bbox, cellM) : null;
  const cells = dims ? dims[0] * dims[1] : 0;
  const over = cells > MAX_GRID_CELLS;
  const tooLarge = !!bbox && areaTooLarge(bbox);
  const auto = bbox ? autoCellSize(bbox) : null;
  // Advanced opens itself when the cell size is the reason the step can't continue.
  const showAdvanced = advanced || (over && !tooLarge) || !(cellM > 0);
  const outside = (p: DestinationPoint) =>
    !!bbox && (p.lat < bbox.min_lat || p.lat > bbox.max_lat || p.lon < bbox.min_lon || p.lon > bbox.max_lon);

  const onPick = useCallback((lat: number, lon: number) => onAddPoint({ lat, lon, label: "" }), [onAddPoint]);
  const onDrawEnd = useCallback(() => setDrawing(false), []);
  // The live box's line: what the area would be, with the cell size rule 1 would give it.
  const preview = useCallback(
    (b: BBox) => {
      const size = areaSize(b).replace(/^about /, "");
      if (areaTooLarge(b)) return `${size} · too large: shrink it`;
      const next = cellSizeForArea(b, cellM, cellManual);
      return `${size} · ${cellSummary(b, next.cellM, next.manual)}`;
    },
    [cellM, cellManual],
  );

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

  const toggleDraw = () => {
    setDrawing((d) => !d);
    setPlacing(false);
  };
  const togglePlacing = () => {
    setPlacing((p) => !p);
    setDrawing(false);
  };

  const outsideCount = points.filter(outside).length;
  const them = outsideCount === 1 ? "it" : "them";

  return (
    <div className="flex flex-col gap-8">
      <section aria-label="Area map" className="flex flex-col gap-3">
        <LeafletArea
          bbox={bbox}
          points={points}
          onPick={placing ? onPick : null}
          mapRef={mapRef}
          drawing={drawing}
          onDrawn={onBbox}
          onDrawEnd={onDrawEnd}
          preview={preview}
        />
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <button type="button" onClick={useView} className={`bld-btn tap ${bbox ? "bld-btn-secondary" : "bld-btn-primary"}`}>
            <Scan size={15} aria-hidden /> {bbox ? "Use this view instead" : "Use this view as the area"}
          </button>
          <button
            type="button"
            onClick={toggleDraw}
            aria-pressed={drawing}
            title="Drag a box on the map to set the area (or hold Shift and drag at any time)"
            className={`bld-btn tap ${drawing ? "bld-btn-primary" : "bld-btn-secondary"}`}
          >
            <MousePointerSquareDashed size={15} aria-hidden /> {drawing ? "Drawing: drag a box" : "Draw area"}
          </button>
        </div>
        <p className="text-sm text-ink" aria-live="polite">
          {bbox ? (
            <>
              Area set: <span className="font-semibold text-slate">{areaSize(bbox)}</span>
              {tooLarge ? (
                <span className="font-semibold text-danger"> · Area too large: shrink it</span>
              ) : (
                cellM > 0 && (
                  <span className={over ? "font-semibold text-danger" : "text-ink"}>
                    {" "}
                    · {cellSummary(bbox, cellM, cellManual)}
                    {over && " (over the limit)"}
                  </span>
                )
              )}
            </>
          ) : (
            "Pan and zoom until the map shows the whole area, then use the view, or draw a box around it."
          )}
        </p>
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
            onClick={togglePlacing}
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
              · {bbox && cellM > 0 ? cellSummary(bbox, cellM, cellManual) : `cells of ${cellM} m`}
            </span>
          )}
        </button>
        {showAdvanced && (
          <div id="area-advanced" className="bld-card-quiet flex flex-col gap-3 px-5 py-4">
            <div className="flex items-center gap-1.5">
              <span id="cell-h" className="bld-label">
                Cell size
              </span>
              {cellHelp.button}
            </div>
            {cellHelp.panel}
            <div role="group" aria-labelledby="cell-h" className="flex flex-wrap items-center gap-2">
              {CELL_SIZES.map((m) => {
                const fits = !bbox || cellSizeFits(bbox, m);
                const on = cellM === m;
                return (
                  <button
                    key={m}
                    type="button"
                    disabled={!fits}
                    aria-pressed={on}
                    onClick={() => onCellM(m)}
                    title={fits ? `${m} m cells` : `${m} m cells would be over ${MAX_GRID_CELLS.toLocaleString()} cells for this area`}
                    className={`bld-btn bld-btn-sm tap font-mono ${on ? "bld-btn-primary" : "bld-btn-secondary"} ${fits ? "" : "opacity-45"}`}
                  >
                    {m} m{auto === m && !cellManual ? " · auto" : ""}
                  </button>
                );
              })}
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <label htmlFor="cell-m" className="text-sm text-ink">
                Or any size
              </label>
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
                  {dims[1].toLocaleString()} × {dims[0].toLocaleString()} = {cells.toLocaleString()} of{" "}
                  {MAX_GRID_CELLS.toLocaleString()} cells
                </span>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <p className={`bld-hint min-w-0 flex-1 ${over ? "font-semibold text-danger" : ""}`}>
                {tooLarge
                  ? "Area too large: even the biggest cells are over the limit. Shrink the area."
                  : over
                    ? "Too many cells: use bigger cells or a smaller area."
                    : cellManual
                      ? "Set by hand. It stays while it fits the area; a size that stops fitting goes back to auto."
                      : `Auto picks the smallest round size that stays under ${AUTO_CELL_TARGET.toLocaleString()} cells, so the live map stays quick.`}
              </p>
              {cellManual && bbox && auto !== null && (
                <button type="button" onClick={onCellAuto} className="bld-btn bld-btn-sm bld-btn-quiet tap">
                  Use auto ({auto} m)
                </button>
              )}
            </div>
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
