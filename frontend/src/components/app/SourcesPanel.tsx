// The console's Sources tab (authority view): one row per input, in plain words, from GET
// /sources/status. On a backend without that route the rows still list the spec's inputs, without status.
import { ArrowUpRight, CloudDownload, Send } from "lucide-react";
import { Link } from "react-router-dom";
import type { InputSpec, SourceStatus } from "../../api/types";
import { useNow, type SourceStatusData } from "../../hooks/useSourceStatus";
import { TONE_DOT, VIA_LABEL, describeSource } from "./sourceStatus";

export function SourcesPanel({ appId, inputs, status }: { appId: string; inputs: InputSpec[]; status: SourceStatusData }) {
  const now = useNow(1000);
  const byId = new Map((status.rows ?? []).map((r) => [r.input_id, r]));
  const keysHref = `/apps/${encodeURIComponent(appId)}/keys`;

  if (inputs.length === 0) return <p className="text-[13px] text-ink">This app has no inputs.</p>;

  return (
    <div className="space-y-3">
      {status.unsupported && (
        <p className="rounded-xl bg-white/45 px-3 py-2 text-[12.5px] text-ink">
          This backend doesn't report source status yet, so only the configuration is shown.
        </p>
      )}
      {status.error && !status.unsupported && (
        <p role="alert" className="rounded-xl bg-coral/15 px-3 py-2 text-[12.5px] text-danger">
          Couldn't check the sources: {status.error}. Retrying every 10 s.
        </p>
      )}
      <ul className="space-y-2 p-0">
        {inputs.map((i) => (
          <SourceRow key={i.id} input={i} row={byId.get(i.id) ?? null} loading={!status.unsupported && status.rows === null && !status.error} fetched={status.rows !== null} now={now} keysHref={keysHref} />
        ))}
      </ul>
      {status.fetchedAt && <p className="font-mono text-[11px] text-inkSoft">checked {Math.max(0, Math.round((now - status.fetchedAt) / 1000))} s ago · refreshes every 10 s</p>}
    </div>
  );
}

function SourceRow({ input, row, loading, fetched, now, keysHref }: { input: InputSpec; row: SourceStatus | null; loading: boolean; fetched: boolean; now: number; keysHref: string }) {
  const Icon = input.kind === "push" ? Send : CloudDownload;
  const line = row ? describeSource(row, now) : null;
  const via = row?.via ? VIA_LABEL[row.via] : input.kind === "push" ? (input.webhook ? "POST API or webhook" : "POST API") : (input.connector?.type ?? "no connector");
  return (
    <li className="list-none rounded-2xl bg-white/50 px-3.5 py-3 ring-1 ring-paleWater/70">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Icon aria-hidden className="h-3.5 w-3.5 text-mutedTeal" />
        <span className="min-w-0 break-all font-mono text-[13px] font-medium text-slate">{input.id}</span>
        <span className="text-[12px] text-ink">
          {input.kind} · {via}
          {row?.poll_s ? ` · every ${row.poll_s >= 120 ? `${Math.round(row.poll_s / 60)} min` : `${row.poll_s} s`}` : ""}
        </span>
      </div>
      {input.meaning && <p className="mt-1 line-clamp-2 max-w-[70ch] text-[12.5px] text-ink">{input.meaning}</p>}
      <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px]">
        {loading ? (
          <span aria-busy className="h-4 w-52 rounded bg-white/70 motion-safe:animate-pulse" />
        ) : line ? (
          <>
            <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${TONE_DOT[line.tone]}`} />
            <span className={`font-semibold ${line.tone === "error" ? "text-danger" : "text-slate"}`}>{line.text}</span>
            {line.tone === "empty" && (
              <Link to={keysHref} className="hit relative inline-flex items-center gap-0.5 rounded font-semibold text-primary underline underline-offset-2 hover:text-primaryHover">
                send one (see keys page) <ArrowUpRight aria-hidden className="h-3.5 w-3.5" />
              </Link>
            )}
          </>
        ) : fetched ? (
          <span className="text-ink">No status reported for this input.</span>
        ) : null}
      </div>
    </li>
  );
}
