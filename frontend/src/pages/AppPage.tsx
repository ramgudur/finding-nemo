// Dev console: live score map, hotspots, layer toggles. With the app's authority key it shows the
// authority view; without one it shows exactly what a public caller gets.
import { useCallback, useEffect, useRef, useState } from "react";
import { forgetKey, loadKey, saveKey } from "../api/authorityKey";
import { ApiError, api } from "../api/client";
import { openStream, type StreamState } from "../api/stream";
import type { CellCollection, GeoJsonBBox, Hotspot, HotspotsResponse, MapResponse, StreamFrame } from "../api/types";
import { DemoTools } from "../components/DemoTools/DemoTools";
import { LayerToggles } from "../components/LayerToggles/LayerToggles";
import { Map } from "../components/Map/Map";

const POLL_MS = 2000; // engine tick; used only while the stream is down

const isAuthError = (e: unknown) => e instanceof ApiError && (e.status === 401 || e.status === 403);

export function AppPage({ appId }: { appId: string }) {
  const [initial] = useState(() => loadKey(appId));
  const [key, setKey] = useState(initial.key);
  const [draftKey, setDraftKey] = useState(initial.key);
  const [remember, setRemember] = useState(initial.remembered);
  const [keyRejected, setKeyRejected] = useState(false);
  const [stream, setStream] = useState<StreamState>("connecting");
  const [bbox, setBbox] = useState<GeoJsonBBox | null>(null);
  const [layers, setLayers] = useState<Record<string, CellCollection>>({});
  const [hotspots, setHotspots] = useState<Hotspot[]>([]);
  const [visible, setVisible] = useState<string[] | null>(null);
  const [updated, setUpdated] = useState("");
  const [error, setError] = useState("");
  const alive = useRef(true);

  const activeKey = keyRejected ? "" : key;

  const apply = useCallback((m: MapResponse, h: HotspotsResponse) => {
    if (!alive.current) return;
    setBbox(m.bbox);
    setLayers(m.layers);
    setHotspots(h.hotspots);
    // first data picks a default; after that the dev's toggles stand (the map skips absent layers)
    setVisible((v) => {
      if (v !== null) return v;
      const ids = Object.keys(m.layers);
      if (ids.length === 0) return null;
      return ids.includes("score") ? ["score"] : ids.slice(0, 1);
    });
    setUpdated(new Date(m.t).toLocaleTimeString());
    setError("");
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [m, h] = await Promise.all([api.map(appId, activeKey), api.hotspots(appId, activeKey)]);
      apply(m, h);
    } catch (e) {
      if (!alive.current) return;
      if (activeKey && isAuthError(e)) setKeyRejected(true);
      else if (e instanceof ApiError && e.status === 404) setError(`App "${appId}" not found or not active.`);
      else setError(String(e));
    }
  }, [appId, activeKey, apply]);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  // Live stream: each tick frame carries the filtered map and hotspots.
  useEffect(() => {
    void refresh();
    return openStream(appId, activeKey, {
      onState: setStream,
      onFrame: (f: StreamFrame) => {
        if (f.type === "auth_ok") {
          if (activeKey && !f.authority) setKeyRejected(true);
        } else if (f.type === "tick") apply(f.map, f.hotspots);
      },
    });
  }, [appId, activeKey, refresh, apply]);

  // Fallback polling while the stream is not open.
  useEffect(() => {
    if (stream === "open") return;
    const id = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(id);
  }, [stream, refresh]);

  const submitKey = (e: React.FormEvent) => {
    e.preventDefault();
    const k = draftKey.trim();
    saveKey(appId, k, remember);
    setKeyRejected(false);
    setVisible(null);
    setKey(k);
  };

  const dropKey = () => {
    forgetKey(appId);
    setDraftKey("");
    setKeyRejected(false);
    setKey("");
  };

  const authority = Boolean(activeKey);
  const muted = { color: "#666" };

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "baseline" }}>
        <h1 style={{ margin: 0, fontSize: 22 }}>Console: {appId}</h1>
        <span
          style={{
            padding: "2px 8px",
            borderRadius: 10,
            fontSize: 13,
            background: authority ? "#e6f0ff" : "#f1f1f1",
            color: authority ? "#0b4aa2" : "#444",
          }}
        >
          {authority ? "authority view" : "public view"}
        </span>
        <span style={muted}>
          {stream === "open" ? "live" : "polling"}
          {updated && ` · updated ${updated}`}
        </span>
      </div>

      <form onSubmit={submitKey} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <input
          type="password"
          placeholder="Authority key (shown once at activation)"
          value={draftKey}
          onChange={(e) => setDraftKey(e.target.value)}
          style={{ flex: "1 1 260px", padding: 6 }}
          autoComplete="off"
          aria-label="Authority key"
        />
        <label style={{ ...muted, fontSize: 14 }}>
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} /> remember on this
          device
        </label>
        <button type="submit" disabled={!draftKey.trim()}>
          Use key
        </button>
        {key && (
          <button type="button" onClick={dropKey}>
            Forget key
          </button>
        )}
      </form>

      {keyRejected && <p style={{ color: "#b00020", margin: 0 }}>Authority key rejected. Showing the public view.</p>}
      {!authority && !keyRejected && (
        <p style={{ ...muted, margin: 0 }}>
          This is what a public caller sees: public layers only. Enter the authority key for raw layers and hotspots.
        </p>
      )}
      {error && <p style={{ color: "#b00020", margin: 0 }}>{error}</p>}

      <Map bbox={bbox} layers={layers} visible={visible ?? []} hotspots={hotspots} />
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
        <LayerToggles layerIds={Object.keys(layers)} visible={visible ?? []} onChange={setVisible} />
        <div>
          <strong>Hotspots</strong>
          {hotspots.length === 0 ? (
            <p style={{ ...muted, margin: "4px 0" }}>None visible.</p>
          ) : (
            <ul style={{ margin: "4px 0", paddingLeft: 18 }}>
              {hotspots.map((h) => (
                <li key={h.id}>
                  {h.id}: score {h.score.toFixed(2)}, {Math.round(h.radius_m)} m, {h.persisted_ticks} ticks
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
      {authority && <DemoTools appId={appId} authorityKey={activeKey} />}
    </div>
  );
}
