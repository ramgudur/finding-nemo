// DEMO_MODE: pause/resume and speed for the server's simulation clock. The simulator runs with
// --follow-clock, so it pauses and speeds up with the engine.
import { useState } from "react";
import { api } from "../../api/client";
import type { ClockState } from "../../api/types";

const SPEEDS = [0.5, 1, 2, 4, 8];

interface Props {
  appId: string;
  authorityKey: string;
  clock: ClockState;
  onChange: (c: ClockState) => void;
}

export function SimControls({ appId, authorityKey, clock, onChange }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [speed, setSpeed] = useState(clock.speed ?? 1);

  const send = async (body: { paused?: boolean; speed?: number }) => {
    setBusy(true);
    setError("");
    try {
      onChange(await api.controlClock(appId, authorityKey, body));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <fieldset style={{ border: "1px solid #ddd", borderRadius: 4, padding: "8px 12px" }}>
      <legend>Simulation (demo only)</legend>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <button type="button" disabled={busy} onClick={() => void send({ paused: !clock.paused })}>
          {clock.paused ? "Resume" : "Pause"}
        </button>
        <label>
          speed{" "}
          <select
            value={speed}
            disabled={busy}
            onChange={(e) => {
              const s = Number(e.target.value);
              setSpeed(s);
              void send({ speed: s });
            }}
          >
            {SPEEDS.map((s) => (
              <option key={s} value={s}>
                {s}x
              </option>
            ))}
          </select>
        </label>
        <span style={{ color: "#666", fontSize: 13 }}>
          {clock.paused ? "paused" : `running at ${clock.speed ?? 1}x`} · {new Date(clock.now).toLocaleTimeString()}
        </span>
      </div>
      {error && <p style={{ color: "#b00020", margin: "4px 0 0" }}>{error}</p>}
    </fieldset>
  );
}
