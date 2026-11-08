// The latest vector (speed + direction) for each vector-field layer in the spec (#284), from the
// authority-only GET /inputs/{input}/raw?limit=1. Rule 8: it runs only with the key, and a 401 drops
// back to the public view. Vector inputs are usually polled sources, so 30 s is plenty.
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "../api/client";
import type { AppSpec } from "../api/types";
import { readVector, vectorLayers, type VectorLayer, type VectorReading } from "../components/architecture/vectorField";

const POLL_MS = 30_000;

export interface VectorState {
  layer: VectorLayer;
  /** null until the first event with both fields arrives. */
  reading: VectorReading | null;
}

export function useVectorReadings(appId: string, key: string | null, spec: AppSpec | null, onRejected?: () => void): VectorState[] {
  const layers = useMemo(() => (key ? vectorLayers(spec) : []), [spec, key]);
  const [readings, setReadings] = useState<Record<string, VectorReading | null>>({});
  const rejected = useRef(onRejected);
  rejected.current = onRejected;
  const sig = layers.map((l) => `${l.layerId}:${l.inputId}:${l.speedField}:${l.dirField}:${l.convention}`).join("|");

  useEffect(() => {
    setReadings({});
    if (!appId || !key || layers.length === 0) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      const next: Record<string, VectorReading | null> = {};
      for (const l of layers) {
        try {
          const [e] = await api.raw(appId, l.inputId, key, { limit: 1 });
          next[l.layerId] = readVector(l, e);
        } catch (err) {
          if (!alive) return;
          if (err instanceof ApiError && err.status === 401) return rejected.current?.();
          // a failed poll keeps the last reading
        }
      }
      if (!alive) return;
      setReadings((prev) => ({ ...prev, ...next }));
      timer = setTimeout(() => void load(), POLL_MS);
    };
    void load();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appId, key, sig]);

  return useMemo(() => layers.map((layer) => ({ layer, reading: readings[layer.layerId] ?? null })), [layers, readings]);
}
