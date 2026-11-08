// The only place the frontend converts between GeoJSON [lon, lat] and Leaflet [lat, lon].
import L from "leaflet";
import type { GeoJsonBBox } from "../../api/types";

export function bboxToBounds([minLon, minLat, maxLon, maxLat]: GeoJsonBBox): L.LatLngBounds {
  return L.latLngBounds([minLat, minLon], [maxLat, maxLon]);
}

export function toLatLng(lat: number, lon: number): L.LatLngTuple {
  return [lat, lon];
}

// --- Grid geometry (mirrors backend engine/grid.py: equirectangular, origin at the SW corner) ---

const EARTH_RADIUS_M = 6_371_008.8;
const DEG = Math.PI / 180;

export interface GridShape {
  rows: number;
  cols: number;
  widthM: number;
  heightM: number;
  /** meters per degree of latitude / longitude at the bbox's mid-latitude */
  mPerDegLat: number;
  mPerDegLon: number;
}

/** Rows x cols the engine will use for this bbox and cell size (row 0 is the southern edge). */
export function gridShape([minLon, minLat, maxLon, maxLat]: GeoJsonBBox, cellM: number): GridShape {
  const lat0 = 0.5 * (minLat + maxLat);
  const mPerDegLat = EARTH_RADIUS_M * DEG;
  const mPerDegLon = EARTH_RADIUS_M * DEG * Math.cos(lat0 * DEG);
  const widthM = (maxLon - minLon) * mPerDegLon;
  const heightM = (maxLat - minLat) * mPerDegLat;
  const safe = cellM > 0 && Number.isFinite(cellM) ? cellM : 1;
  return {
    rows: Math.max(1, Math.ceil(heightM / safe - 1e-9)),
    cols: Math.max(1, Math.ceil(widthM / safe - 1e-9)),
    widthM,
    heightM,
    mPerDegLat,
    mPerDegLon,
  };
}

/** Stride so that at most `max` lines are drawn per axis (1 = every cell boundary). */
export function lineStride(n: number, max: number): number {
  return Math.max(1, Math.ceil(n / max));
}

/** Compact coordinate label, e.g. "32.77000, -96.77000". */
export function fmtLatLon(lat: number, lon: number): string {
  return `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
}

// --- Heat ramp (DESIGN.md palette): soft blue/cyan for low values, warm faded coral for hot ones ---

type Rgb = [number, number, number];
const STOPS: Array<[number, Rgb]> = [
  [0.0, [169, 210, 213]], // soft aqua #A9D2D5
  [0.3, [125, 192, 204]], // soft cyan
  [0.55, [176, 203, 196]], // sea glass, warming
  [0.7, [231, 207, 180]], // cream sand
  [0.85, [217, 147, 126]], // coral #D9937E
  [1.0, [232, 135, 95]], // warm coral #E8875F
];

const clamp01 = (v: number) => (Number.isFinite(v) ? Math.min(Math.max(v, 0), 1) : 0);

export function heatColor(v: number): string {
  const t = clamp01(v);
  let prev = STOPS[0] as [number, Rgb];
  for (const stop of STOPS) {
    const [t1, c1] = stop;
    if (t <= t1) {
      const [t0, c0] = prev;
      const f = t1 > t0 ? (t - t0) / (t1 - t0) : 0;
      const c = c0.map((x, k) => Math.round(x + ((c1[k] ?? x) - x) * f));
      return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
    }
    prev = stop;
  }
  return "rgb(232, 135, 95)";
}

/** Cells stay translucent so the washed-out tiles read through the low end. */
export function heatOpacity(v: number): number {
  return 0.2 + 0.48 * clamp01(v);
}

/** CSS gradient for a legend bar, from the same stops. */
export const HEAT_GRADIENT_CSS = `linear-gradient(90deg, ${STOPS.map(
  ([t, c]) => `rgba(${c[0]}, ${c[1]}, ${c[2]}, ${heatOpacity(t).toFixed(2)}) ${Math.round(t * 100)}%`,
).join(", ")})`;

/** Leaflet path style for a value in [0, 1] (kept for existing callers). */
export function heatStyle(v: number): L.PathOptions {
  return { stroke: false, fillColor: heatColor(v), fillOpacity: heatOpacity(v) };
}
