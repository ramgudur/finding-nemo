// Read-only summary of a push input's webhook mapping: where each value is read from in the sender's JSON.
// Shared by the Builder's review step, the spec review page and the keys page.
import type { WebhookMapping } from "../../api/types";

/** [target, path] pairs in display order: items, lat, lon, time, sender, then each declared field. */
export function webhookMappingParts(w: WebhookMapping): [string, string][] {
  return [
    ["items", w.items_path || "the body"],
    ["lat", w.lat],
    ["lon", w.lon],
    ["time", w.ts ? `${w.ts} (${w.ts_format ?? "iso"})` : "when it arrives"],
    ...(w.user_id ? ([["sender", w.user_id]] as [string, string][]) : []),
    ...Object.entries(w.fields ?? {}),
  ];
}

/** One line per mapped value of a webhook source. `title` defaults to the review wording. */
export function WebhookSummary({ w, title = "Receives another service's webhook" }: { w: WebhookMapping; title?: string }) {
  return (
    <span className="flex flex-col gap-0.5 text-[11px] text-ink">
      <span className="font-semibold text-slate">{title}</span>
      <span className="font-mono break-words">
        {webhookMappingParts(w)
          .map(([k, v]) => `${k} ← ${v}`)
          .join(" · ")}
      </span>
    </span>
  );
}
