// App builder: describe the app and its inputs -> the Compiler proposes a spec -> review -> confirm.
// Confirming activates the app and shows its keys once. Domain-neutral copy: the dev's words go in the form.
import L from "leaflet";
import { useEffect, useRef, useState } from "react";
import { saveKey } from "../api/authorityKey";
import { ApiError, api } from "../api/client";
import { MAX_GRID_CELLS } from "../api/types";
import type {
  ActivateResponse,
  AppSpec,
  BBox,
  CompileInput,
  FieldType,
  InputField,
  InputKind,
  InputShape,
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

type Draft = { id: string; kind: InputKind; shape: InputShape; meaning: string; fields: string };
const emptyInput = (): Draft => ({ id: "", kind: "push", shape: "point", meaning: "", fields: "" });

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

function AreaPicker({ bbox, onChange }: { bbox: BBox | null; onChange: (b: BBox) => void }) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const rect = useRef<L.Rectangle | null>(null);

  useEffect(() => {
    if (!el.current) return;
    const m = L.map(el.current).setView([20, 0], 2);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(m);
    map.current = m;
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

  const useView = () => {
    const b = map.current?.getBounds();
    if (!b) return;
    const r = (v: number) => Math.round(v * 1e6) / 1e6;
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

function Review({ spec }: { spec: AppSpec }) {
  const w = warnings(spec);
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
      {(spec.inputs ?? []).map((i) => (
        <div key={i.id} style={box}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "baseline" }}>
            <strong>{i.id}</strong>
            <span style={muted}>
              {i.kind} · {i.shape}
            </span>
            <span>
              tier <strong>{i.tier}</strong>
            </span>
            <span>
              visibility <strong>{i.visibility ?? "authority"}</strong>
            </span>
          </div>
          <div style={{ ...muted, fontStyle: "italic", margin: "4px 0" }}>“{i.meaning}”</div>
          <div>
            operators:{" "}
            {(i.operators ?? []).map((o) => `${o.op} (weight ${o.weight})`).join(", ") || <span style={muted}>none</span>}
          </div>
          <div>
            plausibility:{" "}
            {i.plausibility ? (
              Object.entries(i.plausibility)
                .filter(([, v]) => v !== null && v !== undefined)
                .map(([k, v]) => `${k}=${String(v)}`)
                .join(", ")
            ) : (
              <span style={muted}>defaults</span>
            )}
          </div>
          {i.connector && (
            <div>
              connector: {i.connector.type}
              {i.connector.preset ? ` (${i.connector.preset})` : ""} {i.connector.url ?? ""}
            </div>
          )}
        </div>
      ))}
      <div style={box}>
        <strong>Verification</strong>{" "}
        {(spec.verification ?? [])
          .slice()
          .sort((a, b) => a.priority - b.priority)
          .map((v) => `${v.priority}. ${v.method}`)
          .join("  ")}
      </div>
      <div style={box}>
        <strong>Output layers</strong>{" "}
        {(spec.outputs?.layers ?? []).map((l) => `${l.id}: ${l.visibility ?? "authority"}`).join(", ")}
        <div>
          <strong>Labels</strong> ({spec.outputs?.poi_labels?.mode ?? "auto"}):{" "}
          {(spec.outputs?.poi_labels?.label_set ?? [])
            .map((l) => `${l.label}${l.sensitive ? " (sensitive)" : ""}${l.destination ? " (destination)" : ""}`)
            .join(", ") || <span style={muted}>none</span>}
        </div>
        <div>
          <strong>Routing</strong>{" "}
          {spec.routing?.enabled
            ? `on, ${spec.routing.destination_points?.length ?? 0} fixed destinations`
            : "off"}
        </div>
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
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState("");
  const [spec, setSpec] = useState<AppSpec | null>(null);
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
  ].filter(Boolean) as string[];

  const setInput = (k: number, patch: Partial<Draft>) =>
    setInputs((xs) => xs.map((x, j) => (j === k ? { ...x, ...patch } : x)));

  const compile = async () => {
    if (!bbox) return;
    setBusy(true);
    setError("");
    setSpec(null);
    try {
      const body = {
        intent: intent.trim(),
        grid: { cell_m: cellM, bbox },
        inputs: inputs.map(
          (i): CompileInput => ({ id: i.id, kind: i.kind, shape: i.shape, meaning: i.meaning.trim(), fields: parseFields(i.fields) }),
        ),
      };
      setSpec(await api.compile(appId, body));
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
        <AreaPicker bbox={bbox} onChange={setBbox} />
        <label style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 6 }}>
          Cell size (m)
          <input type="number" min={1} value={cellM} onChange={(e) => setCellM(Number(e.target.value))} style={{ width: 90 }} />
          {dims && <span style={muted}>{`grid ${dims[0]} x ${dims[1]} cells`}</span>}
        </label>
      </fieldset>
      <fieldset style={{ ...box, display: "grid", gap: 10 }}>
        <legend>Inputs</legend>
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
              placeholder="What does one event of this input mean? Your words go to the Compiler verbatim."
              value={i.meaning}
              onChange={(e) => setInput(k, { meaning: e.target.value })}
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
          Add input
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
          <Review spec={spec} />
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button type="button" disabled={busy} onClick={() => void confirm()}>
              Confirm and activate
            </button>
            <span style={muted}>Change the form and compile again to get a different proposal.</span>
          </div>
        </section>
      )}
    </div>
  );
}
