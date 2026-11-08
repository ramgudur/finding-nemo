import L from "leaflet";
import { useEffect, useRef } from "react";
import type { CellCollection, DistrustedUser, GeoJsonBBox, Hotspot, POI, VectorGrid } from "../../api/types";
import type { Flash } from "../../hooks/useMapLearning";
import { usePrefersReducedMotion } from "../../hooks/usePrefersReducedMotion";
import { bboxToBounds, gridShape, heatColor, heatOpacity, lineStride, toLatLng } from "./geo";
import { VectorFieldOverlay } from "./VectorFieldLayer";
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
  /** "row|col" → fill colour for the score layer (colour by source); absent cells and null use the heat ramp. */
  tint?: ReadonlyMap<string, string> | null;
  /** Cells to outline once after an agent change (fades over ~3 s). */
  flash?: Flash | null;
  /** Users with lowered trust at their last position (authority view only). */
  distrusted?: DistrustedUser[];
  /** Agent marks on POIs: fill opacity from confidence, one pulse when new, the caption on hover/focus. */
  agentMarks?: boolean;
  newPoiIds?: ReadonlySet<string>;
  /** Server time of the current frame, for "named 3 min ago" (the server clock may be simulated). */
  serverNow?: string | null;
  /** Vector fields as arrows per cell (MapResponse.vectors), with readable names per layer id. */
  vectors?: Record<string, VectorGrid>;
  vectorLabels?: Record<string, string>;
}

/** Cells at or above this value also get a blurred copy underneath (the soft glow). */
const GLOW_FROM = 0.6;
const FADE_MS = 700;
const MAX_GRID_LINES = 64; // per axis; larger grids draw every k-th boundary
const MAX_STAGGER_MS = 650;
const MAX_EVIDENCE = 3;
const EVIDENCE_CHARS = 140;

const clip = (t: string, n: number) => (t.length > n ? `${t.slice(0, n - 1).trimEnd()}…` : t);
const FLASH_MS = 3000;
const FLASH_STATIC_MS = 1400;

/** "just now", "3 min ago", "2 h ago", "4 d ago" between two ISO times (now = the server's frame time). */
export function fmtAgo(iso: string, nowIso?: string | null): string {
  const now = nowIso ? Date.parse(nowIso) : Date.now();
  const s = Math.max(0, (now - Date.parse(iso)) / 1000);
  if (!Number.isFinite(s) || s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}
const pct = (c: number) => `${Math.round(Math.min(Math.max(c, 0), 1) * 100)}%`;

/** Built with textContent only: labels and evidence are agent text and never go through innerHTML. */
function el<K extends keyof HTMLElementTagNameMap>(tag: K, cls: string, text?: string): HTMLElementTagNameMap[K] {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

/** `el` under another name, for effects inside the component (where `el` is the container ref). */
const tag = el;

function poiIcon(p: POI, destination: boolean, marks?: { isNew: boolean; caption: string }): L.DivIcon {
  const root = el("div", `hm-poi${destination ? " hm-poi--dest" : ""}${marks ? " hm-poi--marks" : ""}`);
  if (marks?.isNew) root.appendChild(el("span", "hm-poi-pulse"));
  const dot = el("span", "hm-poi-dot");
  // Agent marks: the fill shows how sure the labeler is (never below a visible minimum).
  if (marks) dot.style.setProperty("--poi-fill", String(Math.min(Math.max(p.confidence, 0.2), 1)));
  root.appendChild(dot);
  const chip = el("span", "hm-poi-chip");
  chip.appendChild(el("span", "hm-poi-label", clip(p.label, 28)));
  if (marks) chip.appendChild(el("span", "hm-poi-more", ` · ${marks.caption}`));
  else chip.appendChild(el("span", "hm-poi-conf", p.confidence.toFixed(2)));
  if (destination) chip.appendChild(el("span", "hm-poi-badge hm-poi-badge--dest", "destination"));
  if (p.visibility === "authority") chip.appendChild(el("span", "hm-poi-badge", "authority only"));
  root.appendChild(chip);
  return L.divIcon({ html: root, className: "hm-poi-icon", iconSize: [0, 0], iconAnchor: [0, 0] });
}

function poiPopup(p: POI, destination: boolean, caption?: string): HTMLElement {
  const root = el("div", "hm-poi-pop");
  const head = el("div", "hm-poi-pop-head");
  head.appendChild(el("strong", "", p.label));
  head.appendChild(el("span", "hm-poi-conf", `confidence ${p.confidence.toFixed(2)}`));
  root.appendChild(head);
  if (caption) root.appendChild(el("div", "hm-poi-pop-meta", caption));
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
  colour?: string;
  removeTimer?: ReturnType<typeof setTimeout>;
}

const ring = (coords: number[][][]): L.LatLngTuple[] =>
  (coords[0] ?? []).map(([lon = 0, lat = 0]) => toLatLng(lat, lon));

function paint(el: Element | undefined, v: number, opacity: number, delayMs = 0, colour?: string) {
  if (!(el instanceof SVGElement)) return;
  el.style.transitionDelay = delayMs ? `${delayMs}ms` : "";
  el.style.fill = colour ?? heatColor(v);
  el.style.fillOpacity = String(opacity);
}

/**
 * Plain Leaflet inside React: one map instance; cells are long-lived paths keyed by layer/row/col and
 * restyled in place each tick, so values ease between frames instead of flashing.
 */
export function Map({
  bbox,
  layers,
  visible,
  hotspots,
  pois,
  destinationLabels,
  cellM,
  height = "min(70vh, 560px)",
  className,
  onPick,
  marker,
  tint,
  flash,
  distrusted,
  agentMarks,
  newPoiIds,
  serverNow,
  vectors,
  vectorLabels,
}: Props) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const renderers = useRef<{ cells: L.Renderer; glow: L.Renderer } | null>(null);
  const cells = useRef(new globalThis.Map<string, CellEntry>());
  const gridGroup = useRef<L.LayerGroup | null>(null);
  const hotspotGroup = useRef<L.LayerGroup | null>(null);
  const poiGroup = useRef<L.LayerGroup | null>(null);
  const markerGroup = useRef<L.LayerGroup | null>(null);
  const flashGroup = useRef<L.LayerGroup | null>(null);
  const distrustGroup = useRef<L.LayerGroup | null>(null);
  const serverNowRef = useRef(serverNow);
  serverNowRef.current = serverNow;
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
    pane("hm-flash", 460);
    pane("hm-distrust", 600).style.pointerEvents = "auto";
    pane("hm-pois", 620).style.pointerEvents = "auto";

    renderers.current = {
      cells: L.svg({ pane: "hm-cells", padding: 0.5 }),
      glow: L.svg({ pane: "hm-glow", padding: 0.5 }),
    };
    gridGroup.current = L.layerGroup().addTo(m);
    hotspotGroup.current = L.layerGroup().addTo(m);
    poiGroup.current = L.layerGroup().addTo(m);
    flashGroup.current = L.layerGroup().addTo(m);
    distrustGroup.current = L.layerGroup().addTo(m);
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
        const colour = id === "score" ? tint?.get(`${row}|${col}`) : undefined;
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
          entry = { poly, glow: null, value, colour };
          cells.current.set(key, entry);
          // Progressive fade-in, sweeping from the SW corner (row 0, col 0) outward.
          fresh.push({ entry, delay: reducedNow ? 0 : Math.round(((row + col) / maxRc) * MAX_STAGGER_MS) });
        } else {
          clearTimeout(entry.removeTimer);
          entry.removeTimer = undefined;
          entry.value = value;
          entry.colour = colour;
          paint(entry.poly.getElement(), value, heatOpacity(value), 0, colour);
        }
        entry.value = value;
        entry.colour = colour;
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
          paint(entry.glow.getElement(), value, 0, 0, colour);
          const g = entry.glow;
          requestAnimationFrame(() => requestAnimationFrame(() => paint(g.getElement(), value, 0.8, 0, colour)));
        } else if (entry.glow) {
          if (value >= GLOW_FROM) paint(entry.glow.getElement(), value, 0.8, 0, colour);
          else {
            entry.glow.remove();
            entry.glow = null;
          }
        }
      }
    }

    if (fresh.length) {
      for (const { entry } of fresh) paint(entry.poly.getElement(), entry.value, 0, 0, entry.colour);
      // two frames so the browser commits opacity 0 before the transition target
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          for (const { entry, delay } of fresh) paint(entry.poly.getElement(), entry.value, heatOpacity(entry.value), delay, entry.colour);
        }),
      );
    }

    for (const [key, entry] of cells.current) {
      if (seen.has(key) || entry.removeTimer) continue;
      paint(entry.poly.getElement(), entry.value, 0, 0, entry.colour);
      if (entry.glow) paint(entry.glow.getElement(), entry.value, 0, 0, entry.colour);
      entry.removeTimer = setTimeout(
        () => {
          entry.poly.remove();
          entry.glow?.remove();
          cells.current.delete(key);
        },
        reducedNow ? 0 : FADE_MS,
      );
    }
  }, [layers, visible, tint]);

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
    const m = map.current;
    const markers: Array<{ marker: L.Marker; at: L.LatLngTuple }> = [];
    for (const p of pois ?? []) {
      if (p.status !== "active") continue;
      const dest = destinationLabels?.has(p.label) ?? false;
      const caption = agentMarks ? `${pct(p.confidence)} · named ${fmtAgo(p.created_at, serverNowRef.current)}` : undefined;
      const marks = caption ? { isNew: newPoiIds?.has(p.id) ?? false, caption } : undefined;
      L.circle(toLatLng(p.lat, p.lon), {
        pane: "hm-pois",
        radius: p.radius_m,
        color: dest ? "#3B6895" : "#35596B",
        weight: 1,
        opacity: 0.5,
        fillOpacity: 0.04,
        interactive: false,
      }).addTo(g);
      const at = toLatLng(p.lat, p.lon);
      const mk = L.marker(at, {
        pane: "hm-pois",
        icon: poiIcon(p, dest, marks),
        keyboard: true,
        title: caption ? `${p.label} · ${caption}` : `${p.label} (${p.confidence.toFixed(2)})`,
        riseOnHover: true,
      })
        .bindPopup(poiPopup(p, dest, caption && `${p.label} · ${caption}`), { className: "hm-popup", maxWidth: 260, autoPanPadding: [16, 16] })
        .addTo(g);
      markers.push({ marker: mk, at });
    }
    if (!m || !agentMarks || !markers.length) return;
    // Keep captions on screen: the chip goes on whichever side of the pin has room, and wraps when
    // neither side does (narrow phones). Re-checked on hover, when the caption grows.
    const fit = () => {
      const w = m.getSize().x;
      for (const { marker: mk, at } of markers) {
        const node = mk.getElement()?.querySelector<HTMLElement>(".hm-poi");
        const chip = node?.querySelector<HTMLElement>(".hm-poi-chip");
        if (!node || !chip) continue;
        const x = m.latLngToContainerPoint(at).x;
        const right = w - x - 16;
        const left = x - 16;
        chip.classList.remove("is-tight");
        chip.style.maxWidth = "";
        const need = chip.offsetWidth;
        const flipped = need > right && left > right;
        node.classList.toggle("hm-poi--flip", flipped);
        if (need > Math.max(right, left)) {
          chip.classList.add("is-tight");
          chip.style.maxWidth = `${Math.max(Math.max(right, left), 120)}px`;
        }
      }
    };
    fit();
    const onHover = () => requestAnimationFrame(fit);
    for (const { marker: mk } of markers) mk.on("mouseover mouseout", onHover);
    m.on("moveend zoomend resize", fit);
    return () => {
      m.off("moveend zoomend resize", fit);
      for (const { marker: mk } of markers) mk.off("mouseover mouseout", onHover);
    };
  }, [pois, destinationLabels, agentMarks, newPoiIds]);

  // Change flash: outline the cells an agent change moved, then fade (a brief static outline with reduced motion).
  useEffect(() => {
    const g = flashGroup.current;
    if (!g) return;
    g.clearLayers();
    if (!flash || !flash.cells.length) return;
    const still = reducedRef.current;
    for (const c of flash.cells) {
      const latlngs = ring(c.ring);
      const cls = `hm-flash hm-flash--${c.tone}${still ? " hm-flash--still" : ""}`;
      const halo = L.polygon(latlngs, { pane: "hm-flash", className: `${cls} hm-flash-halo`, weight: 7, fill: false, interactive: false }).addTo(g);
      const line = L.polygon(latlngs, { pane: "hm-flash", className: cls, weight: 2, fill: false, interactive: false }).addTo(g);
      if (c.tone === "shift" && c.hue)
        for (const poly of [halo, line]) {
          const node = poly.getElement();
          if (node instanceof SVGElement) node.style.stroke = c.hue;
        }
    }
    const t = setTimeout(() => g.clearLayers(), still ? FLASH_STATIC_MS : FLASH_MS + 100);
    return () => clearTimeout(t);
  }, [flash]);

  // Users whose trust an agent lowered: grey dots at their last position, a dashed ring around each group.
  useEffect(() => {
    const g = distrustGroup.current;
    if (!g) return;
    g.clearLayers();
    const pts = (distrusted ?? []).filter((u) => u.last_lat !== null && u.last_lon !== null);
    if (!pts.length) return;
    const near = Math.max(2 * (cellM ?? 10), 20); // metres: one ring for users this close together
    const mPerDegLat = 111_195;
    const dist = (aLat: number, aLon: number, bLat: number, bLon: number) => {
      const dy = (aLat - bLat) * mPerDegLat;
      const dx = (aLon - bLon) * mPerDegLat * Math.cos((aLat * Math.PI) / 180);
      return Math.hypot(dx, dy);
    };
    const groups: Array<{ lat: number; lon: number; users: DistrustedUser[] }> = [];
    for (const u of pts) {
      const grp = groups.find((x) => dist(x.lat, x.lon, u.last_lat!, u.last_lon!) <= near);
      if (grp) grp.users.push(u);
      else groups.push({ lat: u.last_lat!, lon: u.last_lon!, users: [u] });
    }
    for (const grp of groups) {
      const n = grp.users.length;
      const lat = grp.users.reduce((s, u) => s + u.last_lat!, 0) / n;
      const lon = grp.users.reduce((s, u) => s + u.last_lon!, 0) / n;
      const spread = Math.max(...grp.users.map((u) => dist(lat, lon, u.last_lat!, u.last_lon!)));
      // agent text (the reason) goes in with textContent only
      const tip = tag("div", "hm-distrust-tip");
      tip.appendChild(tag("strong", "", n === 1 ? "1 user with lowered trust" : `${n} users with lowered trust`));
      const lowest = Math.min(...grp.users.map((u) => u.trust));
      tip.appendChild(tag("div", "hm-poi-pop-meta", `trust ${lowest.toFixed(2)}${n > 1 ? " (lowest)" : ""}`));
      const reason = grp.users.find((u) => u.reason)?.reason;
      if (reason) tip.appendChild(tag("div", "hm-distrust-why", clip(reason, 120)));
      L.circle(toLatLng(lat, lon), {
        pane: "hm-distrust",
        className: "hm-distrust-ring",
        radius: spread + Math.max((cellM ?? 10) * 0.9, 12),
        weight: 1.5,
        dashArray: "4 3.5",
        fillOpacity: 0.1,
      })
        .bindTooltip(tip, { className: "hm-tooltip", direction: "top" })
        .addTo(g);
      for (const u of grp.users)
        L.circleMarker(toLatLng(u.last_lat!, u.last_lon!), {
          pane: "hm-distrust",
          className: "hm-distrust-dot",
          radius: 4.5,
          weight: 1.4,
          fillOpacity: 0.9,
          interactive: false,
        }).addTo(g);
    }
  }, [distrusted, cellM]);

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
    <div className={`${onPick ? "hm-map--picking" : ""} ${className ?? ""}`} style={{ height, minHeight: 280, width: "100%", position: "relative" }}>
      <div ref={el} className="hm-map" style={{ height: "100%", width: "100%", borderRadius: 12 }} />
      <VectorFieldOverlay mapRef={map} vectors={vectors} bbox={bbox} cellM={cellM} labels={vectorLabels} pane="hm-vectors" zIndex={455} />
    </div>
  );
}
