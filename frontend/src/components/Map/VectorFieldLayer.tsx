// Vector fields as arrows per cell. The server sends, per layer, the (u east, v north) its operator
// computed at sampled cell centres (MapResponse.vectors), with that layer's visibility (rule 8), and
// with the direction convention already resolved, so every arrow points where the field heads.
// Drawn on one canvas in its own pane (never a marker per cell), redrawn on zoom/move end and resize,
// not on every pan frame. On screen, arrows are thinned so they stay at least MIN_GAP_PX apart.
// Labels come from the spec (the console passes them) or from the layer id; nothing here names a
// domain (rule 3).
import L from "leaflet";
import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import type { GeoJsonBBox, VectorGrid } from "../../api/types";
import { gridShape } from "./geo";

const MIN_GAP_PX = 28;
const MAX_LEN_PX = 30;
const PAD = 0.25; // canvas covers the view plus a quarter on each side, so a short pan shows arrows
const FALLBACK_RGB = "62 124 126"; // --c-muted-teal (working pages)

export interface VectorFieldOverlayProps {
  /** The Leaflet map; read in effects, so a ref filled by the parent's mount effect is fine. */
  mapRef: RefObject<L.Map | null>;
  vectors: Record<string, VectorGrid> | undefined;
  bbox: GeoJsonBBox | null;
  cellM: number | null | undefined;
  /** Readable names per layer id (from the spec); else a name from the layer id. */
  labels?: Record<string, string>;
  /** Pane name and z-index for the canvas (between the cells and the pins). */
  pane: string;
  zIndex: number;
  /** Where the legend sits inside the map's (relative) wrapper. */
  legendClassName?: string;
  variant?: "console" | "public";
}

export interface VectorSummary {
  layerId: string;
  label: string;
  /** Mean magnitude over the sampled cells. */
  speed: number;
  /** Where the mean vector heads / comes from, degrees clockwise from north. */
  towardDeg: number;
  fromDeg: number;
  /** True when magnitude or direction differs across the area. */
  varies: boolean;
}

const norm360 = (d: number) => ((d % 360) + 360) % 360;
const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
const compass = (deg: number) => COMPASS[Math.round(norm360(deg) / 22.5) % 16]!;
const fmtSpeed = (s: number) => `${s < 10 ? s.toFixed(1) : Math.round(s)} m/s`;

/** "wind_obs.field" → "wind obs": the input id part of a layer id, readable. */
export function labelFromLayerId(layerId: string): string {
  return (layerId.split(".")[0] ?? layerId).replace(/[_-]+/g, " ").trim() || layerId;
}

export function summarize(layerId: string, grid: VectorGrid, label: string): VectorSummary | null {
  const cells = grid.cells;
  if (!cells.length) return null;
  let su = 0;
  let sv = 0;
  let sm = 0;
  let lo = Infinity;
  let hi = 0;
  for (const c of cells) {
    const m = Math.hypot(c.u, c.v);
    su += c.u;
    sv += c.v;
    sm += m;
    lo = Math.min(lo, m);
    hi = Math.max(hi, m);
  }
  const n = cells.length;
  const towardDeg = norm360((Math.atan2(su / n, sv / n) * 180) / Math.PI);
  // the mean vector is shorter than the mean magnitude when directions disagree
  const coherence = sm > 0 ? Math.hypot(su, sv) / sm : 1;
  return { layerId, label, speed: sm / n, towardDeg, fromDeg: norm360(towardDeg + 180), varies: hi - lo > 0.1 * hi || coherence < 0.98 };
}

function teal(map: L.Map): string {
  const v = getComputedStyle(map.getContainer()).getPropertyValue("--c-muted-teal").trim();
  return `rgb(${v || FALLBACK_RGB})`;
}

function draw(
  map: L.Map,
  canvas: HTMLCanvasElement,
  vectors: Record<string, VectorGrid>,
  bbox: GeoJsonBBox,
  cellM: number,
) {
  const size = map.getSize();
  const w = Math.round(size.x * (1 + 2 * PAD));
  const h = Math.round(size.y * (1 + 2 * PAD));
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
  }
  const origin = map.containerPointToLayerPoint([-size.x * PAD, -size.y * PAD]);
  L.DomUtil.setPosition(canvas, origin);
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const s = gridShape(bbox, cellM);
  const dLat = cellM / s.mPerDegLat;
  const dLon = cellM / s.mPerDegLon;
  const at = (row: number, col: number) =>
    map.latLngToLayerPoint([bbox[1] + (row + 0.5) * dLat, bbox[0] + (col + 0.5) * dLon]).subtract(origin);
  const colour = teal(map);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";

  for (const grid of Object.values(vectors)) {
    if (!grid.cells.length) continue;
    // on-screen spacing of the sampled cells, then thin so arrows stay MIN_GAP_PX apart
    const a = at(0, 0);
    const gapX = Math.abs(at(0, grid.step).x - a.x);
    const gapY = Math.abs(at(grid.step, 0).y - a.y);
    const gap = Math.max(Math.min(gapX, gapY), 0.01);
    const k = Math.max(1, Math.ceil(MIN_GAP_PX / gap));
    const maxLen = Math.min(gap * k * 0.72, MAX_LEN_PX);
    let r0 = Infinity;
    let c0 = Infinity;
    let maxMag = 0;
    for (const c of grid.cells) {
      r0 = Math.min(r0, c.row);
      c0 = Math.min(c0, c.col);
      maxMag = Math.max(maxMag, Math.hypot(c.u, c.v));
    }
    if (maxMag <= 0) continue;
    for (const c of grid.cells) {
      if (Math.round((c.row - r0) / grid.step) % k || Math.round((c.col - c0) / grid.step) % k) continue;
      const mag = Math.hypot(c.u, c.v);
      if (mag <= 0) continue;
      const p = at(c.row, c.col);
      if (p.x < -MAX_LEN_PX || p.y < -MAX_LEN_PX || p.x > w + MAX_LEN_PX || p.y > h + MAX_LEN_PX) continue;
      const f = mag / maxMag;
      const len = Math.max(8, maxLen * (0.4 + 0.6 * f));
      const ux = c.u / mag;
      const uy = -c.v / mag; // screen y grows southward
      const x0 = p.x - (ux * len) / 2;
      const y0 = p.y - (uy * len) / 2;
      const x1 = p.x + (ux * len) / 2;
      const y1 = p.y + (uy * len) / 2;
      const head = Math.min(6, len * 0.4);
      const path = () => {
        ctx.beginPath();
        ctx.moveTo(x0, y0);
        ctx.lineTo(x1, y1);
        ctx.moveTo(x1 - head * (ux * 0.87 - uy * 0.5), y1 - head * (uy * 0.87 + ux * 0.5));
        ctx.lineTo(x1, y1);
        ctx.lineTo(x1 - head * (ux * 0.87 + uy * 0.5), y1 - head * (uy * 0.87 - ux * 0.5));
      };
      // a pale halo keeps the arrow legible over tiles and hot cells alike
      ctx.globalAlpha = 0.55 * (0.5 + 0.5 * f);
      ctx.strokeStyle = "rgb(248 244 234)";
      ctx.lineWidth = 3.4;
      path();
      ctx.stroke();
      ctx.globalAlpha = 0.5 + 0.5 * f;
      ctx.strokeStyle = colour;
      ctx.lineWidth = 1.5;
      path();
      ctx.stroke();
    }
  }
  ctx.globalAlpha = 1;
}

function LegendArrow({ deg }: { deg: number }) {
  return (
    <svg viewBox="0 0 16 16" aria-hidden className="h-4 w-4 shrink-0 text-mutedTeal" style={{ transform: `rotate(${deg}deg)` }}>
      <path d="M8 14 V3 M4.5 6.5 L8 3 L11.5 6.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** The canvas of arrows plus its legend and show/hide toggle. Renders nothing without vectors. */
export function VectorFieldOverlay({
  mapRef,
  vectors,
  bbox,
  cellM,
  labels,
  pane,
  zIndex,
  legendClassName = "right-2 top-10 items-end",
  variant = "console",
}: VectorFieldOverlayProps) {
  const [shown, setShown] = useState(true);
  const [retry, setRetry] = useState(0);
  const canvasRef = useRef<{ map: L.Map; canvas: HTMLCanvasElement } | null>(null);
  const has = !!vectors && Object.values(vectors).some((g) => g.cells.length > 0);
  const data = useRef({ vectors, bbox, cellM, shown });
  data.current = { vectors, bbox, cellM, shown };

  useEffect(() => {
    const m = mapRef.current;
    if (!has) return;
    if (!m) {
      // the parent creates the map in its own mount effect; try again next frame
      const id = requestAnimationFrame(() => setRetry((r) => r + 1));
      return () => cancelAnimationFrame(id);
    }
    let cur = canvasRef.current;
    if (!cur || cur.map !== m) {
      const p = m.getPane(pane) ?? m.createPane(pane);
      p.style.zIndex = String(zIndex);
      p.style.pointerEvents = "none";
      const canvas = L.DomUtil.create("canvas", "leaflet-zoom-hide", p);
      canvas.setAttribute("aria-hidden", "true");
      cur = { map: m, canvas };
      canvasRef.current = cur;
    }
    const { canvas } = cur;
    const redraw = () => {
      const d = data.current;
      canvas.style.display = d.shown ? "" : "none";
      if (d.shown && d.vectors && d.bbox && d.cellM) draw(m, canvas, d.vectors, d.bbox, d.cellM);
    };
    redraw();
    m.on("moveend zoomend resize viewreset", redraw);
    return () => {
      m.off("moveend zoomend resize viewreset", redraw);
    };
  }, [mapRef, has, vectors, bbox, cellM, shown, pane, zIndex, retry]);

  // Drop the canvas when vectors go away or on unmount (the map may already be gone).
  useEffect(() => {
    if (has) return;
    canvasRef.current?.canvas.remove();
    canvasRef.current = null;
  }, [has]);
  useEffect(
    () => () => {
      canvasRef.current?.canvas.remove();
      canvasRef.current = null;
    },
    [],
  );

  const summaries = useMemo(
    () =>
      Object.entries(vectors ?? {}).flatMap(([id, g]) => summarize(id, g, labels?.[id] ?? labelFromLayerId(id)) ?? []),
    [vectors, labels],
  );
  if (!has || !summaries.length) return null;
  const big = variant === "public";

  return (
    <div
      className={`absolute z-[700] flex max-w-[calc(100%-1rem)] flex-col gap-1 ${legendClassName}`}
      aria-label="Vector field legend"
      role="group"
    >
      {shown &&
        summaries.map((s) => (
          <p
            key={s.layerId}
            title={`${s.label}: arrows point where the field heads; longer and darker means stronger${s.varies ? " (average over the area shown)" : ""}`}
            className={`m-0 inline-flex max-w-full items-center gap-1.5 truncate rounded-lg bg-cream/95 px-2 py-1 text-slate shadow-[var(--shadow-1)] ${big ? "text-[15px] font-semibold" : "font-mono text-[11.5px]"}`}
          >
            <LegendArrow deg={s.towardDeg} />
            <span className="truncate">
              {s.label}, {s.varies ? "avg " : ""}
              {fmtSpeed(s.speed)} from {compass(s.fromDeg)}
            </span>
          </p>
        ))}
      <button
        type="button"
        aria-pressed={shown}
        onClick={() => setShown((v) => !v)}
        className={`tap wp-press rounded-full bg-cream/95 px-2.5 py-1 font-bold text-slate shadow-[var(--shadow-1)] hover:bg-cream ${big ? "min-h-[44px] px-3.5 text-[15px]" : "text-[11.5px]"}`}
      >
        {shown ? "Hide arrows" : "Show arrows"}
      </button>
    </div>
  );
}
