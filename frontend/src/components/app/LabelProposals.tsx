// POI Labeler label proposals (GET /label-proposals; accept / dismiss). Authority view only (rule 8):
// `why` is agent text and the parent renders this only with a valid key. Accepting adds the label to
// the running spec's label_set through the dev tool and guards, which write a spec_edit Change.
// Sensitive and destination are mutually exclusive (a destination label is a public routing target).
import { Check, ChevronDown, RefreshCw, Tag, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api } from "../../api/client";
import type { LabelProposal, PoiLabel } from "../../api/types";

const POLL_MS = 10_000;

function reason(e: unknown): string {
  if (!(e instanceof ApiError)) return e instanceof Error ? e.message : String(e);
  if (e.status === 404 && e.message === "Not Found") return "This backend doesn't offer label proposals yet.";
  if (e.status === 404) return "That proposal no longer exists. The list has been refreshed.";
  if (e.status === 409) return `Nothing changed: ${e.message}. The list has been refreshed.`;
  if (e.status === 422) return `The spec checks refused it: ${e.message}`;
  return e.message;
}

type Flags = { sensitive: boolean; destination: boolean };

export function LabelProposals({
  appId,
  authorityKey,
  labelSet,
  onRejected,
}: {
  appId: string;
  authorityKey: string;
  labelSet: PoiLabel[];
  onRejected: () => void;
}) {
  const [items, setItems] = useState<LabelProposal[] | null>(null);
  const [loadError, setLoadError] = useState("");
  // seeded from the spec once; an accept answers with the new label_set
  const [labels, setLabels] = useState<PoiLabel[]>(labelSet);
  const [flags, setFlags] = useState<Record<string, Flags>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, { ok: boolean; text: string }>>({});
  const [refreshing, setRefreshing] = useState(false);
  const alive = useRef(true);

  const load = useCallback(async () => {
    try {
      const list = await api.labelProposals(appId, authorityKey);
      if (!alive.current) return;
      setItems(list);
      setLoadError("");
    } catch (e) {
      if (!alive.current) return;
      if (e instanceof ApiError && e.status === 401) return onRejected();
      setLoadError(reason(e));
      setItems((prev) => prev ?? []);
    }
  }, [appId, authorityKey, onRejected]);

  useEffect(() => {
    alive.current = true;
    void load();
    const id = setInterval(() => void load(), POLL_MS);
    return () => {
      alive.current = false;
      clearInterval(id);
    };
  }, [load]);

  const flagsOf = (p: LabelProposal): Flags => flags[p.id] ?? { sensitive: !!p.sensitive, destination: !!p.destination && !p.sensitive };
  const setFlag = (p: LabelProposal, which: keyof Flags, on: boolean) => {
    const next = { ...flagsOf(p), [which]: on };
    // never both: turning one on turns the other off
    if (on) next[which === "sensitive" ? "destination" : "sensitive"] = false;
    setFlags((f) => ({ ...f, [p.id]: next }));
  };

  const act = async (p: LabelProposal, kind: "accept" | "dismiss") => {
    setBusy(p.id);
    try {
      if (kind === "accept") {
        const r = await api.acceptLabelProposal(appId, authorityKey, p.id, flagsOf(p));
        setLabels(r.label_set);
        setNotes((n) => ({ ...n, [p.id]: { ok: true, text: `“${r.proposal.label}” is now in the label set.` } }));
      } else {
        await api.dismissLabelProposal(appId, authorityKey, p.id);
        setNotes((n) => ({ ...n, [p.id]: { ok: true, text: "Dismissed." } }));
      }
      await load();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onRejected();
      setNotes((n) => ({ ...n, [p.id]: { ok: false, text: reason(e) } }));
      if (e instanceof ApiError && (e.status === 404 || e.status === 409)) await load();
    } finally {
      if (alive.current) setBusy(null);
    }
  };

  const refresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  if (items === null)
    return (
      <div aria-busy className="space-y-2">
        <div className="h-16 rounded-xl bg-white/50 motion-safe:animate-pulse" />
      </div>
    );

  const newest = [...items].sort((a, b) => b.t.localeCompare(a.t));
  const open = newest.filter((p) => (p.status ?? "open") === "open");
  const past = newest.filter((p) => (p.status ?? "open") !== "open");
  // notes for proposals that just left the open list (accepted / dismissed) stay visible above the history
  const doneNotes = past.filter((p) => notes[p.id]);

  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-[14px] font-semibold text-slate">Label proposals</h3>
          <p className="mt-0.5 max-w-[60ch] text-[12.5px] text-ink">
            New place labels the POI Labeler suggested. Nothing changes until you accept one.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={refreshing}
          aria-label="Refresh label proposals"
          className="inline-flex shrink-0 items-center gap-1 rounded-full bg-white/70 px-2.5 py-1 text-[12px] font-semibold text-ink ring-1 ring-paleWater transition-[transform,box-shadow] duration-150 hover:-translate-y-px hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mutedTeal disabled:opacity-50"
        >
          <RefreshCw aria-hidden className={`h-3 w-3 ${refreshing ? "motion-safe:animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {loadError && (
        <p role="alert" className="text-[12.5px] font-semibold text-danger">
          {loadError}
        </p>
      )}

      {open.length === 0 ? (
        <p className="rounded-xl bg-white/35 px-3 py-2.5 text-[12.5px] text-ink">
          No open proposals. The POI Labeler suggests a label when the data shows places that none of the current labels fit.
        </p>
      ) : (
        <ul className="space-y-2">
          {open.map((p) => {
            const f = flagsOf(p);
            const note = notes[p.id];
            const changed = f.sensitive !== !!p.sensitive || f.destination !== !!p.destination;
            return (
              <li key={p.id} className="rounded-xl border border-paleWater bg-white/45 px-3 py-2.5 text-[12.5px]">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <Tag aria-hidden className="h-3.5 w-3.5 text-mutedTeal" />
                  <span className="text-[13.5px] font-semibold text-slate">{p.label}</span>
                  <span className="font-mono text-[11px] text-inkSoft">{new Date(p.t).toLocaleTimeString()}</span>
                </div>
                <p className="mt-1 max-w-[65ch] text-ink">
                  <span className="font-semibold text-slate">Why: </span>
                  {p.why}
                </p>
                <fieldset className="mt-2 flex flex-wrap gap-x-4 gap-y-1.5">
                  <legend className="sr-only">Flags for {p.label}</legend>
                  <label className="flex items-center gap-1.5 text-ink">
                    <input
                      type="checkbox"
                      checked={f.sensitive}
                      disabled={busy !== null}
                      onChange={(e) => setFlag(p, "sensitive", e.target.checked)}
                      className="accent-mutedTeal"
                    />
                    Sensitive <span className="text-inkSoft">(authority view only)</span>
                  </label>
                  <label className="flex items-center gap-1.5 text-ink">
                    <input
                      type="checkbox"
                      checked={f.destination}
                      disabled={busy !== null}
                      onChange={(e) => setFlag(p, "destination", e.target.checked)}
                      className="accent-mutedTeal"
                    />
                    Routing destination <span className="text-inkSoft">(public)</span>
                  </label>
                </fieldset>
                <p className="mt-1 text-[11.5px] text-inkSoft">
                  A label can't be both: a destination is shown to the public on routes.
                  {changed && <span className="font-semibold text-slate"> Accepting uses your flags, not the proposed ones.</span>}
                </p>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => void act(p, "accept")}
                    className="inline-flex items-center gap-1 rounded-full bg-primary px-3 py-1 text-[12px] font-semibold text-cream shadow-[0_2px_8px_rgba(59,104,149,0.25)] transition-[transform,background-color] duration-150 hover:-translate-y-px hover:bg-primaryHover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mutedTeal focus-visible:ring-offset-1 disabled:translate-y-0 disabled:opacity-50"
                  >
                    <Check aria-hidden className="h-3.5 w-3.5" />
                    {busy === p.id ? "Working…" : "Accept"}
                  </button>
                  <button
                    type="button"
                    disabled={busy !== null}
                    onClick={() => void act(p, "dismiss")}
                    className="inline-flex items-center gap-1 rounded-full bg-white/70 px-3 py-1 text-[12px] font-semibold text-ink ring-1 ring-paleWater transition-[transform,box-shadow] duration-150 hover:-translate-y-px hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mutedTeal disabled:translate-y-0 disabled:opacity-50"
                  >
                    <X aria-hidden className="h-3.5 w-3.5" />
                    Dismiss
                  </button>
                  {note && (
                    <span role={note.ok ? "status" : "alert"} className={`text-[12px] font-semibold ${note.ok ? "text-mutedTeal" : "text-danger"}`}>
                      {note.text}
                    </span>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {doneNotes.length > 0 && (
        <ul className="space-y-1" aria-live="polite">
          {doneNotes.map((p) => (
            <li key={p.id} className="text-[12px] font-semibold text-mutedTeal">
              {p.label}: {notes[p.id]!.text}
            </li>
          ))}
        </ul>
      )}

      {labels.length > 0 && (
        <p className="text-[12px] text-ink">
          <span className="font-semibold text-slate">Current labels: </span>
          {labels.map((l, i) => (
            <span key={l.label}>
              {i > 0 && ", "}
              {l.label}
              {l.sensitive ? " (sensitive)" : l.destination ? " (destination)" : ""}
            </span>
          ))}
        </p>
      )}

      {past.length > 0 && (
        <details className="group rounded-xl bg-white/30 px-3 py-2 text-[12.5px]">
          <summary className="flex cursor-pointer list-none items-center gap-1 font-semibold text-slate [&::-webkit-details-marker]:hidden">
            <ChevronDown aria-hidden className="h-3.5 w-3.5 transition-transform duration-150 group-open:rotate-180" />
            History <span className="font-mono text-[11px] font-normal text-inkSoft">{past.length}</span>
          </summary>
          <ul className="mt-2 max-h-[220px] space-y-1.5 overflow-y-auto pr-1">
            {past.map((p) => (
              <li key={p.id} className="flex flex-wrap items-baseline gap-x-2">
                <span
                  className={`rounded-full px-1.5 py-px font-mono text-[10.5px] ${p.status === "accepted" ? "bg-seaGlass/35 text-slate" : "bg-softStone/60 text-ink"}`}
                >
                  {p.status}
                </span>
                <span className="font-semibold text-slate">{p.label}</span>
                <span className="font-mono text-[11px] text-inkSoft">{new Date(p.t).toLocaleString()}</span>
                <span className="w-full text-ink">{p.why}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
