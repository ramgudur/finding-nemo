// "Test your app" (authority view): POST /simulate sends simulated events to a push input, either
// scattered reports from distinct ids or a spoof burst (many new ids at one point), so the dev can see
// the map, the Critic and the spoof checks react without a real device. Events are traced as simulated.
// "Run full simulation" (mode "line") places clusters along a diagonal across the area and the server keeps
// posting waves for a few minutes; "Run Critic now" / "Run Labeler now" queue a triggered agent run.
import { Bot, Crosshair, FlaskConical, MapPin, Route, ShieldAlert, Square, Tag } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError, api } from "../../api/client";
import type { AppSpec, SimPoint, RunnableAgent, SimulateMode, SimulateResult } from "../../api/types";

export interface TestPoint {
  lat: number;
  lon: number;
  radius_m: number;
}

export interface TestControlsProps {
  appId: string;
  authorityKey: string;
  spec: AppSpec;
  point: TestPoint;
  onPointChange: (p: TestPoint) => void;
  picking: boolean;
  onPickingChange: (on: boolean) => void;
  onRejected: () => void;
  onOpenTab: (tab: "trace" | "sources") => void;
  /** The path of a running full simulation (null when none), for the map's dashed preview. */
  onLineChange?: (line: { from: SimPoint; to: SimPoint } | null) => void;
}

/** Full-simulation defaults (contract B). */
const LINE_CLUSTERS = 8;
const LINE_COUNT = 15;
const LINE_DURATION_S = 240;

/** From near the top-left (10% down, 25% in) to near the bottom-right (10% in from bottom and right). */
export function linePoints(spec: AppSpec): { from: SimPoint; to: SimPoint } {
  const b = spec.grid.bbox;
  const h = b.max_lat - b.min_lat;
  const w = b.max_lon - b.min_lon;
  const r = (v: number) => Number(v.toFixed(6));
  return {
    from: { lat: r(b.max_lat - 0.1 * h), lon: r(b.min_lon + 0.25 * w) },
    to: { lat: r(b.min_lat + 0.1 * h), lon: r(b.max_lon - 0.1 * w) },
  };
}

/** Cluster spread scaled to the grid: about 1.5 x cell_m x 3, clamped to 10..5000 m. */
export const lineRadius = (spec: AppSpec) => clampInt(1.5 * spec.grid.cell_m * 3, 10, 5000);

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.max(0, s) % 60).padStart(2, "0")}`;

const AGENT_NAME: Record<RunnableAgent, string> = { critic: "Critic", poi_labeler: "Labeler" };

type Outcome = { mode: SimulateMode; input: string; result: SimulateResult } | { error: string };

export function centreOf(spec: AppSpec): { lat: number; lon: number } {
  const b = spec.grid.bbox;
  return { lat: (b.min_lat + b.max_lat) / 2, lon: (b.min_lon + b.max_lon) / 2 };
}

export function inArea(spec: AppSpec, lat: number, lon: number): boolean {
  const b = spec.grid.bbox;
  return lat >= b.min_lat && lat <= b.max_lat && lon >= b.min_lon && lon <= b.max_lon;
}

const clampInt = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, Math.round(Number.isFinite(v) ? v : lo)));

function failure(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 404 && e.message === "Not Found") return "This backend can't simulate events yet (no /simulate route).";
    if (e.status === 422) return `Not sent: ${e.message}`;
    if (e.status === 409) return `Not sent: ${e.message}`;
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}

export function TestControls({ appId, authorityKey, spec, point, onPointChange, picking, onPickingChange, onRejected, onOpenTab, onLineChange }: TestControlsProps) {
  const push = (spec.inputs ?? []).filter((i) => i.kind === "push");
  const [inputId, setInputId] = useState(push[0]?.id ?? "");
  const [count, setCount] = useState(30); // the new-id-burst spoof check needs at least 15
  const [busy, setBusy] = useState<SimulateMode | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);

  useEffect(() => {
    if (!push.some((i) => i.id === inputId)) setInputId(push[0]?.id ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spec]);

  // Full simulation: running until endsAt (ms), cleared when the timer runs out or Stop is pressed.
  const [line, setLine] = useState<{ endsAt: number; input: string; result: SimulateResult } | null>(null);
  const [lineBusy, setLineBusy] = useState<"start" | "stop" | null>(null);
  const [lineError, setLineError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const lineChange = useRef(onLineChange);
  lineChange.current = onLineChange;
  useEffect(() => {
    if (!line) return;
    const id = window.setInterval(() => {
      const t = Date.now();
      setNow(t);
      if (t >= line.endsAt) setLine(null);
    }, 1000);
    return () => window.clearInterval(id);
  }, [line]);
  useEffect(() => {
    lineChange.current?.(line ? linePoints(spec) : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [line]);
  useEffect(() => () => lineChange.current?.(null), []);

  if (push.length === 0)
    return (
      <Card>
        <p className="text-[13px] text-ink">
          Simulation sends events to a push input, and this app only pulls data. Its sources are polled on their own; see the Sources tab.
        </p>
        <AgentRunRow appId={appId} authorityKey={authorityKey} onRejected={onRejected} onOpenTrace={() => onOpenTab("trace")} />
      </Card>
    );

  const startLine = async () => {
    setLineBusy("start");
    setLineError(null);
    onPickingChange(false);
    const { from, to } = linePoints(spec);
    try {
      const result = await api.simulate(appId, authorityKey, {
        input_id: inputId,
        mode: "line",
        from_point: from,
        to_point: to,
        clusters: LINE_CLUSTERS,
        count: LINE_COUNT,
        radius_m: lineRadius(spec),
        duration_s: LINE_DURATION_S,
      });
      const t = Date.now();
      setNow(t);
      setLine({ endsAt: t + (result.duration_s ?? LINE_DURATION_S) * 1000, input: inputId, result });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onRejected();
      setLineError(
        e instanceof ApiError && e.status === 422 && /mode|from_point|to_point|clusters|duration_s/i.test(e.message)
          ? `This backend can't run a full simulation yet (${e.message}).`
          : failure(e),
      );
    } finally {
      setLineBusy(null);
    }
  };

  const stopLine = async () => {
    setLineBusy("stop");
    setLineError(null);
    try {
      await api.stopSimulate(appId, authorityKey);
      setLine(null);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onRejected();
      if (e instanceof ApiError && (e.status === 404 || e.status === 405)) {
        setLine(null); // nothing running on the server, or no stop route: nothing left to stop here
        return;
      }
      setLineError(`Couldn't stop it: ${failure(e)}`);
    } finally {
      setLineBusy(null);
    }
  };

  const outside = !inArea(spec, point.lat, point.lon);
  const send = async (mode: SimulateMode) => {
    setBusy(mode);
    setOutcome(null);
    onPickingChange(false);
    try {
      const result = await api.simulate(appId, authorityKey, {
        input_id: inputId,
        count: clampInt(count, 1, 200),
        lat: Number(point.lat.toFixed(6)),
        lon: Number(point.lon.toFixed(6)),
        radius_m: clampInt(point.radius_m, 10, 5000),
        mode,
      });
      setOutcome({ mode, input: inputId, result });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onRejected();
      setOutcome({ error: failure(e) });
    } finally {
      setBusy(null);
    }
  };

  const label = "text-[12px] font-semibold text-ink";
  const field =
    "phone-16 w-full rounded-lg border border-paleWater bg-white/85 px-2.5 py-1.5 text-[13px] text-slate focus:border-mutedTeal focus:outline-none focus-visible:ring-2 focus-visible:ring-mutedTeal/40 max-sm:min-h-11";

  const left = line ? Math.max(0, Math.ceil((line.endsAt - now) / 1000)) : 0;

  return (
    <Card>
      <div className="space-y-2.5 rounded-xl bg-white/55 px-4 py-3.5 ring-1 ring-paleWater/80">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          {line ? (
            <>
              <span role="status" className="inline-flex items-center gap-2.5 text-[14px] font-bold text-slate">
                <span aria-hidden className="wp-dot is-live" />
                Simulating · <span className="font-mono tabular-nums">{mmss(left)}</span> left
              </span>
              <button
                type="button"
                disabled={lineBusy === "stop"}
                onClick={() => void stopLine()}
                className="tap wp-press inline-flex items-center gap-1.5 rounded-full bg-white/85 px-3.5 py-1.5 text-[13px] font-semibold text-slate ring-1 ring-paleWater transition-colors duration-150 hover:bg-white disabled:cursor-not-allowed disabled:opacity-45"
              >
                <Square aria-hidden className="h-3.5 w-3.5" />
                {lineBusy === "stop" ? "Stopping…" : "Stop"}
              </button>
            </>
          ) : (
            <button
              type="button"
              disabled={!!lineBusy || !!busy || !inputId}
              onClick={() => void startLine()}
              className="tap wp-press inline-flex items-center gap-2 rounded-full bg-deep px-5 py-2.5 text-[14px] font-bold text-cream shadow-[0_4px_14px_rgba(43,86,112,0.28)] transition-[background-color,opacity] duration-150 hover:bg-primaryHover disabled:cursor-not-allowed disabled:opacity-45"
            >
              <Route aria-hidden className="h-4 w-4" />
              {lineBusy === "start" ? "Starting…" : "Run full simulation"}
            </button>
          )}
        </div>
        <p className="text-[12.5px] text-ink">
          {line ? (
            <>
              {line.result.accepted} reports in the first wave on <span className="font-mono">{line.input}</span>
              {line.result.rejected > 0 && <span className="text-danger">, {line.result.rejected} rejected</span>}. New waves arrive about every 10 s
              along the dashed line, each cluster from its own ids (<span className="whitespace-nowrap font-mono">{line.result.user_id_prefix}*</span>).
            </>
          ) : (
            <>
              {LINE_CLUSTERS} clusters of reports along a line across the area, sent to <span className="font-mono">{inputId}</span> about every 10 s
              for {LINE_DURATION_S / 60} minutes. Each cluster has its own ids, so the Critic can trust them differently.
            </>
          )}
        </p>
        {lineError && (
          <p role="alert" className="rounded-lg bg-coral/15 px-3 py-1.5 text-[13px] text-danger">
            {lineError}
          </p>
        )}
        <AgentRunRow appId={appId} authorityKey={authorityKey} onRejected={onRejected} onOpenTrace={() => onOpenTab("trace")} />
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-[minmax(0,1.4fr)_minmax(0,0.7fr)_minmax(0,0.8fr)]">
        <label className="col-span-2 space-y-1 sm:col-span-1">
          <span className={label}>Push input</span>
          <select value={inputId} onChange={(e) => setInputId(e.target.value)} className={`${field} font-mono`}>
            {push.map((i) => (
              <option key={i.id} value={i.id}>
                {i.id}
              </option>
            ))}
          </select>
        </label>
        <label className="space-y-1">
          <span className={label}>Events</span>
          <input type="number" min={1} max={200} value={count} onChange={(e) => setCount(Number(e.target.value))} onBlur={() => setCount((c) => clampInt(c, 1, 200))} className={`${field} font-mono tabular-nums`} />
        </label>
        <label className="space-y-1">
          <span className={label}>Radius (m)</span>
          <input
            type="number"
            min={10}
            max={5000}
            step={10}
            value={point.radius_m}
            onChange={(e) => onPointChange({ ...point, radius_m: Number(e.target.value) })}
            onBlur={() => onPointChange({ ...point, radius_m: clampInt(point.radius_m, 10, 5000) })}
            className={`${field} font-mono tabular-nums`}
          />
        </label>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-[13px]">
        <MapPin aria-hidden className="h-4 w-4 shrink-0 text-coral" />
        <span className="font-mono text-[12.5px] text-slate">
          {point.lat.toFixed(5)}, {point.lon.toFixed(5)}
        </span>
        <button
          type="button"
          aria-pressed={picking}
          onClick={() => onPickingChange(!picking)}
          className={`tap inline-flex items-center gap-1 rounded-full px-3 py-1 text-[12.5px] font-semibold ring-1 transition-colors duration-150 ${
            picking ? "bg-slate text-cream ring-slate" : "bg-white/75 text-slate ring-paleWater hover:bg-white"
          }`}
        >
          <Crosshair aria-hidden className="h-3.5 w-3.5" />
          {picking ? "Click the map…" : "Pick on the map"}
        </button>
        <button
          type="button"
          onClick={() => onPointChange({ ...point, ...centreOf(spec) })}
          className="tap rounded-full px-3 py-1 text-[12.5px] font-semibold text-ink transition-colors duration-150 hover:bg-white/60 hover:text-slate"
        >
          Use map centre
        </button>
      </div>
      {outside && <p className="text-[12.5px] font-semibold text-danger">That point is outside the app's area. Pick one inside the dashed box.</p>}

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          disabled={!!busy || outside || !inputId}
          onClick={() => void send("normal")}
          className="tap inline-flex items-center gap-1.5 rounded-full bg-primary px-4 py-2 text-[13px] font-semibold text-cream shadow-[0_4px_14px_rgba(59,104,149,0.25)] transition-[transform,background-color,opacity] duration-150 hover:-translate-y-px hover:bg-primaryHover disabled:translate-y-0 disabled:cursor-not-allowed disabled:opacity-45"
        >
          <FlaskConical aria-hidden className="h-4 w-4" />
          {busy === "normal" ? "Sending…" : "Simulate reports"}
        </button>
        <button
          type="button"
          disabled={!!busy || outside || !inputId}
          onClick={() => void send("spoof")}
          className="tap inline-flex items-center gap-1.5 rounded-full bg-white/85 px-4 py-2 text-[13px] font-semibold text-danger ring-1 ring-coral/70 transition-[transform,background-color,opacity] duration-150 hover:-translate-y-px hover:bg-white disabled:translate-y-0 disabled:cursor-not-allowed disabled:opacity-45"
        >
          <ShieldAlert aria-hidden className="h-4 w-4" />
          {busy === "spoof" ? "Sending…" : "Simulate a spoof attack"}
        </button>
      </div>
      <p className="text-[12px] text-ink">
        Reports scatter {clampInt(count, 1, 200)} events from different test ids within the radius. A spoof attack sends them all from brand-new ids at exactly one point, within seconds.
      </p>
      <p className="text-[12px] text-ink">
        For the full spoof check, simulate normal reports first, wait a minute, then run the spoof attack{count < 15 ? " with at least 15 events" : ""}.
      </p>

      <div aria-live="polite">
        {outcome && "error" in outcome && (
          <p role="alert" className="rounded-xl bg-coral/15 px-3 py-2 text-[13px] text-danger">
            {outcome.error}
          </p>
        )}
        {outcome && "result" in outcome && (
          <div className="space-y-1.5 rounded-xl bg-white/60 px-3 py-2.5 text-[13px] text-slate ring-1 ring-seaGlass/60">
            <p className="font-semibold">
              {outcome.result.accepted} simulated {outcome.mode === "spoof" ? "spoof events" : "reports"} accepted on <span className="font-mono">{outcome.input}</span>
              {outcome.result.rejected > 0 && <span className="text-danger">, {outcome.result.rejected} rejected</span>}
              <span className="font-normal text-ink">
                {" "}
                · ids <span className="font-mono">{outcome.result.user_id_prefix}*</span>
              </span>
            </p>
            <p className="text-ink">
              {outcome.mode === "spoof"
                ? "The duplicate-coordinate check flags these; the new-id-burst check also fires when the app already had other traffic a minute earlier. The Critic's next run lowers their trust and says why in the Trace."
                : "They show on the map at the next tick (about 2 s). The Critic may react on its next run; its steps are in the Trace."}
            </p>
            <div className="flex flex-wrap gap-x-3">
              <button type="button" onClick={() => onOpenTab("trace")} className="hit relative rounded font-semibold text-primary underline underline-offset-2 hover:text-primaryHover">
                Open the Trace
              </button>
              <button type="button" onClick={() => onOpenTab("sources")} className="hit relative rounded font-semibold text-primary underline underline-offset-2 hover:text-primaryHover">
                See the event count in Sources
              </button>
            </div>
          </div>
        )}
      </div>
    </Card>
  );
}

/** "Run Critic now" / "Run Labeler now": queue a triggered run (contract D). */
function AgentRunRow({ appId, authorityKey, onRejected, onOpenTrace }: { appId: string; authorityKey: string; onRejected: () => void; onOpenTrace: () => void }) {
  const [busy, setBusy] = useState<RunnableAgent | null>(null);
  const [note, setNote] = useState<{ text: string; tone: "ok" | "wait" | "error" } | null>(null);
  const timer = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (timer.current) window.clearTimeout(timer.current);
    },
    [],
  );
  const show = (text: string, tone: "ok" | "wait" | "error", ms: number) => {
    setNote({ text, tone });
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setNote(null), ms);
  };

  const run = async (agent: RunnableAgent) => {
    setBusy(agent);
    try {
      await api.runAgent(appId, authorityKey, agent);
      show(`${AGENT_NAME[agent]} queued. The map flashes what it changes.`, "ok", 5000);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) return onRejected();
      if (e instanceof ApiError && e.status === 429) show(`Cooling down, try again in ${Math.max(1, Math.ceil(e.retryAfterS ?? 15))}s.`, "wait", 5000);
      else if (e instanceof ApiError && e.status === 404 && e.message === "Not Found") show("This backend can't run agents on request yet.", "error", 8000);
      else if (e instanceof ApiError) show(`Not queued: ${e.message}`, "error", 8000);
      else show(`Not queued: ${e instanceof Error ? e.message : String(e)}`, "error", 8000);
    } finally {
      setBusy(null);
    }
  };

  const btn =
    "tap wp-press inline-flex items-center gap-1.5 rounded-full bg-white/85 px-3.5 py-1.5 text-[13px] font-semibold text-slate ring-1 ring-paleWater transition-colors duration-150 hover:bg-white disabled:cursor-not-allowed disabled:opacity-45";
  return (
    <div className="flex flex-wrap items-center gap-2 pt-1">
      <button type="button" disabled={!!busy} onClick={() => void run("critic")} className={btn}>
        <Bot aria-hidden className="h-3.5 w-3.5" />
        {busy === "critic" ? "Queuing…" : "Run Critic now"}
      </button>
      <button type="button" disabled={!!busy} onClick={() => void run("poi_labeler")} className={btn}>
        <Tag aria-hidden className="h-3.5 w-3.5" />
        {busy === "poi_labeler" ? "Queuing…" : "Run Labeler now"}
      </button>
      <span aria-live="polite" className={`text-[12.5px] ${note?.tone === "error" ? "text-danger" : note?.tone === "wait" ? "font-semibold text-attn" : "text-ink"}`}>
        {note?.text}
        {note?.tone === "ok" && (
          <>
            {" "}
            <button type="button" onClick={onOpenTrace} className="hit relative rounded font-semibold text-primary underline underline-offset-2 hover:text-primaryHover">
              Open the Trace
            </button>
          </>
        )}
      </span>
    </div>
  );
}

function Card({ children }: { children: ReactNode }) {
  return (
    <section aria-labelledby="test-title" className="wp-sheet space-y-4 p-5 sm:p-6">
      <div>
        <h2 id="test-title" className="text-[18px] font-bold text-slate">
          Test your app
        </h2>
        <p className="text-[12.5px] text-ink">Send simulated events through the real ingest path. They're marked as simulated in the trace.</p>
      </div>
      {children}
    </section>
  );
}
