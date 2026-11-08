// Authority-view console data (weights, verification, trace, Change log), polled together.
// Ported from ConsolePanels.tsx. Rule 8: nothing is fetched without a key, and a 401 clears the data.
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type { Change, TraceEntry, VerificationView, WeightsView } from "../api/types";

const POLL_MS = 4000;

export type RevertResult = { ok: true; change: Change } | { ok: false; reason: string };

export interface AuthorityConsoleData {
  weights: WeightsView | null;
  verification: VerificationView | null;
  trace: TraceEntry[];
  changes: Change[];
  /** True once any request came back 401: the key is wrong for this app. */
  rejected: boolean;
  loaded: boolean;
  pinBusy: boolean;
  pinError: string;
  pin: (layerId: string, pinned: boolean) => Promise<void>;
  revert: (changeId: string) => Promise<RevertResult>;
  refresh: () => Promise<void>;
}

/** A revert failure as a sentence the dev can act on (api/trace.py: 404 / 409 stale / 422 guard). */
function revertReason(e: unknown): string {
  if (!(e instanceof ApiError)) return String(e);
  if (e.status === 401) return "Key rejected.";
  if (e.status === 404 && e.message === "Not Found") return "This backend doesn't offer revert yet.";
  if (e.status === 404) return `Not found: ${e.message}`;
  if (e.status === 409) return `Can't revert, the target has changed since: ${e.message}`;
  if (e.status === 422) return `Not revertible: ${e.message.replace(/^rejected: /, "a guard rejected it: ")}`;
  return e.message;
}

export function useAuthorityConsoleData(appId: string, key: string | null): AuthorityConsoleData {
  const [weights, setWeights] = useState<WeightsView | null>(null);
  const [verification, setVerification] = useState<VerificationView | null>(null);
  const [trace, setTrace] = useState<TraceEntry[]>([]);
  const [changes, setChanges] = useState<Change[]>([]);
  const [rejected, setRejected] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [pinBusy, setPinBusy] = useState(false);
  const [pinError, setPinError] = useState("");
  const keyRef = useRef(key);
  keyRef.current = key;

  const clear = () => {
    setWeights(null);
    setVerification(null);
    setTrace([]);
    setChanges([]);
  };

  const load = useCallback(async () => {
    if (!key) return;
    const [w, v, t, c] = await Promise.allSettled([
      api.weights(appId, key),
      api.verification(appId, key),
      api.trace(appId, key),
      api.changes(appId, key),
    ]);
    if (keyRef.current !== key) return; // the key changed while this was in flight
    const unauthorized = [w, v, t, c].some((r) => r.status === "rejected" && r.reason instanceof ApiError && r.reason.status === 401);
    if (unauthorized) {
      clear();
      setRejected(true);
      setLoaded(true);
      return;
    }
    setRejected(false);
    if (w.status === "fulfilled") setWeights(w.value);
    if (v.status === "fulfilled") setVerification(v.value);
    if (t.status === "fulfilled") setTrace(t.value);
    if (c.status === "fulfilled") setChanges(c.value);
    setLoaded(true);
  }, [appId, key]);

  useEffect(() => {
    clear();
    setRejected(false);
    setLoaded(false);
    if (!key) return;
    void load();
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [load, key]);

  const pin = useCallback(
    async (layerId: string, pinned: boolean) => {
      if (!key) return;
      setPinBusy(true);
      setPinError("");
      try {
        setWeights(await api.pin(appId, key, layerId, { pinned, reason: pinned ? "pinned by dev" : "unpinned by dev" }));
        void load(); // the pin wrote a Change
      } catch (e) {
        setPinError(e instanceof ApiError && e.status === 401 ? "Key rejected." : e instanceof ApiError ? e.message : String(e));
      } finally {
        setPinBusy(false);
      }
    },
    [appId, key, load],
  );

  const revert = useCallback(
    async (changeId: string): Promise<RevertResult> => {
      if (!key) return { ok: false, reason: "Authority key required." };
      try {
        const change = await api.revertChange(appId, key, changeId);
        await load();
        return { ok: true, change };
      } catch (e) {
        return { ok: false, reason: revertReason(e) };
      }
    },
    [appId, key, load],
  );

  return { weights, verification, trace, changes, rejected, loaded, pinBusy, pinError, pin, revert, refresh: load };
}
