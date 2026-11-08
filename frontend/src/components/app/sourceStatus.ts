// Plain-language status for one input, from GET /sources/status. Shared by the Sources tab and the
// freshness dot on each layer row, so both always say the same thing.
import type { SourceStatus } from "../../api/types";

export type SourceTone = "fresh" | "quiet" | "waiting" | "empty" | "error";

/** "10 s ago", "3 min ago", "2 h ago", "4 d ago". */
export function ago(iso: string, now: number): string {
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (s < 60) return `${s} s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} d ago`;
}

/** A countdown: "0:42", "2:40", "12 min", "3 h". "now" once it's due. */
export function until(iso: string, now: number): string {
  const s = Math.round((Date.parse(iso) - now) / 1000);
  if (s <= 0) return "now";
  if (s < 600) return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  const m = Math.round(s / 60);
  return m < 120 ? `${m} min` : `${Math.round(m / 60)} h`;
}

const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`;

/** The server's "error: ..." with the prefix dropped; never contains a URL or body (contract B). */
export function errorText(status: string | null): string | null {
  if (!status || !/^error/i.test(status)) return null;
  return status.replace(/^error:?\s*/i, "") || "unknown error";
}

/** A push input counts as fresh within this window of its last event; a pull input within 2 polls. */
const PUSH_FRESH_MS = 5 * 60_000;

export interface SourceLine {
  tone: SourceTone;
  /** One line, e.g. "Polled 3 min ago · 0 items in your area in the last hour". */
  text: string;
  /** Short form for a layer row, e.g. "10 s ago", "no data yet", "poll error". */
  short: string;
}

export function describeSource(s: SourceStatus, now: number): SourceLine {
  const err = errorText(s.last_status);
  if (s.kind === "pull") {
    const next = s.next_poll_at ? until(s.next_poll_at, now) : null;
    if (err) {
      const retry = next ? (next === "now" ? " · retrying now" : ` · retrying in ${next}`) : "";
      return { tone: "error", text: `Error: ${err}${retry}`, short: "poll error" };
    }
    if (!s.last_polled_at)
      return {
        tone: "waiting",
        text: `Waiting for first poll${next ? (next === "now" ? " · due now" : ` · next in ${next}`) : ""}`,
        short: "waiting for poll",
      };
    const items = `${plural(s.events_last_hour, "item")} in your area in the last hour`;
    const nextPart = next ? (next === "now" ? " · polling now" : ` · next in ${next}`) : "";
    const tone: SourceTone = s.events_last_hour > 0 ? "fresh" : "quiet";
    return { tone, text: `Polled ${ago(s.last_polled_at, now)} · ${items}${nextPart}`, short: tone === "fresh" ? `polled ${ago(s.last_polled_at, now)}` : "polled, no items" };
  }
  if (s.events_total === 0 || !s.last_event_at) return { tone: "empty", text: "No events yet", short: "no data yet" };
  const fresh = now - Date.parse(s.last_event_at) < PUSH_FRESH_MS;
  return {
    tone: fresh ? "fresh" : "quiet",
    text: `${plural(s.events_total, "event")} · last ${ago(s.last_event_at, now)}`,
    short: ago(s.last_event_at, now),
  };
}

export const VIA_LABEL: Record<NonNullable<SourceStatus["via"]>, string> = {
  post_api: "POST API",
  webhook: "webhook",
  rss: "RSS feed",
  json_api: "JSON API",
  json_list: "JSON list",
};

/** Dot colour per tone; error text is always written out too, never colour alone. */
export const TONE_DOT: Record<SourceTone, string> = {
  fresh: "bg-mutedTeal",
  quiet: "bg-seaGlass/70",
  waiting: "bg-softStone",
  empty: "bg-inkSoft/45",
  error: "bg-coral",
};
