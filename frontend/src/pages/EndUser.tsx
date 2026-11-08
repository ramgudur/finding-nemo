// End-user page: public instructions only. Must work on a phone over a tunnel.
// Sends only this device's own position; shows the guarded instruction and route; recomputes on each
// public stream tick. No key, no layers beyond what a public caller gets.
import L from "leaflet";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import { openStream } from "../api/stream";
import type { GeoJsonBBox, Instruction } from "../api/types";
import { bboxToBounds, toLatLng } from "../components/Map/geo";

type Pos = { lat: number; lon: number };
type Source = "gps" | "map" | null;

const MIN_RECOMPUTE_MS = 2000; // the engine ticks every 2 s

export function EndUser({ appId }: { appId: string }) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const overlay = useRef<L.LayerGroup | null>(null);
  const lastAsk = useRef(0);
  const [bbox, setBbox] = useState<GeoJsonBBox | null>(null);
  const [pos, setPos] = useState<Pos | null>(null);
  const [source, setSource] = useState<Source>(null);
  const [ins, setIns] = useState<Instruction | null>(null);
  const [status, setStatus] = useState("Finding your position…");
  const [error, setError] = useState("");

  // area outline from the public map (bbox is always public)
  useEffect(() => {
    api
      .map(appId)
      .then((m) => setBbox(m.bbox))
      .catch((e) => setError(e instanceof ApiError && e.status === 404 ? "This alert is not active." : String(e)));
  }, [appId]);

  // phone GPS; tapping the map sets the position too (desktop, or GPS denied)
  useEffect(() => {
    if (!("geolocation" in navigator)) {
      setStatus("Tap the map where you are.");
      return;
    }
    const id = navigator.geolocation.watchPosition(
      (p) => {
        setPos({ lat: p.coords.latitude, lon: p.coords.longitude });
        setSource("gps");
      },
      () => setStatus("Location is off. Tap the map where you are."),
      { enableHighAccuracy: true, maximumAge: 5000, timeout: 15000 },
    );
    return () => navigator.geolocation.clearWatch(id);
  }, []);

  const ask = useCallback(
    async (p: Pos, force = false) => {
      const now = Date.now();
      if (!force && now - lastAsk.current < MIN_RECOMPUTE_MS) return;
      lastAsk.current = now;
      try {
        setIns(await api.instruction(appId, p));
        setError("");
      } catch (e) {
        if (e instanceof ApiError && e.status === 503) setStatus("Waiting for the first map…");
        else if (e instanceof ApiError && e.status === 404) setError("This alert is not active.");
        else setError("Can't reach the service. Retrying…");
      }
    },
    [appId],
  );

  useEffect(() => {
    if (pos) void ask(pos, true);
  }, [pos, ask]);

  // recompute on every public tick (the hazard moves); keep a slow poll in case the stream is down
  const posRef = useRef<Pos | null>(null);
  posRef.current = pos;
  useEffect(() => {
    const close = openStream(appId, "", {
      onState: () => undefined,
      onFrame: (f) => {
        if (f.type === "tick" && posRef.current) void ask(posRef.current);
      },
    });
    const id = setInterval(() => posRef.current && void ask(posRef.current), 5000);
    return () => {
      close();
      clearInterval(id);
    };
  }, [appId, ask]);

  // map: area, you, route, destination
  useEffect(() => {
    if (!el.current || map.current) return;
    const m = L.map(el.current, { zoomControl: true, attributionControl: true });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 20,
      maxNativeZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(m);
    m.setView([0, 0], 2);
    m.on("click", (e: L.LeafletMouseEvent) => {
      setPos({ lat: e.latlng.lat, lon: e.latlng.lng });
      setSource("map");
    });
    overlay.current = L.layerGroup().addTo(m);
    map.current = m;
    return () => {
      m.remove();
      map.current = null;
    };
  }, []);

  useEffect(() => {
    if (map.current && bbox) map.current.fitBounds(bboxToBounds(bbox));
  }, [bbox]);

  useEffect(() => {
    const g = overlay.current;
    if (!g) return;
    g.clearLayers();
    if (ins && ins.route.length > 1) {
      L.polyline(ins.route.map(([la, lo]) => toLatLng(la, lo)), { color: "#0b6bcb", weight: 6, opacity: 0.9 }).addTo(g);
    }
    if (ins?.destination) {
      L.circleMarker(toLatLng(ins.destination.lat, ins.destination.lon), {
        radius: 10,
        color: "#0a7a3d",
        fillColor: "#19a35b",
        fillOpacity: 1,
        weight: 3,
      })
        .bindTooltip(ins.destination.label, { permanent: true, direction: "top" })
        .addTo(g);
    }
    if (pos) {
      L.circleMarker(toLatLng(pos.lat, pos.lon), { radius: 8, color: "#fff", fillColor: "#0b6bcb", fillOpacity: 1, weight: 3 })
        .bindTooltip("You", { direction: "bottom" })
        .addTo(g);
    }
  }, [ins, pos]);

  const fallback = ins !== null && ins.destination === null;
  return (
    <div style={{ display: "grid", gap: 12, maxWidth: 640, margin: "0 auto" }}>
      <section
        aria-live="polite"
        style={{
          padding: "14px 16px",
          borderRadius: 8,
          background: fallback ? "#fff4e5" : "#eaf3ff",
          border: `2px solid ${fallback ? "#c77700" : "#0b6bcb"}`,
          fontSize: 20,
          lineHeight: 1.35,
        }}
      >
        {ins ? ins.text : status}
      </section>
      {error && <p style={{ color: "#b00020", margin: 0, fontSize: 16 }}>{error}</p>}
      <div ref={el} style={{ height: "min(60vh, 480px)", minHeight: 280, width: "100%", borderRadius: 8 }} />
      <p style={{ color: "#555", margin: 0, fontSize: 14 }}>
        {source === "gps" ? "Using your phone's location." : source === "map" ? "Using the spot you tapped." : ""} Tap the map
        to set your position. Only your own position is sent; it isn't stored with your name.
      </p>
    </div>
  );
}
