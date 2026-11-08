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

export function AgentTrace({ entries, empty = "No agent runs yet." }: { entries: TraceEntry[]; empty?: string }) {
  const newest = [...entries].reverse();
  if (newest.length === 0) return <p className="text-[12.5px] text-inkSoft">{empty}</p>;
  return (
    <ul className="max-h-[420px] space-y-2 overflow-y-auto pr-1">
      {newest.map((e, i) => (
        <li key={`${e.t}-${i}`} className="rounded-xl border border-paleWater bg-white/35 px-3 py-2 text-[12.5px]">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <span className="font-semibold text-slate">{e.agent}</span>
            <span className="font-mono text-[11px] text-inkSoft">{new Date(e.t).toLocaleTimeString()}</span>
            {e.model && <span className="break-all font-mono text-[11px] text-inkSoft">{e.model}</span>}
          </div>
          {e.tool_calls.length > 0 && (
            <div className="mt-1 flex flex-wrap gap-1">
              {e.tool_calls.map((c, n) => (
                <span
                  key={n}
                  title={c.result_summary}
                  className={`rounded-full px-1.5 py-px font-mono text-[10.5px] ${c.ok ? "bg-seaGlass/25 text-tierStiff" : "bg-coral/25 text-danger"}`}
                >
                  {c.name}
                  {c.ok ? "" : " (failed)"}
                </span>
              ))}
            </div>
          )}
          {e.skipped_reason ? (
            <div className="mt-1 text-attn">skipped: {e.skipped_reason}</div>
          ) : (
            <dl className="mt-1 grid grid-cols-[max-content_1fr] gap-x-2 gap-y-0.5">
              {FIELDS.filter(([k]) => e[k]).map(([k, label]) => (
                <div key={k} className="contents">
                  <dt className="text-inkSoft">{label}</dt>
                  <dd className="text-ink [overflow-wrap:anywhere]">{String(e[k])}</dd>
                </div>
              ))}
            </dl>
          )}
        </li>
      ))}
    </ul>
  );
}
