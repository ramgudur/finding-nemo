// Settings for a pull option before it becomes a source. "main" is what it can't be added without: the feed /
// endpoint address (RSS, custom JSON APIs, JSON lists) and a JSON list's item mapping. "more" is what has a
// good default: how often a JSON list is fetched, and a json_api's JSON config (presets start at the area's
// centre). The suggestion card shows "main" inline and "more" under Details.
import type { SourceOption } from "../../api/types";
import { Labeled } from "../builder/Help";
import { WebhookEditor } from "./WebhookEditor";
import { LOCAL_FILE_NAME, POLL_CHOICES, emptyWebhook, parseConfig, presetOf, urlRequired } from "./sourceUtils";
import type { ConnectorDraft } from "./sourceUtils";

function PollSelect({ value, onChange, label }: { value: number; onChange: (s: number) => void; label: string }) {
  return (
    <Labeled label={label} htmlFor="poll-s">
      <select id="poll-s" value={value} onChange={(e) => onChange(Number(e.target.value))} className="bld-field is-sm w-fit">
        {POLL_CHOICES.map((c) => (
          <option key={c.s} value={c.s}>
            {c.label}
          </option>
        ))}
      </select>
    </Labeled>
  );
}

export function ConnectorConfigFields({
  option,
  draft,
  onChange,
  part = "all",
}: {
  option: SourceOption;
  draft: ConnectorDraft;
  onChange: (d: ConnectorDraft) => void;
  part?: "main" | "more" | "all";
}) {
  const main = part !== "more";
  const more = part !== "main";
  const preset = presetOf(option);
  const showUrl = !preset;
  const configBad = option.type === "json_api" && parseConfig(draft.configText) === null;
  const urlMissing = urlRequired(option) && !draft.url.trim();
  const list = option.type === "json_list";
  const file = option.type === "local_file";
  const urlId = `url-${option.type}`;
  if (file) {
    const name = (draft.filename ?? "").trim();
    const bad = !!name && !LOCAL_FILE_NAME.test(name);
    return (
      <div className="flex flex-col gap-4">
        {main && (
          <Labeled label="File name" htmlFor="local-file">
            <input
              id="local-file"
              value={draft.filename ?? ""}
              onChange={(e) => onChange({ ...draft, filename: e.target.value })}
              placeholder="myfeed.txt"
              spellCheck={false}
              aria-invalid={!name || bad}
              className="bld-field is-mono w-full"
            />
            <span className={`bld-hint ${bad ? "text-danger" : ""}`}>
              {bad ? "A bare name ending in .txt: letters, digits, . _ or -, no folders." : "Put the file in backend/data/feeds/. One line per item: lat, lon | text."}
            </span>
          </Labeled>
        )}
        {more && <PollSelect value={draft.pollS ?? 60} onChange={(pollS) => onChange({ ...draft, pollS })} label="How often to read it" />}
      </div>
    );
  }
  if (!(main && (showUrl || list)) && !(more && (list || option.type === "json_api"))) return null;
  return (
    <div className="flex flex-col gap-4">
      {main && showUrl && (
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
            <span className="bld-hint">No feed yet? Leave it empty and we'll look for one.</span>
          )}
        </Labeled>
      )}
      {more && list && <PollSelect value={draft.pollS ?? 300} onChange={(pollS) => onChange({ ...draft, pollS })} label="How often to fetch it" />}
      {main && list && (
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
          </div>
      )}
      {more && option.type === "json_api" && (
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
