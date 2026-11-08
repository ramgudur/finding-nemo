// Settings for a pull option before it becomes a source (ported from Builder.tsx's SourcePicker):
// a feed / endpoint URL for RSS and custom JSON APIs, and the JSON config for json_api (presets included).
import type { SourceOption } from "../../api/types";
import { parseConfig, presetOf, urlRequired } from "./sourceUtils";
import type { ConnectorDraft } from "./sourceUtils";

const field =
  "w-full rounded-xl border bg-white/55 px-3 py-2 font-mono text-[13px] text-slate outline-none transition focus:bg-white/85";

export function ConnectorConfigFields({
  option,
  draft,
  onChange,
}: {
  option: SourceOption;
  draft: ConnectorDraft;
  onChange: (d: ConnectorDraft) => void;
}) {
  const preset = presetOf(option);
  const showUrl = !preset;
  const configBad = option.type === "json_api" && parseConfig(draft.configText) === null;
  const urlMissing = urlRequired(option) && !draft.url.trim();
  return (
    <div className="flex flex-col gap-3">
      {showUrl && (
        <label className="flex flex-col gap-1">
          <span className="text-xs text-ink">
            {option.type === "rss" ? "Feed URL" : "JSON endpoint URL"}
            {option.type === "rss" && <span className="text-inkSoft"> · optional</span>}
          </span>
          <input
            value={draft.url}
            onChange={(e) => onChange({ ...draft, url: e.target.value })}
            placeholder="https://…"
            spellCheck={false}
            className={`${field} ${urlMissing ? "border-coral/50" : "border-white/70"}`}
          />
          {option.type === "rss" && !draft.url.trim() && (
            <span className="text-[11px] text-inkSoft">No feed yet? Leave it empty and the Compiler can ask the Scout to find one.</span>
          )}
        </label>
      )}
      {option.type === "json_api" && (
        <label className="flex flex-col gap-1">
          <span className="text-xs text-ink">
            Connector config (JSON){preset && <span className="font-mono text-inkSoft"> · preset {preset}</span>}
          </span>
          <textarea
            rows={2}
            value={draft.configText}
            onChange={(e) => onChange({ ...draft, configText: e.target.value })}
            spellCheck={false}
            className={`${field} resize-y ${configBad ? "border-coral/60" : "border-white/70"}`}
          />
          <span className={`text-[11px] ${configBad ? "text-coral" : "text-inkSoft"}`}>
            {configBad ? "Must be a JSON object." : "Latitude and longitude default to the area's centre."}
          </span>
        </label>
      )}
    </div>
  );
}
