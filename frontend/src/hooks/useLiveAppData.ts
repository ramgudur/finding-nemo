// Live map + hotspots for an active app, ported from pages/AppPage.tsx's state machine:
// WS stream (first-frame auth) with REST polling as the fallback while the socket is down.
// Rule 8: without a key (or with a rejected one) only public items ever arrive; this hook never
// widens that, it only reports what the server sent.
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import { openStream, type StreamState } from "../api/stream";
import type { HotspotsResponse, MapResponse, POI, StreamFrame } from "../api/types";

const POLL_MS = 2000; // engine tick; used only while the stream is down
const POI_POLL_MS = 10_000; // POIs change on the labeler's cadence (~60 s), not every tick

const isAuthError = (e: unknown) => e instanceof ApiError && (e.status === 401 || e.status === 403);

export interface LiveAppData {
  map: MapResponse | null;
  hotspots: HotspotsResponse | null;
  /** POIs as the server filtered them for this key (public items only without one), or null before the first poll. */
  pois: POI[] | null;
  /** True while the WebSocket is open (otherwise REST polling is running). */
  connected: boolean;
  streamState: StreamState;
  /** Server time of the last frame or poll (MapResponse.t), or null before the first one. */
  lastUpdate: string | null;
  error: string | null;
  /** The authority key was rejected (401/403 or auth_ok:false); data falls back to the public view. */
  keyRejected: boolean;
  /** Whether data is being requested with an authority key (and it has not been rejected). */
  authority: boolean;
}

export function useLiveAppData(
  appId: string,
  authorityKey: string | null,
  opts?: { onAuthError?: () => void },
): LiveAppData {
  const [keyRejected, setKeyRejected] = useState(false);
  const [streamState, setStreamState] = useState<StreamState>("connecting");
  const [map, setMap] = useState<MapResponse | null>(null);
  const [hotspots, setHotspots] = useState<HotspotsResponse | null>(null);
  const [pois, setPois] = useState<POI[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const alive = useRef(true);
  const onAuthError = useRef(opts?.onAuthError);
  onAuthError.current = opts?.onAuthError;

  // A new app or key starts clean (never show one app's/view's data under another).
  useEffect(() => {
    setKeyRejected(false);
    setMap(null);
    setHotspots(null);
    setPois(null);
    setError(null);
  }, [appId, authorityKey]);

  const activeKey = keyRejected ? "" : (authorityKey ?? "");

  const reject = useCallback(() => {
    setKeyRejected(true);
    // drop authority data at once; the public view refills on the next frame/poll
    setMap(null);
    setHotspots(null);
    setPois(null);
    onAuthError.current?.();
  }, []);

  const apply = useCallback((m: MapResponse, h: HotspotsResponse) => {
    if (!alive.current) return;
    setMap(m);
    setHotspots(h);
    setError(null);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [m, h] = await Promise.all([api.map(appId, activeKey), api.hotspots(appId, activeKey)]);
      apply(m, h);
    } catch (e) {
      if (!alive.current) return;
      if (activeKey && isAuthError(e)) reject();
      else if (e instanceof ApiError && e.status === 404) setError(`App "${appId}" not found or not active.`);
      else setError(e instanceof Error ? e.message : String(e));
    }
  }, [appId, activeKey, apply, reject]);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  // Live stream: each tick frame carries the filtered map and hotspots.
  useEffect(() => {
    if (!appId) return;
    void refresh();
    return openStream(appId, activeKey, {
      onState: (s) => alive.current && setStreamState(s),
      onFrame: (f: StreamFrame) => {
        if (f.type === "auth_ok") {
          if (activeKey && !f.authority) reject();
        } else if (f.type === "tick") apply(f.map, f.hotspots);
      },
    });
  }, [appId, activeKey, refresh, apply, reject]);

  // Fallback polling while the stream is not open.
  useEffect(() => {
    if (!appId || streamState === "open") return;
    const id = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(id);
  }, [appId, streamState, refresh]);

  // POIs: their own slower poll (not on the stream). A failed poll keeps the last list; a rejected
  // key drops it like the map. The server filters per item, so nothing is filtered here.
  useEffect(() => {
    if (!appId) return;
    let cancelled = false;
    const load = async () => {
      try {
        const p = await api.pois(appId, activeKey || undefined);
        if (!cancelled && alive.current) setPois(p);
      } catch (e) {
        if (cancelled || !alive.current) return;
        if (activeKey && isAuthError(e)) reject();
      }
    };
    void load();
    const id = setInterval(() => void load(), POI_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [appId, activeKey, reject]);

  return {
    map,
    hotspots,
    pois,
    connected: streamState === "open",
    streamState,
    lastUpdate: map?.t ?? null,
    error: keyRejected ? "Authority key rejected. Showing the public view." : error,
    keyRejected,
    authority: Boolean(activeKey),
  };
}
