// Newest-first agent trace: what each run saw, hypothesized, tested, found, did and why (or why it skipped).
import type { TraceEntry } from "../../api/types";

const FIELDS: [keyof TraceEntry, string][] = [
  ["saw", "saw"],
  ["hypothesis", "hypothesis"],
  ["tested", "tested"],
  ["result", "result"],
  ["action", "action"],
  ["why", "why"],
];

export function AgentTrace({ entries }: { entries: TraceEntry[] }) {
  const newest = [...entries].reverse();
  return (
    <fieldset style={{ border: "1px solid #ddd", borderRadius: 4, padding: "8px 12px", minWidth: 0 }}>
      <legend>Agent trace</legend>
      {newest.length === 0 ? (
        <p style={{ margin: 0, color: "#666" }}>No agent runs yet.</p>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0, maxHeight: 360, overflowY: "auto" }}>
          {newest.map((e, i) => (
            <li key={`${e.t}-${i}`} style={{ borderTop: i ? "1px solid #eee" : "none", padding: "6px 0", fontSize: 14 }}>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "baseline" }}>
                <strong>{e.agent}</strong>
                <span style={{ color: "#666" }}>{new Date(e.t).toLocaleTimeString()}</span>
                {e.model && <span style={{ color: "#666" }}>{e.model}</span>}
                {e.tool_calls.length > 0 && (
                  <span style={{ color: "#666" }}>tools: {e.tool_calls.map((c) => c.name + (c.ok ? "" : " (failed)")).join(", ")}</span>
                )}
              </div>
              {e.skipped_reason ? (
                <div style={{ color: "#8a5a00" }}>skipped: {e.skipped_reason}</div>
              ) : (
                <dl style={{ margin: "2px 0 0", display: "grid", gridTemplateColumns: "max-content 1fr", gap: "0 8px" }}>
                  {FIELDS.filter(([k]) => e[k]).map(([k, label]) => (
                    <div key={k} style={{ display: "contents" }}>
                      <dt style={{ color: "#666" }}>{label}</dt>
                      <dd style={{ margin: 0, overflowWrap: "anywhere" }}>{String(e[k])}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </li>
          ))}
        </ul>
      )}
    </fieldset>
  );
}
