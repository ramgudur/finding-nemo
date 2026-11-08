// Settings for a pull option before it becomes a source: a feed / endpoint URL for RSS, custom JSON APIs and
// JSON lists; the JSON config for json_api (presets included); and for a JSON list, how often to fetch it and
// where each item keeps its place, time and fields (the same mapping editor as webhooks).
import type { SourceOption } from "../../api/types";
import { Labeled } from "../builder/Help";
import { WebhookEditor } from "./WebhookEditor";
import { POLL_CHOICES, emptyWebhook, parseConfig, presetOf, urlRequired } from "./sourceUtils";
import type { ConnectorDraft } from "./sourceUtils";

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
  const list = option.type === "json_list";
  const urlId = `url-${option.type}`;
  return (
    <div className="flex flex-col gap-4">
      {showUrl && (
        <Labeled
          label={option.type === "rss" ? "Feed address" : list ? "Web address of the list" : "JSON endpoint address"}
          htmlFor={urlId}
          optional={option.type === "rss"}
          topic="this address"
          help={
            list
              ? "An address that returns JSON: a plain list of items, an object holding a list, or a GeoJSON FeatureCollection. The platform fetches it on the schedule below; each item becomes one point on the map."
              : option.type === "rss"
                ? "An RSS or Atom feed. Each article becomes a document the platform places on the map."
                : "An address that returns JSON. The platform fetches it on a schedule."
          }
        >
          <input
            id={urlId}
            value={draft.url}
            onChange={(e) => onChange({ ...draft, url: e.target.value })}
            placeholder="e.g. https://example.org/feed.json"
            spellCheck={false}
            inputMode="url"
            aria-invalid={urlMissing}
            className="bld-field is-mono w-full"
          />
          {option.type === "rss" && !draft.url.trim() && (
            <span className="bld-hint">No feed yet? Leave it empty and the Compiler can ask the Scout to find one.</span>
          )}
        </Labeled>
      )}
      {list && (
        <>
          <Labeled label="How often to fetch it" htmlFor="poll-s">
            <select
              id="poll-s"
              value={draft.pollS ?? 300}
              onChange={(e) => onChange({ ...draft, pollS: Number(e.target.value) })}
              className="bld-field is-sm w-fit"
            >
              {POLL_CHOICES.map((c) => (
                <option key={c.s} value={c.s}>
                  {c.label}
                </option>
              ))}
            </select>
          </Labeled>
          <div className="flex flex-col gap-1.5">
            <span className="bld-label">What each item holds</span>
            <WebhookEditor
              sourceId=""
              variant="list"
              mapping={draft.mapping ?? emptyWebhook()}
              fields={[]}
              onChange={(mapping) => onChange({ ...draft, mapping })}
              sample={draft.sample ?? ""}
              onSample={(sample) => onChange({ ...draft, sample })}
              fetchUrl={draft.url}
            />
            <span className="bld-hint">Extra values per item (a level, a note) can be added on the source once it's added.</span>
          </div>
        </>
      )}
      {option.type === "json_api" && (
        <Labeled
          label={
            <>
              Connector settings (JSON){preset && <span className="font-mono font-normal text-ink"> · ready-made: {preset}</span>}
            </>
          }
          htmlFor="json-config"
          topic="connector settings"
          help="Values the ready-made connector needs, written as a JSON object. Latitude and longitude start at the centre of your area."
        >
          <textarea
            id="json-config"
            rows={2}
            value={draft.configText}
            onChange={(e) => onChange({ ...draft, configText: e.target.value })}
            spellCheck={false}
            aria-invalid={configBad}
            className="bld-field is-mono w-full resize-y"
          />
          <span className={`bld-hint ${configBad ? "text-danger" : ""}`}>
            {configBad ? 'Must be a JSON object, like {"latitude": 1.5}.' : "Latitude and longitude default to the area's centre."}
          </span>
        </Labeled>
      )}
    </div>
  );
}
