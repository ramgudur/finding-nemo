// Dev console (authority view): live score map, hotspots, layer toggles.
import { useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import type { CellCollection, GeoJsonBBox, Hotspot } from "../api/outputs";
import { LayerToggles } from "../components/LayerToggles/LayerToggles";
import { Map } from "../components/Map/Map";

const POLL_MS = 2000; // engine tick; replaced by the WS stream later
const keyStore = (appId: string) => `authority-key:${appId}`;

function loadKey(appId: string): string {
  try {
    return sessionStorage.getItem(keyStore(appId)) ?? "";
  } catch {
    return "";
  }
}

export function AppPage({ appId }: { appId: string }) {
  const [key, setKey] = useState(() => loadKey(appId));
  const [draftKey, setDraftKey] = useState(key);
  const [bbox, setBbox] = useState<GeoJsonBBox | null>(null);
  const [layers, setLayers] = useState<Record<string, CellCollection>>({});
  const [hotspots, setHotspots] = useState<Hotspot[]>([]);
  const [visible, setVisible] = useState<string[] | null>(null);
  const [updated, setUpdated] = useState<string>("");
  const [error, setError] = useState<string>("");

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const [m, h] = await Promise.all([api.map(appId, key), api.hotspots(appId, key)]);
        if (!alive) return;
        setBbox(m.bbox);
        setLayers(m.layers);
        setHotspots(h.hotspots);
        setVisible((v) => v ?? ("score" in m.layers ? ["score"] : Object.keys(m.layers).slice(0, 1)));
        setUpdated(new Date(m.t).toLocaleTimeString());
        setError("");
      } catch (e) {
        if (!alive) return;
        setError(e instanceof ApiError && e.status === 404 ? `App "${appId}" not found or not active.` : String(e));
      }
    };
    void poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [appId, key]);

  const saveKey = () => {
    setKey(draftKey.trim());
    try {
      sessionStorage.setItem(keyStore(appId), draftKey.trim());
    } catch {
      // storage unavailable: key lives for this page only
    }
  };

  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "baseline" }}>
        <h1 style={{ margin: 0, fontSize: 22 }}>Console: {appId}</h1>
        <span style={{ color: "#666" }}>{key ? "authority view" : "public view (enter the authority key)"}</span>
        {updated && <span style={{ color: "#666" }}>· updated {updated}</span>}
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          saveKey();
        }}
        style={{ display: "flex", gap: 8, flexWrap: "wrap" }}
      >
        <input
          type="password"
          placeholder="Authority key (shown once at activation)"
          value={draftKey}
          onChange={(e) => setDraftKey(e.target.value)}
          style={{ flex: "1 1 260px", padding: 6 }}
          autoComplete="off"
        />
        <button type="submit">Use key</button>
      </form>
      {error && <p style={{ color: "#b00020", margin: 0 }}>{error}</p>}
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr)", gap: 12 }}>
        <Map bbox={bbox} layers={layers} visible={visible ?? []} hotspots={hotspots} />
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
          <LayerToggles layerIds={Object.keys(layers)} visible={visible ?? []} onChange={setVisible} />
          <div>
            <strong>Hotspots</strong>
            {hotspots.length === 0 ? (
              <p style={{ color: "#666", margin: "4px 0" }}>None.</p>
            ) : (
              <ul style={{ margin: "4px 0", paddingLeft: 18 }}>
                {hotspots.map((h) => (
                  <li key={h.id}>
                    {h.id}: peak {h.peak.toFixed(2)}, {Math.round(h.area_m2)} m², {h.persisted_ticks} ticks
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
