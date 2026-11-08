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
    <fieldset className="rounded-2xl border border-softAqua/70 bg-cream/60 px-4 pb-3 pt-2">
      <legend className="px-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-inkSoft">Simulation (demo only)</legend>
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          disabled={busy}
          onClick={() => void send({ paused: !clock.paused })}
          className="rounded-full bg-[#4F80B0] px-4 py-1.5 text-sm font-semibold text-cream shadow-sm transition hover:-translate-y-px disabled:opacity-50"
        >
          {clock.paused ? "Resume" : "Pause"}
        </button>
        <label className="flex items-center gap-2 text-sm text-ink">
          speed
          <select
            value={speed}
            disabled={busy}
            onChange={(e) => {
              const s = Number(e.target.value);
              setSpeed(s);
              void send({ speed: s });
            }}
            className="rounded-full border border-softAqua bg-mist px-2 py-1 font-mono text-xs text-slate"
          >
            {SPEEDS.map((s) => (
              <option key={s} value={s}>
                {s}x
              </option>
            ))}
          </select>
        </label>
        <span className="font-mono text-[11px] text-inkSoft">
          {clock.paused ? "paused" : `running at ${clock.speed ?? 1}x`} · {new Date(clock.now).toLocaleTimeString()}
        </span>
      </div>
      {!clock.controllable && <p className="mt-1 text-xs text-inkSoft">No sim clock installed: controls have no effect.</p>}
      {error && <p className="mt-1 text-xs text-coral">{error}</p>}
    </fieldset>
  );
}
