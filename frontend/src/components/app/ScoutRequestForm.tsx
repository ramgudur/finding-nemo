// "Ask the Scout for sources": POST /v1/apps/{app}/scout {input_id, request} -> 202 ScoutJobAccepted.
// Authority view only. The Scout configures fetched (pull) inputs; a push input receives its own data,
// so it is listed but can't be picked. The Scout runs asynchronously on the shared rate budget and its
// steps appear in the Trace tab (an empty budget shows there as a skipped run).
import { ArrowRight, Compass } from "lucide-react";
import { useId, useState } from "react";
import { ApiError, api } from "../../api/client";
import type { InputSpec, ScoutJobAccepted } from "../../api/types";

const MAX = 500;

function reason(e: unknown): string {
  if (!(e instanceof ApiError)) return e instanceof Error ? e.message : String(e);
  if (e.status === 404 && e.message === "Not Found") return "This backend doesn't offer Scout requests yet.";
  if (e.status === 404) return "That input isn't in this app any more. Reload the page to see its current inputs.";
  if (e.status === 409) return "The app isn't active, so the Scout can't run for it.";
  if (e.status === 422) return `The request was refused: ${e.message}`;
  if (e.status === 429 || e.status === 503) return "The agents' rate budget is used up right now. Try again in a minute.";
  return e.message;
}

export function ScoutRequestForm({
  appId,
  authorityKey,
  inputs,
  onRejected,
  onOpenTrace,
}: {
  appId: string;
  authorityKey: string;
  inputs: InputSpec[];
  onRejected: () => void;
  onOpenTrace: () => void;
}) {
  const pulls = inputs.filter((i) => i.kind === "pull");
  const [inputId, setInputId] = useState(pulls[0]?.id ?? "");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [job, setJob] = useState<ScoutJobAccepted | null>(null);
  const ids = { input: useId(), text: useId(), count: useId(), note: useId() };

  const trimmed = text.trim();
  const canSend = !!inputId && trimmed.length >= 1 && trimmed.length <= MAX && !busy;

  const send = async () => {
    if (!canSend) return;
    setBusy(true);
    setError("");
    try {
      const r = await api.requestScout(appId, authorityKey, { input_id: inputId, request: trimmed });
      setJob(r);
      setText("");
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onRejected();
      setError(reason(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <div>
        <h3 className="flex items-center gap-1.5 text-[14px] font-semibold text-slate">
          <Compass aria-hidden className="h-4 w-4 text-mutedTeal" /> Ask the Scout for sources
        </h3>
        <p className="mt-0.5 max-w-[60ch] text-[12.5px] text-ink">
          Describe what a fetched input should cover. The Scout searches, configures and tests feeds for it, then they keep polling on
          their own.
        </p>
      </div>

      {pulls.length === 0 ? (
        <p id={ids.note} className="rounded-xl bg-white/35 px-3 py-2.5 text-[12.5px] text-ink">
          This app has no fetched (pull) inputs. The Scout configures fetched sources only; push inputs receive data from your own app.
        </p>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void send();
          }}
          className="space-y-2.5"
        >
          <div className="space-y-1">
            <label htmlFor={ids.input} className="block text-[12.5px] font-semibold text-slate">
              Input
            </label>
            <select
              id={ids.input}
              value={inputId}
              onChange={(e) => setInputId(e.target.value)}
              disabled={busy}
              aria-describedby={pulls.length < inputs.length ? ids.note : undefined}
              className="w-full rounded-lg border border-paleWater bg-white/80 px-2.5 py-1.5 font-mono text-[13px] text-slate focus:outline-none focus:ring-2 focus:ring-mutedTeal/40"
            >
              {inputs.map((i) => (
                <option key={i.id} value={i.id} disabled={i.kind !== "pull"}>
                  {i.id} · {i.kind === "pull" ? `fetched ${i.shape}` : "push, not available"}
                </option>
              ))}
            </select>
            {pulls.length < inputs.length && (
              <p id={ids.note} className="text-[11.5px] text-inkSoft">
                Push inputs are greyed out: the Scout configures fetched sources, and push inputs get their data from your app.
              </p>
            )}
          </div>

          <div className="space-y-1">
            <div className="flex items-baseline justify-between gap-2">
              <label htmlFor={ids.text} className="block text-[12.5px] font-semibold text-slate">
                What should it look for?
              </label>
              <span
                id={ids.count}
                aria-live="polite"
                className={`font-mono text-[11px] tabular-nums ${text.length > MAX ? "font-semibold text-danger" : "text-inkSoft"}`}
              >
                {text.length}/{MAX}
              </span>
            </div>
            <textarea
              id={ids.text}
              value={text}
              onChange={(e) => setText(e.target.value.slice(0, MAX))}
              maxLength={MAX}
              rows={3}
              disabled={busy}
              aria-describedby={ids.count}
              placeholder="e.g. local outlets that report incidents inside the map area"
              className="w-full resize-y rounded-lg border border-paleWater bg-white/80 px-2.5 py-1.5 text-[13px] text-slate placeholder:text-ink/70 focus:outline-none focus:ring-2 focus:ring-mutedTeal/40"
            />
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="submit"
              disabled={!canSend}
              className="inline-flex items-center gap-1 rounded-full bg-primary px-3.5 py-1.5 text-[12.5px] font-semibold text-cream shadow-[0_2px_8px_rgba(59,104,149,0.25)] transition-[transform,background-color] duration-150 hover:-translate-y-px hover:bg-primaryHover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mutedTeal focus-visible:ring-offset-1 disabled:translate-y-0 disabled:cursor-not-allowed disabled:opacity-45"
            >
              {busy ? "Sending…" : "Ask the Scout"}
            </button>
            {error && (
              <span role="alert" className="text-[12px] font-semibold text-danger">
                {error}
              </span>
            )}
          </div>
        </form>
      )}

      {job && (
        <div role="status" className="rounded-xl bg-seaGlass/20 px-3 py-2.5 text-[12.5px] text-slate ring-1 ring-seaGlass/60">
          <p className="font-semibold">Scout is on it.</p>
          <p className="mt-0.5 text-ink">
            Job <span className="font-mono text-slate">{job.job_id}</span> for <span className="font-mono text-slate">{job.input_id}</span>.
            Its steps, and any connectors it creates, appear in the Trace tab as it works.
          </p>
          <button
            type="button"
            onClick={onOpenTrace}
            className="mt-1.5 inline-flex items-center gap-1 rounded font-semibold text-slate underline underline-offset-2 hover:text-mutedTeal focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-mutedTeal"
          >
            Open the Trace <ArrowRight aria-hidden className="h-3.5 w-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}
