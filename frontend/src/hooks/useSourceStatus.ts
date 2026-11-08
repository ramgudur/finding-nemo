// GET /sources/status for the console (authority only), polled every 10 s. A backend without the
// route answers 404: `unsupported` turns on and the console shows the spec's sources without status.
import { useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type { SourceStatus } from "../api/types";

const POLL_MS = 10_000;

export interface SourceStatusData {
  rows: SourceStatus[] | null;
  /** This backend has no /sources/status route. */
  unsupported: boolean;
  error: string | null;
  /** Wall-clock ms when `rows` arrived, for the "checked N s ago" line. */
  fetchedAt: number | null;
  refresh: () => void;
}

export function useSourceStatus(appId: string, key: string | null, onRejected?: () => void): SourceStatusData {
  const [rows, setRows] = useState<SourceStatus[] | null>(null);
  const [unsupported, setUnsupported] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fetchedAt, setFetchedAt] = useState<number | null>(null);
  const [nonce, setNonce] = useState(0);
  const rejected = useRef(onRejected);
  rejected.current = onRejected;

  useEffect(() => {
    setRows(null);
    setUnsupported(false);
    setError(null);
    setFetchedAt(null);
  }, [appId, key]);

  useEffect(() => {
    if (!appId || !key) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const r = await api.sourcesStatus(appId, key);
        if (!alive) return;
        setRows(r);
        setError(null);
        setFetchedAt(Date.now());
      } catch (e) {
        if (!alive) return;
        if (e instanceof ApiError && e.status === 401) return rejected.current?.();
        if (e instanceof ApiError && e.status === 404 && (e.message === "Not Found" || /404/.test(e.message))) {
          setUnsupported(true);
          return; // no route: stop polling
        }
        setError(e instanceof Error ? e.message : String(e));
      }
      if (alive) timer = setTimeout(() => void load(), POLL_MS);
    };
    void load();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [appId, key, nonce]);

  return { rows, unsupported, error, fetchedAt, refresh: () => setNonce((n) => n + 1) };
}

/** A clock that re-renders its caller every `ms` (for countdowns and "N s ago"). */
export function useNow(ms = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}
