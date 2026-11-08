// WS /v1/apps/{app}/stream. The browser can't set headers on a WebSocket, so the authority key goes
// in the first frame: {type: "auth", key}. Until a valid key arrives the server sends public-only frames.
import type { HotspotsResponse, MapResponse } from "./types";

export type StreamFrame =
  | { type: "auth_ok"; authority: boolean }
  | { type: "tick"; t: string; map?: MapResponse; hotspots?: HotspotsResponse };

export type StreamState = "connecting" | "open" | "closed";

interface Options {
  onFrame: (f: StreamFrame) => void;
  onState: (s: StreamState) => void;
}

const MAX_BACKOFF_MS = 15000;

/** Opens the stream, sends the auth frame first, reconnects with backoff. Returns a close function. */
export function openStream(appId: string, authorityKey: string, { onFrame, onState }: Options): () => void {
  let ws: WebSocket | null = null;
  let closed = false;
  let backoff = 1000;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const connect = () => {
    onState("connecting");
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/v1/apps/${encodeURIComponent(appId)}/stream`);
    ws.onopen = () => {
      backoff = 1000;
      if (authorityKey) ws?.send(JSON.stringify({ type: "auth", key: authorityKey }));
      onState("open");
    };
    ws.onmessage = (e) => {
      try {
        const f = JSON.parse(String(e.data)) as StreamFrame;
        if (f && (f.type === "tick" || f.type === "auth_ok")) onFrame(f);
      } catch {
        // ignore frames that aren't JSON
      }
    };
    ws.onclose = () => {
      onState("closed");
      if (closed) return;
      timer = setTimeout(connect, backoff);
      backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
    };
  };

  connect();
  return () => {
    closed = true;
    clearTimeout(timer);
    ws?.close();
  };
}
