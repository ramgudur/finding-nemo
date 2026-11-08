// "Try it now": one real test event per push input, sent with the ingest key this tab still holds
// (only right after activation). Shows the HTTP status and body exactly as the server answered.
// The key is sent in the header only; it is never shown in the result or logged.
import { ArrowRight, Loader2, Send } from "lucide-react";
import { useState } from "react";
import type { AppSpec, InputSpec } from "../../api/types";
import { sampleEventBody } from "./snippets";

const HINTS: Record<number, string> = {
  401: "The ingest key was refused.",
  403: "The app isn't active.",
  404: "The app or input wasn't found.",
  422: "The event didn't match the input's schema; see the reason above.",
};

type Result = { status: number; body: string } | { error: string };

/** The same path the app's own client uses (/v1, proxied in dev), so it works wherever the page does. */
async function sendTest(spec: AppSpec, input: InputSpec, ingestKey: string): Promise<Result> {
  const body = sampleEventBody(spec, input, "setup-test");
  body.ts = new Date().toISOString();
  try {
    const r = await fetch(`/v1/apps/${encodeURIComponent(spec.app_id)}/inputs/${encodeURIComponent(input.id)}/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json", "X-Ingest-Key": ingestKey },
      body: JSON.stringify(body),
    });
    const text = await r.text();
    let pretty = text;
    try {
      pretty = JSON.stringify(JSON.parse(text));
    } catch {
      // not JSON: show it as sent
    }
    return { status: r.status, body: pretty.slice(0, 400) };
  } catch {
    return { error: "Couldn't reach the API. Is the backend running?" };
  }
}

function Row({ spec, input, ingestKey }: { spec: AppSpec; input: InputSpec; ingestKey: string | null }) {
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<Result | null>(null);
  const ok = res && "status" in res && res.status === 202;
  const run = async () => {
    if (!ingestKey) return;
    setBusy(true);
    setRes(await sendTest(spec, input, ingestKey));
    setBusy(false);
  };
  return (
    <li className="space-y-2 rounded-xl bg-sheet px-4 py-3 shadow-[inset_0_0_0_1px_rgb(var(--c-slate)/0.12)]">
      <div className="flex flex-wrap items-center gap-3">
        <span className="min-w-0 flex-1 font-mono text-[14px] font-semibold text-slate">{input.id}</span>
        <button
          type="button"
          onClick={() => void run()}
          disabled={!ingestKey || busy}
          className="keys-press tap inline-flex items-center gap-1.5 rounded-full bg-deep px-4 py-2 text-[13.5px] font-bold text-cream hover:bg-primaryHover disabled:cursor-not-allowed disabled:opacity-45 disabled:shadow-none"
        >
          {busy ? <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" /> : <Send aria-hidden className="h-3.5 w-3.5" />}
          Send a test event
        </button>
      </div>
      {res && (
        <div role="status" className="keys-reveal space-y-1.5">
          {"error" in res ? (
            <p className="text-[13.5px] text-danger">{res.error}</p>
          ) : (
            <p className="flex flex-wrap items-baseline gap-2 font-mono text-[13.5px]">
              <span className={`rounded-md px-1.5 py-0.5 font-semibold ${ok ? "bg-seaGlass/40 text-slate" : "bg-coral/30 text-danger"}`}>{res.status}</span>
              <span className="min-w-0 break-all text-slate">{res.body}</span>
            </p>
          )}
          {res && "status" in res && !ok && HINTS[res.status] && <p className="text-[13.5px] text-ink">{HINTS[res.status]}</p>}
          {ok && (
            <p className="text-[13.5px] text-ink">
              Accepted at the centre of your area as <span className="font-mono">setup-test</span>. It shows on the console map within a tick.
            </p>
          )}
        </div>
      )}
    </li>
  );
}

export function TryIt(props: { spec: AppSpec; inputs: InputSpec[]; ingestKey: string | null; onOpenConsole: () => void }) {
  const { spec, inputs, ingestKey, onOpenConsole } = props;
  return (
    <div className="space-y-2">
      {!ingestKey && (
        <p className="max-w-[68ch] rounded-xl bg-sheet2 px-3 py-2 text-[13.5px] text-ink">
          The ingest key is no longer in this tab, so the page can't send for you. Use an example above with your saved key.
        </p>
      )}
      <ul className="space-y-2">
        {inputs.map((i) => (
          <Row key={i.id} spec={spec} input={i} ingestKey={ingestKey} />
        ))}
      </ul>
      <button
        type="button"
        onClick={onOpenConsole}
        className="keys-press inline-flex items-center gap-1 rounded-full px-1 text-[13px] font-bold text-deep underline-offset-2 hover:underline"
      >
        See it land in the console <ArrowRight aria-hidden className="h-3.5 w-3.5" />
      </button>
      {ingestKey && <p className="text-[13.5px] text-ink">Save your keys first: leaving this page hides them for good.</p>}
    </div>
  );
}
