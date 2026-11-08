// Verification methods in the spec's priority order, with their current value and whether they have data.
import type { VerificationView } from "../../api/types";

export function VerificationScores({ view }: { view: VerificationView }) {
  return (
    <fieldset style={{ border: "1px solid #ddd", borderRadius: 4, padding: "8px 12px" }}>
      <legend>Verification</legend>
      {view.methods.length === 0 ? (
        <p style={{ margin: 0, color: "#666" }}>Not enough ticks cached yet.</p>
      ) : (
        <ol style={{ margin: 0, paddingLeft: 20, fontSize: 14 }}>
          {view.methods.map((m) => (
            <li key={m.method}>
              {m.method}:{" "}
              {m.has_data && m.value !== null ? (
                <strong style={{ fontVariantNumeric: "tabular-nums" }}>{m.value.toFixed(3)}</strong>
              ) : (
                <span style={{ color: "#666" }}>no data</span>
              )}
            </li>
          ))}
        </ol>
      )}
      <p style={{ margin: "6px 0 0", fontSize: 14, color: view.cold_start ? "#8a5a00" : "#444" }}>
        {view.cold_start
          ? "Cold start: flexible weight changes are rejected until two methods have data."
          : `${view.ticks} ticks in the last ${Math.round(view.window_s)} s`}
      </p>
    </fieldset>
  );
}
