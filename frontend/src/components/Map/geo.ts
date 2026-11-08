// The only place the frontend converts between GeoJSON [lon, lat] and Leaflet [lat, lon].
import L from "leaflet";
import type { GeoJsonBBox } from "../../api/outputs";

export function bboxToBounds([minLon, minLat, maxLon, maxLat]: GeoJsonBBox): L.LatLngBounds {
  return L.latLngBounds([minLat, minLon], [maxLat, maxLon]);
}

export function toLatLng(lat: number, lon: number): L.LatLngTuple {
  return [lat, lon];
}

/** Score in [0, 1] (clamped) -> heat colour, transparent yellow to opaque dark red. */
export function heatStyle(v: number): L.PathOptions {
  const t = Math.min(Math.max(v, 0), 1);
  const hue = 55 - 55 * t; // 55 = yellow, 0 = red
  const light = 60 - 25 * t;
  return { stroke: false, fillColor: `hsl(${hue} 100% ${light}%)`, fillOpacity: 0.15 + 0.6 * t };
}
