// The Change log, newest first, with a dev Revert per change (POST /changes/{id}/revert).
// The server decides: a stale target is 409, a guard or a non-revertible kind is 422; both are shown as sentences.
import { RotateCcw, Undo2 } from "lucide-react";
import { useState } from "react";
import type { Change } from "../../api/types";
import type { RevertResult } from "../../hooks/useAuthorityConsoleData";

const NOT_REVERTIBLE = new Set(["spec_confirm", "spec_edit"]);
const shortId = (id: string) => id.replace(/-/g, "").slice(0, 8);

function compact(v: unknown): string {
  if (v === null || v === undefined) return "—";
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s.length > 90 ? `${s.slice(0, 87)}…` : s;
}

export function ChangesList({ changes, onRevert }: { changes: Change[]; onRevert: (id: string) => Promise<RevertResult> }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, { ok: boolean; text: string }>>({});
  const newest = [...changes].sort((a, b) => b.t.localeCompare(a.t));
  const revertedBy = new Map(changes.filter((c) => c.reverts).map((c) => [c.reverts as string, c.id]));

  const revert = async (id: string) => {
    setBusy(id);
    const r = await onRevert(id);
    setNotes((n) => ({ ...n, [id]: r.ok ? { ok: true, text: `Reverted as ${shortId(r.change.id)}.` } : { ok: false, text: r.reason } }));
    setBusy(null);
  };

  if (newest.length === 0) return <p className="text-[12.5px] text-inkSoft">No changes recorded yet.</p>;
  return (
    <ul className="max-h-[420px] space-y-2 overflow-y-auto pr-1">
      {newest.map((c) => {
        const by = revertedBy.get(c.id);
        const note = notes[c.id];
        const canRevert = !NOT_REVERTIBLE.has(c.kind) && !by;
        return (
          <li key={c.id} className="rounded-xl border border-paleWater bg-white/35 px-3 py-2 text-[12.5px]">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="rounded-full bg-paleWater/70 px-1.5 py-px font-mono text-[10.5px] text-slate">{c.kind}</span>
              <span className="break-all font-mono text-slate">{c.target}</span>
              <span className="text-[11px] text-inkSoft">
                {c.actor} · <span className="font-mono">{new Date(c.t).toLocaleTimeString()}</span> ·{" "}
                <span className="font-mono">{shortId(c.id)}</span>
              </span>
              {canRevert && (
                <button
                  type="button"
                  disabled={busy !== null}
                  onClick={() => void revert(c.id)}
                  className="ml-auto inline-flex items-center gap-1 rounded-full bg-white/70 px-2 py-0.5 text-[11px] font-semibold text-ink ring-1 ring-paleWater transition-[transform,box-shadow] duration-150 hover:-translate-y-px hover:shadow-sm disabled:opacity-50"
                >
                  <RotateCcw aria-hidden className={`h-3 w-3 ${busy === c.id ? "animate-spin" : ""}`} />
                  {busy === c.id ? "reverting" : "Revert"}
                </button>
              )}
            </div>
            <p className="mt-1 text-ink">{c.reason}</p>
            <p className="mt-0.5 break-all font-mono text-[11px] text-inkSoft">
              {compact(c.before)} <span className="text-mutedTeal">→</span> {compact(c.after)}
            </p>
            {(c.reverts || by || NOT_REVERTIBLE.has(c.kind)) && (
              <p className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-inkSoft">
                {c.reverts && (
                  <span className="inline-flex items-center gap-1">
                    <Undo2 aria-hidden className="h-3 w-3" /> reverts <span className="font-mono">{shortId(c.reverts)}</span>
                  </span>
                )}
                {by && (
                  <span>
                    reverted by <span className="font-mono">{shortId(by)}</span>
                  </span>
                )}
                {NOT_REVERTIBLE.has(c.kind) && <span>spec changes aren't revertible here</span>}
              </p>
            )}
            {note && (
              <p role="status" className={`mt-1 text-[11.5px] ${note.ok ? "text-mutedTeal" : "text-[#8A4B39]"}`}>
                {note.text}
              </p>
            )}
          </li>
        );
      })}
    </ul>
  );
}
