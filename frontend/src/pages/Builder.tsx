// App builder: describe the app and its inputs -> the Compiler proposes a spec -> review -> confirm.
// Confirming activates the app and shows its keys once. Domain-neutral copy: the dev's words go in the form.
import L from "leaflet";
import { useCallback, useEffect, useRef, useState } from "react";
import { saveKey } from "../api/authorityKey";
import { ApiError, api } from "../api/client";
import { MAX_GRID_CELLS } from "../api/types";
import type {
  ActivateResponse,
  AppSpec,
  BBox,
  CompileInput,
  ConnectorConfig,
  DestinationPoint,
  FieldType,
  InputField,
  InputKind,
  InputShape,
  InputSpec,
  OperatorConfig,
  Outputs,
  Plausibility,
  PoiLabel,
  PoiLabelConfig,
  PoiLabelMode,
  SourceOption,
  SourceOptionType,
  SourceSuggestion,
  Tier,
  Visibility,
} from "../api/types";

const KINDS: InputKind[] = ["push", "pull"];
const SHAPES: InputShape[] = ["point", "track", "fixed_point", "field", "document"];
const FIELD_TYPES: FieldType[] = ["number", "integer", "string", "boolean"];
const SLUG = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const M_PER_DEG = (6_371_008.8 * Math.PI) / 180; // same Earth radius as engine/grid.py and GridSpec

/** Grid size in cells for a bbox at cell_m, computed like GridSpec.shape() so client and server agree. */
function gridCells(b: BBox, cellM: number): [number, number] {
  const midLat = ((b.min_lat + b.max_lat) / 2) * (Math.PI / 180);
  const h = ((b.max_lat - b.min_lat) * M_PER_DEG) / cellM;
  const w = ((b.max_lon - b.min_lon) * M_PER_DEG * Math.cos(midLat)) / cellM;
  return [Math.max(1, Math.ceil(h - 1e-9)), Math.max(1, Math.ceil(w - 1e-9))];
}

type Draft = {
  id: string;
  kind: InputKind;
  shape: InputShape;
  meaning: string;
  fields: string;
  // "Add a source": the suggestion for this input's description and the option the dev picked
  suggestion: SourceSuggestion | null;
  suggesting: boolean;
  suggestError: string;
  source: SourceOption | null;
  url: string; // rss / custom json_api
  connConfig: string; // json_api config as JSON, e.g. preset placeholders
};
const emptyInput = (): Draft => ({
  id: "",
  kind: "push",
  shape: "point",
  meaning: "",
  fields: "",
  suggestion: null,
  suggesting: false,
  suggestError: "",
  source: null,
  url: "",
  connConfig: "",
});

const OPTION_KIND: Record<SourceOptionType, InputKind> = { post_api: "push", webhook: "push", rss: "pull", json_api: "pull" };
const optionKey = (o: SourceOption) => `${o.type}:${String(o.config?.preset ?? "")}`;
const presetOf = (o: SourceOption | null) => (typeof o?.config?.preset === "string" ? o.config.preset : null);
const needsUrl = (d: Draft) => d.kind === "pull" && !!d.source && OPTION_KIND[d.source.type] === "pull" && !presetOf(d.source);

function parseConfig(text: string): Record<string, unknown> | null {
  if (!text.trim()) return {};
  try {
    const v: unknown = JSON.parse(text);
    return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/** The pull connector the dev chose for this input, or undefined (push, or no source picked). */
function connectorOf(d: Draft): ConnectorConfig | undefined {
  const s = d.source;
  if (d.kind !== "pull" || !s || OPTION_KIND[s.type] !== "pull") return undefined;
  const type = s.type === "rss" ? "rss" : "json_api";
  const preset = presetOf(s);
  if (!preset && !d.url.trim()) return undefined; // no URL yet: the Compiler may ask the Scout for one
  return {
    type,
    url: preset ? null : d.url.trim(),
    preset,
    config: type === "json_api" ? parseConfig(d.connConfig) ?? {} : {},
  };
}

/** "name:type, other:type?" -> InputField[] ("?" marks optional). */
function parseFields(text: string): InputField[] {
  return text
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => {
      const [rawName = "", rawType = "string"] = s.split(":").map((x) => x.trim());
      const optional = rawType.endsWith("?") || rawName.endsWith("?");
      const type = rawType.replace("?", "") as FieldType;
      return {
        name: rawName.replace("?", ""),
        type: FIELD_TYPES.includes(type) ? type : "string",
        required: !optional,
      };
    });
}

/** Client-side mirror of validate_spec's safeguard warnings that matter at review time. */
function warnings(spec: AppSpec): string[] {
  const out: string[] = [];
  for (const i of spec.inputs ?? []) {
    if ((i.shape === "point" || i.shape === "track") && i.tier === "flexible" && !i.plausibility) {
      out.push(`${i.id}: no plausibility limits, so spoof checks use neutral defaults (no speed limit).`);
    }
  }
  const layers = spec.outputs?.layers ?? [];
  const score = layers.find((l) => l.id === "score");
  if (score?.visibility === "public" && layers.some((l) => l.id !== "score" && l.visibility !== "public")) {
    out.push("score is public while some layers are private.");
  }
  return out;
}

const box = { border: "1px solid #ddd", borderRadius: 6, padding: "10px 12px" } as const;
const muted = { color: "#666" } as const;

const round6 = (v: number) => Math.round(v * 1e6) / 1e6;

function AreaPicker({
  bbox,
  onChange,
  points,
  onPick,
}: {
  bbox: BBox | null;
  onChange: (b: BBox) => void;
  points: DestinationPoint[];
  onPick: ((lat: number, lon: number) => void) | null; // set while the dev is placing destinations
}) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const rect = useRef<L.Rectangle | null>(null);
  const marks = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!el.current) return;
    const m = L.map(el.current).setView([20, 0], 2);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(m);
    map.current = m;
    marks.current = L.layerGroup().addTo(m);
    return () => {
      m.remove();
      map.current = null;
    };
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    rect.current?.remove();
    rect.current = bbox
      ? L.rectangle(
          [
            [bbox.min_lat, bbox.min_lon],
            [bbox.max_lat, bbox.max_lon],
          ],
          { color: "#0b6bcb", weight: 2, fill: false },
        ).addTo(m)
      : null;
  }, [bbox]);

  useEffect(() => {
    const g = marks.current;
    if (!g) return;
    g.clearLayers();
    for (const p of points) {
      L.circleMarker([p.lat, p.lon], { radius: 7, color: "#fff", weight: 2, fillColor: "#1a7f37", fillOpacity: 1 })
        .bindTooltip(p.label || "(no label yet)", { permanent: true, direction: "right" })
        .addTo(g);
    }
  }, [points]);

  useEffect(() => {
    const m = map.current;
    if (!m || !onPick) return;
    const click = (e: L.LeafletMouseEvent) => onPick(round6(e.latlng.lat), round6(e.latlng.lng));
    m.on("click", click);
    m.getContainer().style.cursor = "crosshair";
    return () => {
      m.off("click", click);
      m.getContainer().style.cursor = "";
    };
  }, [onPick]);

  const useView = () => {
    const b = map.current?.getBounds();
    if (!b) return;
    const r = round6;
    onChange({ min_lat: r(b.getSouth()), min_lon: r(b.getWest()), max_lat: r(b.getNorth()), max_lon: r(b.getEast()) });
  };

  return (
    <div style={{ display: "grid", gap: 6 }}>
      <div ref={el} style={{ height: 280, width: "100%", borderRadius: 6 }} />
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <button type="button" onClick={useView}>
          Use this view as the area
        </button>
        <span style={muted}>
          {bbox
            ? `${bbox.min_lat}, ${bbox.min_lon} to ${bbox.max_lat}, ${bbox.max_lon}`
            : "Pan and zoom to the area, then use the view."}
        </span>
      </div>
    </div>
  );
}

/** "Add a source": the suggested kind/shape and ranked options for one input, and the picked option's settings. */
function SourcePicker({
  draft,
  appId,
  group,
  onPick,
  onChange,
}: {
  draft: Draft;
  appId: string;
  group: string; // radio group name, unique per input
  onPick: (o: SourceOption) => void;
  onChange: (patch: Partial<Draft>) => void;
}) {
  const s = draft.suggestion;
  if (!s) return null;
  const unranked = new Set(s.options.map((o) => OPTION_KIND[o.type])).size > 1;
  const picked = draft.source ? optionKey(draft.source) : "";
  const endpoint = `/v1/apps/${appId || "<app>"}/inputs/${draft.id || "<input id>"}/events`;
  const pickedKindMismatch = draft.source && OPTION_KIND[draft.source.type] !== draft.kind;
  return (
    <div style={{ ...box, display: "grid", gap: 6, background: "#f7f9fc" }}>
      <div>
        {unranked ? (
          <span style={{ color: "#8a5a00" }}>
            The model couldn't rank these, so every option is listed. Pick one and set kind and shape yourself.
          </span>
        ) : (
          <span>
            Looks like a <strong>{s.kind}</strong> source ({s.kind === "push" ? "it sends to us" : "we fetch it"}), one
            item is a <strong>{s.shape}</strong>. Override kind or shape above if that's wrong.
          </span>
        )}
      </div>
      {s.options.map((o) => (
        <label key={optionKey(o)} style={{ display: "flex", gap: 6, alignItems: "baseline" }}>
          <input type="radio" name={group} checked={picked === optionKey(o)} onChange={() => onPick(o)} />
          <span>
            <strong>{o.label}</strong> <span style={muted}>({OPTION_KIND[o.type]})</span> — {o.why}
          </span>
        </label>
      ))}
      {(unranked || s.kind === "push") && (
        <label style={{ display: "flex", gap: 6, alignItems: "baseline", color: "#999" }}>
          <input type="radio" disabled />
          <span>SMS / phone (later)</span>
        </label>
      )}
      {draft.source && pickedKindMismatch && (
        <span style={{ color: "#8a5a00" }}>
          The picked option is a {OPTION_KIND[draft.source.type]} source but kind is {draft.kind}; it won't be used.
        </span>
      )}
      {draft.source && !pickedKindMismatch && OPTION_KIND[draft.source.type] === "push" && (
        <span style={muted}>
          {draft.source.type === "webhook" ? "Webhook URL" : "Endpoint"}: <code>POST {endpoint}</code> with header{" "}
          <code>X-Ingest-Key</code> (the key is shown once when you confirm).
        </span>
      )}
      {draft.source && !pickedKindMismatch && needsUrl(draft) && (
        <input
          aria-label="source URL"
          placeholder={draft.source.type === "rss" ? "feed URL (https://...)" : "JSON endpoint URL (https://...)"}
          value={draft.url}
          onChange={(e) => onChange({ url: e.target.value })}
        />
      )}
      {draft.source && !pickedKindMismatch && draft.source.type === "json_api" && (
        <label style={{ display: "grid", gap: 4 }}>
          <span style={muted}>
            Connector config (JSON){presetOf(draft.source) ? ` for preset ${presetOf(draft.source)}` : ""}; latitude and
            longitude default to the area's center.
          </span>
          <input aria-label="connector config" value={draft.connConfig} onChange={(e) => onChange({ connConfig: e.target.value })} />
        </label>
      )}
      {draft.source?.type === "rss" && !pickedKindMismatch && !draft.url.trim() && (
        <span style={muted}>No feed yet? Leave the URL empty and the Compiler can ask the Scout to find one.</span>
      )}
    </div>
  );
}

/** The platform's registered operators and the input shapes each accepts (engine/operators/registry.py). */
const OPERATOR_SHAPES: Record<string, InputShape[]> = {
  flow_source: ["track"],
  density: ["point", "track"],
  dwell: ["track"],
  field: ["field"],
  event_points: ["document"],
  override: ["fixed_point"],
};
const TIERS: Tier[] = ["locked", "stiff", "flexible"];
const VISIBILITIES: Visibility[] = ["authority", "public"];
const LABEL_MODES: PoiLabelMode[] = ["auto", "on", "off"];

const layerIdOf = (inputId: string, o: OperatorConfig) => o.layer_id || `${inputId}.${o.op}`;
const numOrNull = (v: string) => (v.trim() === "" || !Number.isFinite(Number(v)) ? null : Number(v));

/** Plausibility with empty values dropped; null when nothing is set (the platform's neutral defaults). */
function cleanPlausibility(p: Plausibility): Plausibility | null {
  const out: Plausibility = {};
  if (p.max_speed_mps != null) out.max_speed_mps = p.max_speed_mps;
  if (p.jitter_m != null) out.jitter_m = p.jitter_m;
  if (p.flag_constant_speed) out.flag_constant_speed = true;
  if (p.flag_straight_line) out.flag_straight_line = true;
  return Object.keys(out).length ? out : null;
}

/** Review the proposed spec and edit it; every edit goes through onChange (saved with PUT by the parent). */
function Review({ spec, onChange }: { spec: AppSpec; onChange: (s: AppSpec) => void }) {
  const w = warnings(spec);
  const inputs = spec.inputs ?? [];
  const outputs = spec.outputs ?? {};
  const outLayers = outputs.layers ?? [];
  const labels = outputs.poi_labels ?? {};
  const labelSet = labels.label_set ?? [];
  const routing = spec.routing ?? {};

  const setInput = (k: number, patch: Partial<InputSpec>) =>
    onChange({ ...spec, inputs: inputs.map((x, j) => (j === k ? { ...x, ...patch } : x)) });
  const setOutputs = (patch: Partial<Outputs>) => onChange({ ...spec, outputs: { ...outputs, ...patch } });
  const setLabels = (patch: Partial<PoiLabelConfig>) => setOutputs({ poi_labels: { ...labels, ...patch } });
  const setLabel = (k: number, patch: Partial<PoiLabel>) =>
    setLabels({ label_set: labelSet.map((x, j) => (j === k ? { ...x, ...patch } : x)) });

  /** Replace one input's operators, keeping outputs.layers pointing only at layers that still exist. */
  const setOperators = (k: number, ops: OperatorConfig[], renamed?: [string, string]) => {
    const i = inputs[k];
    if (!i) return;
    const nextInputs = inputs.map((x, j) => (j === k ? { ...x, operators: ops } : x));
    const known = new Set(["score", ...nextInputs.flatMap((x) => (x.operators ?? []).map((o) => layerIdOf(x.id, o)))]);
    const nextLayers = outLayers
      .map((l) => (renamed && l.id === renamed[0] ? { ...l, id: renamed[1] } : l))
      .filter((l) => known.has(l.id));
    onChange({ ...spec, inputs: nextInputs, outputs: { ...outputs, layers: nextLayers } });
  };

  const opChoices = (shape: InputShape) => Object.keys(OPERATOR_SHAPES).filter((n) => OPERATOR_SHAPES[n]?.includes(shape));

  return (
    <div style={{ display: "grid", gap: 10 }}>
      {w.length > 0 && (
        <div style={{ ...box, borderColor: "#c77700", background: "#fff4e5" }}>
          <strong>Check before confirming</strong>
          <ul style={{ margin: "4px 0 0", paddingLeft: 20 }}>
            {w.map((x) => (
              <li key={x}>{x}</li>
            ))}
          </ul>
        </div>
      )}
      {inputs.map((i, k) => {
        const ops = i.operators ?? [];
        const choices = opChoices(i.shape);
        const unused = choices.filter((n) => !ops.some((o) => o.op === n));
        const p = i.plausibility ?? {};
        const setP = (patch: Partial<Plausibility>) => setInput(k, { plausibility: cleanPlausibility({ ...p, ...patch }) });
        return (
          <div key={i.id} style={{ ...box, display: "grid", gap: 6 }}>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "baseline" }}>
              <strong>{i.id}</strong>
              <span style={muted}>
                {i.kind} · {i.shape}
              </span>
              <label>
                tier{" "}
                <select aria-label={`${i.id} tier`} value={i.tier} onChange={(e) => setInput(k, { tier: e.target.value as Tier })}>
                  {TIERS.map((x) => (
                    <option key={x}>{x}</option>
                  ))}
                </select>
              </label>
              <label>
                visibility{" "}
                <select
                  aria-label={`${i.id} visibility`}
                  value={i.visibility ?? "authority"}
                  onChange={(e) => setInput(k, { visibility: e.target.value as Visibility })}
                >
                  {VISIBILITIES.map((x) => (
                    <option key={x}>{x}</option>
                  ))}
                </select>
              </label>
            </div>
            <div style={{ ...muted, fontStyle: "italic" }}>“{i.meaning}”</div>
            <div style={{ display: "grid", gap: 4 }}>
              <span>operators</span>
              {ops.length === 0 && <span style={muted}>none</span>}
              {ops.map((o, n) => (
                <div key={layerIdOf(i.id, o)} style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
                  <select
                    aria-label={`${i.id} operator`}
                    value={o.op}
                    onChange={(e) => {
                      const op = e.target.value;
                      const layer_id = `${i.id}.${op}`;
                      // a different operator takes its own default params; keep weight and noise floor
                      const next: OperatorConfig = { op, weight: o.weight, noise_floor: o.noise_floor ?? null, layer_id };
                      setOperators(k, ops.map((x, j) => (j === n ? next : x)), [layerIdOf(i.id, o), layer_id]);
                    }}
                  >
                    {[o.op, ...choices.filter((c) => c !== o.op && !ops.some((x) => x.op === c))].map((c) => (
                      <option key={c}>{c}</option>
                    ))}
                  </select>
                  <label>
                    weight{" "}
                    <input
                      aria-label={`${i.id} ${o.op} weight`}
                      type="number"
                      step="0.05"
                      min={0}
                      value={o.weight}
                      onChange={(e) =>
                        setOperators(k, ops.map((x, j) => (j === n ? { ...x, weight: Number(e.target.value) } : x)))
                      }
                      style={{ width: 80 }}
                    />
                  </label>
                  <button type="button" onClick={() => setOperators(k, ops.filter((_, j) => j !== n))}>
                    Remove
                  </button>
                </div>
              ))}
              {unused.length > 0 && (
                <select
                  aria-label={`${i.id} add operator`}
                  value=""
                  onChange={(e) => {
                    const op = e.target.value;
                    if (op) setOperators(k, [...ops, { op, weight: 0.5, layer_id: `${i.id}.${op}` }]);
                  }}
                  style={{ justifySelf: "start" }}
                >
                  <option value="">add an operator…</option>
                  {unused.map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
              )}
            </div>
            {(i.shape === "point" || i.shape === "track") && (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                <span>plausibility</span>
                <label>
                  max speed (m/s){" "}
                  <input
                    aria-label={`${i.id} max speed`}
                    type="number"
                    min={0}
                    value={p.max_speed_mps ?? ""}
                    placeholder="none"
                    onChange={(e) => setP({ max_speed_mps: numOrNull(e.target.value) })}
                    style={{ width: 80 }}
                  />
                </label>
                <label>
                  jitter (m){" "}
                  <input
                    aria-label={`${i.id} jitter`}
                    type="number"
                    min={0}
                    value={p.jitter_m ?? ""}
                    placeholder="none"
                    onChange={(e) => setP({ jitter_m: numOrNull(e.target.value) })}
                    style={{ width: 80 }}
                  />
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={!!p.flag_constant_speed}
                    onChange={(e) => setP({ flag_constant_speed: e.target.checked })}
                  />{" "}
                  flag constant speed
                </label>
                <label>
                  <input
                    type="checkbox"
                    checked={!!p.flag_straight_line}
                    onChange={(e) => setP({ flag_straight_line: e.target.checked })}
                  />{" "}
                  flag straight lines
                </label>
              </div>
            )}
            {i.connector && (
              <div>
                connector: {i.connector.type}
                {i.connector.preset ? ` (${i.connector.preset})` : ""} {i.connector.url ?? ""}
              </div>
            )}
          </div>
        );
      })}
      <div style={box}>
        <strong>Verification</strong>{" "}
        {(spec.verification ?? [])
          .slice()
          .sort((a, b) => a.priority - b.priority)
          .map((v) => `${v.priority}. ${v.method}`)
          .join("  ")}
      </div>
      <div style={{ ...box, display: "grid", gap: 8 }}>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
          <strong>Output layers</strong>
          {outLayers.length === 0 && <span style={muted}>none</span>}
          {outLayers.map((l, n) => (
            <label key={l.id}>
              {l.id}{" "}
              <select
                aria-label={`${l.id} output visibility`}
                value={l.visibility ?? "authority"}
                onChange={(e) =>
                  setOutputs({
                    layers: outLayers.map((x, j) => (j === n ? { ...x, visibility: e.target.value as Visibility } : x)),
                  })
                }
              >
                {VISIBILITIES.map((x) => (
                  <option key={x}>{x}</option>
                ))}
              </select>
            </label>
          ))}
        </div>
        <div style={{ display: "grid", gap: 4 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <strong>Labels</strong>
            <select
              aria-label="label mode"
              value={labels.mode ?? "auto"}
              onChange={(e) => setLabels({ mode: e.target.value as PoiLabelMode })}
            >
              {LABEL_MODES.map((x) => (
                <option key={x}>{x}</option>
              ))}
            </select>
          </div>
          {labelSet.length === 0 && <span style={muted}>none</span>}
          {labelSet.map((l, n) => (
            <div key={n} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <input aria-label="label" value={l.label} onChange={(e) => setLabel(n, { label: e.target.value })} />
              <label>
                <input
                  type="checkbox"
                  checked={!!l.sensitive}
                  onChange={(e) => setLabel(n, { sensitive: e.target.checked, ...(e.target.checked ? { destination: false } : {}) })}
                />{" "}
                sensitive (authority only)
              </label>
              <label>
                <input
                  type="checkbox"
                  checked={!!l.destination}
                  onChange={(e) => setLabel(n, { destination: e.target.checked, ...(e.target.checked ? { sensitive: false } : {}) })}
                />{" "}
                destination (public routing target)
              </label>
              <button type="button" onClick={() => setLabels({ label_set: labelSet.filter((_, j) => j !== n) })}>
                Remove
              </button>
            </div>
          ))}
          <button
            type="button"
            style={{ justifySelf: "start" }}
            onClick={() => setLabels({ label_set: [...labelSet, { label: "", sensitive: false, destination: false }] })}
          >
            Add a label
          </button>
        </div>
        <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <input
            type="checkbox"
            checked={!!routing.enabled}
            onChange={(e) => onChange({ ...spec, routing: { ...routing, enabled: e.target.checked } })}
          />
          <strong>Routing</strong>
          <span style={muted}>{`${routing.destination_points?.length ?? 0} fixed destinations`}</span>
        </label>
      </div>
      <p style={{ margin: 0, color: "#8a5a00" }}>
        After you confirm, tiers, locked weights and verification methods can't be changed by any agent.
      </p>
    </div>
  );
}

export function Builder() {
  const [appId, setAppId] = useState("");
  const [intent, setIntent] = useState("");
  const [cellM, setCellM] = useState(25);
  const [bbox, setBbox] = useState<BBox | null>(null);
  const [inputs, setInputs] = useState<Draft[]>([emptyInput()]);
  const [points, setPoints] = useState<DestinationPoint[]>([]);
  const [placing, setPlacing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState("");
  const [spec, setSpec] = useState<AppSpec | null>(null); // as last compiled or saved on the server
  const [draft, setDraft] = useState<AppSpec | null>(null); // the dev's edits on the review screen
  const [saving, setSaving] = useState(false);
  const [saveWarnings, setSaveWarnings] = useState<string[] | null>(null);
  const [saveErrors, setSaveErrors] = useState<string[]>([]);
  const dirty = !!spec && !!draft && JSON.stringify(spec) !== JSON.stringify(draft);
  const [keys, setKeys] = useState<ActivateResponse | null>(null);

  useEffect(() => {
    if (!busy) return;
    const t0 = Date.now();
    const id = setInterval(() => setElapsed(Math.round((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(id);
  }, [busy]);

  const dims = bbox && cellM > 0 ? gridCells(bbox, cellM) : null;
  const problems = [
    !SLUG.test(appId) && "App id: lowercase letters, digits, - or _.",
    !intent.trim() && "Describe what the app is for.",
    !bbox && "Pick the area on the map.",
    !(cellM > 0) && "Cell size must be positive.",
    dims &&
      dims[0] * dims[1] > MAX_GRID_CELLS &&
      `The grid would be ${dims[0]} x ${dims[1]} cells; the limit is ${MAX_GRID_CELLS.toLocaleString()}. Use a smaller area or bigger cells.`,
    inputs.some((i) => !SLUG.test(i.id) || !i.meaning.trim()) && "Every input needs an id and a meaning.",
    inputs.some((i) => i.kind === "pull" && i.source?.type === "json_api" && parseConfig(i.connConfig) === null) &&
      "Connector config must be a JSON object.",
    inputs.some((i) => needsUrl(i) && i.source?.type === "json_api" && !i.url.trim()) &&
      "A custom JSON API source needs its URL.",
    points.some((p) => !p.label.trim()) && "Every destination needs a label.",
    bbox &&
      points.some((p) => p.lat < bbox.min_lat || p.lat > bbox.max_lat || p.lon < bbox.min_lon || p.lon > bbox.max_lon) &&
      "Every destination must be inside the area.",
  ].filter(Boolean) as string[];

  const setInput = (k: number, patch: Partial<Draft>) =>
    setInputs((xs) => xs.map((x, j) => (j === k ? { ...x, ...patch } : x)));

  const center = bbox
    ? { latitude: round6((bbox.min_lat + bbox.max_lat) / 2), longitude: round6((bbox.min_lon + bbox.max_lon) / 2) }
    : null;

  const suggest = async (k: number) => {
    const d = inputs[k];
    if (!d) return;
    setInput(k, { suggesting: true, suggestError: "" });
    try {
      const sug = await api.suggestSource(appId, { intent: intent.trim(), description: d.meaning });
      setInput(k, { suggesting: false, suggestion: sug, source: null, url: "", connConfig: "" });
      if (new Set(sug.options.map((o) => OPTION_KIND[o.type])).size === 1) {
        setInput(k, { kind: sug.kind, shape: sug.shape });
      }
    } catch (e) {
      setInput(k, { suggesting: false, suggestError: String(e instanceof ApiError ? e.message : e) });
    }
  };

  const pick = (k: number, o: SourceOption) => {
    const d = inputs[k];
    const sug = d?.suggestion;
    const unranked = sug ? new Set(sug.options.map((x) => OPTION_KIND[x.type])).size > 1 : true;
    const url = typeof o.config?.url === "string" ? o.config.url : "";
    setInput(k, {
      source: o,
      kind: OPTION_KIND[o.type],
      ...(sug && !unranked ? { shape: sug.shape } : {}),
      url,
      connConfig: o.type === "json_api" && center ? JSON.stringify(center) : "",
    });
  };

  const addPoint = useCallback((lat: number, lon: number) => setPoints((xs) => [...xs, { lat, lon, label: "" }]), []);
  const setPoint = (k: number, patch: Partial<DestinationPoint>) =>
    setPoints((xs) => xs.map((x, j) => (j === k ? { ...x, ...patch } : x)));

  const compile = async () => {
    if (!bbox) return;
    setBusy(true);
    setError("");
    setSpec(null);
    setDraft(null);
    try {
      const body = {
        intent: intent.trim(),
        grid: { cell_m: cellM, bbox },
        inputs: inputs.map(
          (i): CompileInput => ({
            id: i.id,
            kind: i.kind,
            shape: i.shape,
            meaning: i.meaning.trim(),
            fields: parseFields(i.fields),
            connector: connectorOf(i),
          }),
        ),
        destination_points: points.map((p) => ({ ...p, label: p.label.trim() })),
      };
      const compiled = await api.compile(appId, body);
      setSpec(compiled);
      setDraft(compiled);
      setSaveWarnings(null);
      setSaveErrors([]);
    } catch (e) {
      const why =
        e instanceof ApiError && e.status === 503
          ? `The model budget is used up right now (${e.message}). Try again in a minute.`
          : e instanceof ApiError && e.status === 409
            ? "This app is already active."
            : e instanceof ApiError && e.status === 422
              ? `The Compiler couldn't produce a valid spec: ${e.message}`
              : String(e);
      setError(why);
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!draft) return;
    setSaving(true);
    setSaveErrors([]);
    try {
      const r = await api.updateSpec(appId, draft);
      setSpec(r.spec);
      setDraft(r.spec);
      setSaveWarnings(r.warnings);
    } catch (e) {
      setSaveWarnings(null);
      if (e instanceof ApiError && e.details.length > 0) setSaveErrors(e.details);
      else if (e instanceof ApiError && e.status === 409) setSaveErrors(["This app is no longer pending confirmation."]);
      else setSaveErrors([String(e instanceof ApiError ? e.message : e)]);
    } finally {
      setSaving(false);
    }
  };

  const confirm = async () => {
    setBusy(true);
    setError("");
    try {
      const k = await api.activate(appId);
      saveKey(appId, k.authority_key, false); // this tab only; the dev keeps their own copy
      setKeys(k);
    } catch (e) {
      setError(String(e instanceof ApiError ? e.message : e));
    } finally {
      setBusy(false);
    }
  };

  if (keys) {
    return (
      <div style={{ display: "grid", gap: 12, maxWidth: 760 }}>
        <h1 style={{ margin: 0, fontSize: 22 }}>{keys.app_id} is active</h1>
        <p style={{ margin: 0, color: "#b00020" }}>
          These keys are shown once. Copy them now; the platform can't show them again.
        </p>
        {(
          [
            ["Ingest key (for your app's event POSTs)", keys.ingest_key],
            ["Authority key (for this console)", keys.authority_key],
          ] as const
        ).map(([label, value]) => (
          <div key={label} style={box}>
            <div style={muted}>{label}</div>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <code style={{ overflowWrap: "anywhere" }}>{value}</code>
              <button type="button" onClick={() => void navigator.clipboard?.writeText(value)}>
                Copy
              </button>
            </div>
          </div>
        ))}
        <a href={`/apps/${encodeURIComponent(keys.app_id)}`}>Open the console</a>
      </div>
    );
  }

  return (
    <div style={{ display: "grid", gap: 14, maxWidth: 760 }}>
      <h1 style={{ margin: 0, fontSize: 22 }}>Build an app</h1>
      <label style={{ display: "grid", gap: 4 }}>
        App id
        <input value={appId} onChange={(e) => setAppId(e.target.value.toLowerCase())} placeholder="my-app" />
      </label>
      <label style={{ display: "grid", gap: 4 }}>
        What should the map tell you? (plain English)
        <textarea value={intent} onChange={(e) => setIntent(e.target.value)} rows={3} />
      </label>
      <fieldset style={box}>
        <legend>Area</legend>
        <AreaPicker bbox={bbox} onChange={setBbox} points={points} onPick={placing ? addPoint : null} />
        <label style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6 }}>
          Cell size (m)
          <input type="number" min={1} value={cellM} onChange={(e) => setCellM(Number(e.target.value))} style={{ width: 90 }} />
          {dims && <span style={muted}>{`grid ${dims[0]} x ${dims[1]} cells`}</span>}
        </label>
      </fieldset>
      <fieldset style={{ ...box, display: "grid", gap: 8 }}>
        <legend>Fixed destinations (optional)</legend>
        <span style={muted}>
          Places users can be routed to, used exactly as you place them. Leave empty if the app doesn't give directions
          or the destinations come from labelled places.
        </span>
        <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <input type="checkbox" checked={placing} onChange={(e) => setPlacing(e.target.checked)} />
          Click the map above to add a destination
        </label>
        {points.map((p, k) => (
          <div key={k} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <input
              aria-label="destination label"
              placeholder="label, e.g. the name of the place"
              value={p.label}
              onChange={(e) => setPoint(k, { label: e.target.value })}
            />
            <span style={muted}>{`${p.lat}, ${p.lon}`}</span>
            <button type="button" onClick={() => setPoints((xs) => xs.filter((_, j) => j !== k))}>
              Remove
            </button>
          </div>
        ))}
      </fieldset>
      <fieldset style={{ ...box, display: "grid", gap: 10 }}>
        <legend>Inputs (sources)</legend>
        {inputs.map((i, k) => (
          <div key={k} style={{ display: "grid", gap: 6, borderBottom: "1px solid #eee", paddingBottom: 10 }}>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <input
                aria-label="input id"
                placeholder="input id"
                value={i.id}
                onChange={(e) => setInput(k, { id: e.target.value.toLowerCase() })}
              />
              <select aria-label="kind" value={i.kind} onChange={(e) => setInput(k, { kind: e.target.value as InputKind })}>
                {KINDS.map((x) => (
                  <option key={x}>{x}</option>
                ))}
              </select>
              <select aria-label="shape" value={i.shape} onChange={(e) => setInput(k, { shape: e.target.value as InputShape })}>
                {SHAPES.map((x) => (
                  <option key={x}>{x}</option>
                ))}
              </select>
              {inputs.length > 1 && (
                <button type="button" onClick={() => setInputs((xs) => xs.filter((_, j) => j !== k))}>
                  Remove
                </button>
              )}
            </div>
            <textarea
              aria-label="meaning"
              rows={2}
              placeholder="Describe the source in plain words: where it comes from and what one item means. Your words go to the Compiler verbatim."
              value={i.meaning}
              onChange={(e) => setInput(k, { meaning: e.target.value })}
            />
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <button
                type="button"
                disabled={i.suggesting || !i.meaning.trim() || !SLUG.test(appId)}
                onClick={() => void suggest(k)}
              >
                {i.suggesting ? "Suggesting…" : i.suggestion ? "Suggest again" : "Suggest how to connect it"}
              </button>
              {!SLUG.test(appId) && <span style={muted}>Set the app id first.</span>}
              {i.suggestError && <span style={{ color: "#b00020" }}>{i.suggestError}</span>}
            </div>
            <SourcePicker
              draft={i}
              appId={appId}
              group={`source-${k}`}
              onPick={(o) => pick(k, o)}
              onChange={(patch) => setInput(k, patch)}
            />
            <input
              aria-label="fields"
              placeholder="extra fields, e.g. level:number, note:string? (? = optional)"
              value={i.fields}
              onChange={(e) => setInput(k, { fields: e.target.value })}
            />
          </div>
        ))}
        <button type="button" onClick={() => setInputs((xs) => [...xs, emptyInput()])} style={{ justifySelf: "start" }}>
          Add a source
        </button>
      </fieldset>
      {problems.length > 0 && <p style={{ ...muted, margin: 0 }}>{problems[0]}</p>}
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <button type="button" disabled={busy || problems.length > 0} onClick={() => void compile()}>
          {spec ? "Compile again" : "Compile"}
        </button>
        {busy && !spec && <span style={muted}>Compiling… {elapsed}s (a run can take tens of seconds)</span>}
      </div>
      {error && <p style={{ color: "#b00020", margin: 0 }}>{error}</p>}
      {spec && (
        <section style={{ display: "grid", gap: 10 }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>Review the proposed spec</h2>
          <Review spec={draft ?? spec} onChange={setDraft} />
          {saveErrors.length > 0 && (
            <div style={{ ...box, borderColor: "#b00020", background: "#fdecee" }}>
              <strong>Not saved: fix these first</strong>
              <ul style={{ margin: "4px 0 0", paddingLeft: 20 }}>
                {saveErrors.map((x) => (
                  <li key={x}>{x}</li>
                ))}
              </ul>
            </div>
          )}
          {saveWarnings && !dirty && (
            <div style={{ ...box, borderColor: saveWarnings.length ? "#c77700" : "#1a7f37" }}>
              {saveWarnings.length === 0 ? (
                "Saved. No warnings."
              ) : (
                <>
                  <strong>Saved, with warnings</strong>
                  <ul style={{ margin: "4px 0 0", paddingLeft: 20 }}>
                    {saveWarnings.map((x) => (
                      <li key={x}>{x}</li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          )}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <button type="button" disabled={busy || saving || !dirty} onClick={() => void save()}>
              {saving ? "Saving…" : "Save changes"}
            </button>
            <button type="button" disabled={busy || saving || !dirty} onClick={() => setDraft(spec)}>
              Discard changes
            </button>
            <button type="button" disabled={busy || saving || dirty} onClick={() => void confirm()}>
              Confirm and activate
            </button>
            <span style={muted}>
              {dirty
                ? "Save your changes before confirming."
                : "Edit above and save, or change the form and compile again to get a different proposal."}
            </span>
          </div>
        </section>
      )}
    </div>
  );
}
