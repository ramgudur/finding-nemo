import L from "leaflet";
import { useEffect, useRef } from "react";
import type { CellCollection, GeoJsonBBox, Hotspot, POI } from "../../api/types";
import { usePrefersReducedMotion } from "../../hooks/usePrefersReducedMotion";
import { bboxToBounds, gridShape, heatColor, heatOpacity, lineStride, toLatLng } from "./geo";
import "./map.css";

interface Props {
  bbox: GeoJsonBBox | null;
  layers: Record<string, CellCollection>;
  /** Layer ids to draw, in order (later ones on top). Ids absent from `layers` are skipped. */
  visible: string[];
  hotspots: Hotspot[];
  /** POIs as the server returned them for this key (already filtered per item, rule 8). */
  pois?: POI[];
  /** Labels the spec marks as routing destinations (null when the spec isn't visible). */
  destinationLabels?: ReadonlySet<string> | null;
  /** Grid cell size; when given, subtle grid lines are drawn over the bbox. */
  cellM?: number;
  height?: number | string;
  className?: string;
  /** When set, a click on the map reports its position (the cursor becomes a crosshair). */
  onPick?: ((lat: number, lon: number) => void) | null;
  /** A marked point with a radius (e.g. where test events will land). */
  marker?: { lat: number; lon: number; radius_m: number } | null;
}

/** Cells at or above this value also get a blurred copy underneath (the soft glow). */
const GLOW_FROM = 0.6;
const FADE_MS = 700;
const MAX_GRID_LINES = 64; // per axis; larger grids draw every k-th boundary
const MAX_STAGGER_MS = 650;
const MAX_EVIDENCE = 3;
const EVIDENCE_CHARS = 140;

const clip = (t: string, n: number) => (t.length > n ? `${t.slice(0, n - 1).trimEnd()}…` : t);

/** Built with textContent only: labels and evidence are agent text and never go through innerHTML. */
function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls: string, text?: string): HTMLElementTagNameMap[K] {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function poiIcon(p: POI, destination: boolean): L.DivIcon {
  const root = el("div", `hm-poi${destination ? " hm-poi--dest" : ""}`);
  root.appendChild(el("span", "hm-poi-dot"));
  const chip = el("span", "hm-poi-chip");
  chip.appendChild(el("span", "hm-poi-label", clip(p.label, 28)));
  chip.appendChild(el("span", "hm-poi-conf", p.confidence.toFixed(2)));
  if (destination) chip.appendChild(el("span", "hm-poi-badge hm-poi-badge--dest", "destination"));
  if (p.visibility === "authority") chip.appendChild(el("span", "hm-poi-badge", "authority only"));
  root.appendChild(chip);
  return L.divIcon({ html: root, className: "hm-poi-icon", iconSize: [0, 0], iconAnchor: [0, 0] });
}

function poiPopup(p: POI, destination: boolean): HTMLElement {
  const root = el("div", "hm-poi-pop");
  const head = el("div", "hm-poi-pop-head");
  head.appendChild(el("strong", "", p.label));
  head.appendChild(el("span", "hm-poi-conf", `confidence ${p.confidence.toFixed(2)}`));
  root.appendChild(head);
  const meta = [
    destination ? "routing destination" : null,
    p.visibility === "authority" ? "authority only" : "public",
    `r ${Math.round(p.radius_m)} m`,
  ]
    .filter(Boolean)
    .join(" · ");
  root.appendChild(el("div", "hm-poi-pop-meta", meta));
  if (p.evidence.length) {
    const ul = el("ul", "hm-poi-pop-ev");
    for (const e of p.evidence.slice(0, MAX_EVIDENCE)) {
      const li = el("li", "");
      li.appendChild(el("span", "hm-poi-pop-kind", e.kind));
      li.appendChild(document.createTextNode(` ${clip(e.text, EVIDENCE_CHARS)}`));
      ul.appendChild(li);
    }
    if (p.evidence.length > MAX_EVIDENCE) ul.appendChild(el("li", "hm-poi-pop-more", `+${p.evidence.length - MAX_EVIDENCE} more`));
    root.appendChild(ul);
  } else root.appendChild(el("div", "hm-poi-pop-meta", "No evidence recorded."));
  return root;
}

interface CellEntry {
  poly: L.Polygon;
  glow: L.Polygon | null;
  value: number;
  removeTimer?: ReturnType<typeof setTimeout>;
}

const ring = (coords: number[][][]): L.LatLngTuple[] =>
  (coords[0] ?? []).map(([lon = 0, lat = 0]) => toLatLng(lat, lon));

function paint(el: Element | undefined, v: number, opacity: number, delayMs = 0) {
  if (!(el instanceof SVGElement)) return;
  el.style.transitionDelay = delayMs ? `${delayMs}ms` : "";
  el.style.fill = heatColor(v);
  el.style.fillOpacity = String(opacity);
}

/**
 * Plain Leaflet inside React: one map instance; cells are long-lived paths keyed by layer/row/col and
 * restyled in place each tick, so values ease between frames instead of flashing.
 */
export function Map({ bbox, layers, visible, hotspots, pois, destinationLabels, cellM, height = "min(70vh, 560px)", className, onPick, marker }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const renderers = useRef<{ cells: L.Renderer; glow: L.Renderer } | null>(null);
  const cells = useRef(new globalThis.Map<string, CellEntry>());
  const gridGroup = useRef<L.LayerGroup | null>(null);
  const hotspotGroup = useRef<L.LayerGroup | null>(null);
  const poiGroup = useRef<L.LayerGroup | null>(null);
  const markerGroup = useRef<L.LayerGroup | null>(null);
  const onPickRef = useRef(onPick);
  onPickRef.current = onPick;
  const fittedFor = useRef("");
  const reduced = usePrefersReducedMotion();
  const reducedRef = useRef(reduced);
  reducedRef.current = reduced;

  useEffect(() => {
    if (!el.current) return;
    const m = L.map(el.current, { zoomControl: true, attributionControl: true, zoomSnap: 0.25 });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 20,
      maxNativeZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(m);
    m.setView([0, 0], 2);

    // Panes, bottom to top: tiles (200) < grid < glow < cells < hotspots.
    const pane = (name: string, z: number) => {
      const p = m.createPane(name);
      p.style.zIndex = String(z);
      p.style.pointerEvents = "none";
      return p;
    };
    pane("hm-grid", 360);
    pane("hm-glow", 380);
    pane("hm-cells", 400);
    pane("hm-hotspots", 450).style.pointerEvents = "auto";
    pane("hm-pois", 620).style.pointerEvents = "auto";

    renderers.current = {
      cells: L.svg({ pane: "hm-cells", padding: 0.5 }),
      glow: L.svg({ pane: "hm-glow", padding: 0.5 }),
    };
    gridGroup.current = L.layerGroup().addTo(m);
    hotspotGroup.current = L.layerGroup().addTo(m);
    poiGroup.current = L.layerGroup().addTo(m);
    pane("hm-marker", 640);
    markerGroup.current = L.layerGroup().addTo(m);
    m.on("click", (e: L.LeafletMouseEvent) => onPickRef.current?.(e.latlng.lat, e.latlng.lng));
    map.current = m;
    const cellMap = cells.current;
    return () => {
      for (const c of cellMap.values()) clearTimeout(c.removeTimer);
      cellMap.clear();
      m.remove();
      map.current = null;
      renderers.current = null;
      fittedFor.current = "";
    };
  }, []);

  // Fit once per bbox (a new app or a changed grid), not on every tick.
  useEffect(() => {
    const m = map.current;
    if (!m || !bbox) return;
    const k = bbox.join(",");
    if (fittedFor.current === k) return;
    m.fitBounds(bboxToBounds(bbox), { padding: [12, 12] });
    fittedFor.current = k;
  }, [bbox]);

  // Subtle grid lines over the bbox.
  useEffect(() => {
    const g = gridGroup.current;
    if (!g) return;
    g.clearLayers();
    if (!bbox || !cellM) return;
    const [minLon, minLat, maxLon, maxLat] = bbox;
    const s = gridShape(bbox, cellM);
    const dLat = cellM / s.mPerDegLat;
    const dLon = cellM / s.mPerDegLon;
    const top = minLat + s.rows * dLat; // last row may run past the bbox (ceil), as on the server
    const right = minLon + s.cols * dLon;
    const style: L.PolylineOptions = {
      pane: "hm-grid",
      color: "#638F91",
      weight: 0.6,
      opacity: 0.16,
      interactive: false,
    };
    const rs = lineStride(s.rows, MAX_GRID_LINES);
    const cs = lineStride(s.cols, MAX_GRID_LINES);
    for (let i = 0; i <= s.rows; i += rs) {
      const lat = minLat + i * dLat;
      L.polyline([toLatLng(lat, minLon), toLatLng(lat, right)], style).addTo(g);
    }
    for (let j = 0; j <= s.cols; j += cs) {
      const lon = minLon + j * dLon;
      L.polyline([toLatLng(minLat, lon), toLatLng(top, lon)], style).addTo(g);
    }
    // The bbox itself, a touch stronger.
    L.rectangle(bboxToBounds([minLon, minLat, maxLon, maxLat]), {
      pane: "hm-grid",
      color: "#638F91",
      weight: 1,
      opacity: 0.4,
      dashArray: "3 5",
      fill: false,
      interactive: false,
    }).addTo(g);
  }, [bbox, cellM]);

  // Cells: create, restyle in place, or fade out and remove.
  useEffect(() => {
    const m = map.current;
    const r = renderers.current;
    if (!m || !r) return;
    const reducedNow = reducedRef.current;
    const seen = new Set<string>();
    const fresh: Array<{ entry: CellEntry; delay: number }> = [];
    let maxRc = 1;
    for (const id of visible) for (const f of layers[id]?.features ?? []) maxRc = Math.max(maxRc, f.properties.row + f.properties.col);

    for (const id of visible) {
      const fc = layers[id];
      if (!fc) continue;
      for (const f of fc.features) {
        const { value, row, col } = f.properties;
        const key = `${id}|${row}|${col}`;
        seen.add(key);
        let entry = cells.current.get(key);
        if (!entry) {
          const latlngs = ring(f.geometry.coordinates);
          const poly = L.polygon(latlngs, {
            renderer: r.cells,
            pane: "hm-cells",
            className: "hm-cell",
            stroke: true,
            color: "#F0EBDD",
            weight: 0.4,
            opacity: 0.35,
            fillOpacity: 0,
            interactive: false,
          }).addTo(m);
          entry = { poly, glow: null, value };
          cells.current.set(key, entry);
          // Progressive fade-in, sweeping from the SW corner (row 0, col 0) outward.
          fresh.push({ entry, delay: reducedNow ? 0 : Math.round(((row + col) / maxRc) * MAX_STAGGER_MS) });
        } else {
          clearTimeout(entry.removeTimer);
          entry.removeTimer = undefined;
          entry.value = value;
          paint(entry.poly.getElement(), value, heatOpacity(value));
        }
        entry.value = value;
        // glow copy only for hot cells
        if (value >= GLOW_FROM && !entry.glow) {
          entry.glow = L.polygon(entry.poly.getLatLngs() as L.LatLng[][], {
            renderer: r.glow,
            pane: "hm-glow",
            className: "hm-cell",
            stroke: false,
            fillOpacity: 0,
            interactive: false,
          }).addTo(m);
          paint(entry.glow.getElement(), value, 0);
          const g = entry.glow;
          requestAnimationFrame(() => requestAnimationFrame(() => paint(g.getElement(), value, 0.8)));
        } else if (entry.glow) {
          if (value >= GLOW_FROM) paint(entry.glow.getElement(), value, 0.8);
          else {
            entry.glow.remove();
            entry.glow = null;
          }
        }
      }
    }

    if (fresh.length) {
      for (const { entry } of fresh) paint(entry.poly.getElement(), entry.value, 0);
      // two frames so the browser commits opacity 0 before the transition target
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          for (const { entry, delay } of fresh) paint(entry.poly.getElement(), entry.value, heatOpacity(entry.value), delay);
        }),
      );
    }

    for (const [key, entry] of cells.current) {
      if (seen.has(key) || entry.removeTimer) continue;
      paint(entry.poly.getElement(), entry.value, 0);
      if (entry.glow) paint(entry.glow.getElement(), entry.value, 0);
      entry.removeTimer = setTimeout(
        () => {
          entry.poly.remove();
          entry.glow?.remove();
          cells.current.delete(key);
        },
        reducedNow ? 0 : FADE_MS,
      );
    }
  }, [layers, visible]);

  // Hotspots: soft coral rings, few enough to redraw each tick.
  useEffect(() => {
    const g = hotspotGroup.current;
    if (!g) return;
    g.clearLayers();
    for (const h of hotspots) {
      L.circle(toLatLng(h.lat, h.lon), {
        pane: "hm-hotspots",
        className: "hm-hotspot",
        radius: h.radius_m,
        color: "#D9937E",
        weight: 1.5,
        opacity: 0.9,
        dashArray: "4 4",
        fillColor: "#E8875F",
        fillOpacity: 0.06,
      })
        .bindTooltip(
          `${h.id} · peak ${h.score.toFixed(2)} · mean ${h.mean.toFixed(2)} · r ${Math.round(h.radius_m)} m · ${h.persisted_ticks} ticks`,
          { className: "hm-tooltip", direction: "top" },
        )
        .addTo(g);
    }
  }, [hotspots]);

  // POIs: a pin plus its label chip; the popup shows the evidence briefly. Few, and refreshed every
  // ~10 s, so they are simply redrawn.
  useEffect(() => {
    const g = poiGroup.current;
    if (!g) return;
    g.clearLayers();
    for (const p of pois ?? []) {
      if (p.status !== "active") continue;
      const dest = destinationLabels?.has(p.label) ?? false;
      L.circle(toLatLng(p.lat, p.lon), {
        pane: "hm-pois",
        radius: p.radius_m,
        color: dest ? "#3B6895" : "#35596B",
        weight: 1,
        opacity: 0.5,
        fillOpacity: 0.04,
        interactive: false,
      }).addTo(g);
      L.marker(toLatLng(p.lat, p.lon), {
        pane: "hm-pois",
        icon: poiIcon(p, dest),
        keyboard: true,
        title: `${p.label} (${p.confidence.toFixed(2)})`,
        riseOnHover: true,
      })
        .bindPopup(poiPopup(p, dest), { className: "hm-popup", maxWidth: 280 })
        .addTo(g);
    }
  }, [pois, destinationLabels]);

  // The marked point: a coral ring for the radius and a small centre dot.
  useEffect(() => {
    const g = markerGroup.current;
    if (!g) return;
    g.clearLayers();
    if (!marker) return;
    const at = toLatLng(marker.lat, marker.lon);
    L.circle(at, { pane: "hm-marker", radius: marker.radius_m, color: "#C46F52", weight: 1.5, dashArray: "2 4", fillColor: "#D9937E", fillOpacity: 0.12, interactive: false }).addTo(g);
    L.circleMarker(at, { pane: "hm-marker", radius: 4, color: "#F0EBDD", weight: 2, fillColor: "#C46F52", fillOpacity: 1, interactive: false }).addTo(g);
  }, [marker?.lat, marker?.lon, marker?.radius_m]); // eslint-disable-line react-hooks/exhaustive-deps

  // Regression guard (#284): Leaflet owns the container's classes (leaflet-container gives it
  // overflow:hidden and lifts preflight's img max-width so tiles keep their size). If anything ever
  // rewrites them again, the tiles collapse to 0 px wide and the map vanishes.
  useEffect(() => {
    if (import.meta.env.DEV && el.current && map.current && !el.current.classList.contains("leaflet-container"))
      console.error("Map: the Leaflet container lost its classes; React must not manage its className.");
  });

  // React styles only the wrapper. The inner div's className is static, so React never rewrites it
  // (toggling a class there would wipe the classes Leaflet added at mount).
  return (
    <div className={`${onPick ? "hm-map--picking" : ""} ${className ?? ""}`} style={{ height, minHeight: 280, width: "100%" }}>
      <div ref={el} className="hm-map" style={{ height: "100%", width: "100%", borderRadius: 12 }} />
    </div>
  );
}
