import L from "leaflet";
import { useEffect, useRef } from "react";
import { cellValue, hotspotRadiusM, type CellCollection, type GeoJsonBBox, type Hotspot } from "../../api/outputs";
import { bboxToBounds, heatStyle, toLatLng } from "./geo";

interface Props {
  bbox: GeoJsonBBox | null;
  layers: Record<string, CellCollection>;
  visible: string[];
  hotspots: Hotspot[];
}

/** Plain Leaflet inside React: one map instance, overlays redrawn when data changes. */
export function Map({ bbox, layers, visible, hotspots }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const overlay = useRef<L.LayerGroup | null>(null);
  const fitted = useRef(false);

  useEffect(() => {
    if (!el.current) return;
    const m = L.map(el.current, { zoomControl: true });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 20,
      maxNativeZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(m);
    m.setView([0, 0], 2);
    overlay.current = L.layerGroup().addTo(m);
    map.current = m;
    return () => {
      m.remove();
      map.current = null;
      fitted.current = false;
    };
  }, []);

  useEffect(() => {
    if (map.current && bbox && !fitted.current) {
      map.current.fitBounds(bboxToBounds(bbox));
      fitted.current = true;
    }
  }, [bbox]);

  useEffect(() => {
    const group = overlay.current;
    if (!group) return;
    group.clearLayers();
    for (const id of visible) {
      const fc = layers[id];
      if (!fc) continue;
      L.geoJSON(fc as GeoJSON.FeatureCollection, {
        style: (f) => heatStyle(f ? cellValue(f as never) : 0),
        interactive: false,
      }).addTo(group);
    }
    for (const h of hotspots) {
      L.circle(toLatLng(h.lat, h.lon), { radius: hotspotRadiusM(h), color: "#7a00cc", weight: 2, fill: false })
        .bindTooltip(`${h.id}: peak ${h.peak.toFixed(2)}`)
        .addTo(group);
    }
  }, [layers, visible, hotspots]);

  return <div ref={el} style={{ height: "70vh", minHeight: 320, width: "100%", borderRadius: 4 }} />;
}
