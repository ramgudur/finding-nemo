// Authority-view panels: weights (with dev pin), verification scores and the agent trace, polled together.
import { useCallback, useEffect, useState } from "react";
import { api } from "../../api/client";
import type { TraceEntry, VerificationView, WeightsView } from "../../api/types";
import { AgentTrace } from "../AgentTrace/AgentTrace";
import { VerificationScores } from "../VerificationScores/VerificationScores";
import { WeightsPanel } from "../WeightsPanel/WeightsPanel";

const POLL_MS = 4000;

export function ConsolePanels({ appId, authorityKey }: { appId: string; authorityKey: string }) {
  const [weights, setWeights] = useState<WeightsView | null>(null);
  const [verification, setVerification] = useState<VerificationView | null>(null);
  const [trace, setTrace] = useState<TraceEntry[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const [w, v, t] = await Promise.allSettled([
      api.weights(appId, authorityKey),
      api.verification(appId, authorityKey),
      api.trace(appId, authorityKey),
    ]);
    if (w.status === "fulfilled") setWeights(w.value);
    if (v.status === "fulfilled") setVerification(v.value);
    if (t.status === "fulfilled") setTrace(t.value);
  }, [appId, authorityKey]);

  useEffect(() => {
    void load();
    const id = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  const pin = async (layerId: string, pinned: boolean) => {
    setBusy(true);
    setError("");
    try {
      setWeights(await api.pin(appId, authorityKey, layerId, { pinned, reason: pinned ? "pinned by dev" : "unpinned by dev" }));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section style={{ display: "grid", gap: 12, borderTop: "1px solid #eee", paddingTop: 12 }}>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
        {weights && <WeightsPanel view={weights} busy={busy} onPin={(l, p) => void pin(l, p)} />}
        {verification && <VerificationScores view={verification} />}
      </div>
      {error && <p style={{ color: "#b00020", margin: 0 }}>{error}</p>}
      <AgentTrace entries={trace} />
    </section>
  );
}
