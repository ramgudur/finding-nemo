// "What the public will see" on the Review page: a plain-words summary, derived from the spec with the
// server's own visibility rules, of what a request without the authority key gets (rule 8), plus two
// one-step fixes (make the risk map public; turn on place labels). Each fix is one PUT /spec of the
// saved proposal, with an undo that PUTs the previous outputs back. The dev still confirms (rule 4).
//
// Server rules mirrored here (keep in sync):
// - validate_spec (tools/compiler_tools.py): `score` may be public only if every weighted layer is
//   public; weighted = a registered operator whose role isn't "override".
// - hotspot_visibility (engine/tick.py): a hotspot is public only if every layer shaping it is public;
//   override layers count where they touch the hotspot.
// - poi_visibility (tools/poi_tools.py): sensitive -> private; destination -> public; any other label
//   -> public only where its contributing layers are public.
// - routing: needs a destination label (with labels on) or a fixed destination point.
// - map (api/outputs.py): only layers listed public in outputs.layers; inputs' raw events never.
import { Eye, MapPin, Undo2 } from "lucide-react";
import { useState } from "react";
import { ApiError, api } from "../../api/client";
import type { AppSpec, OperatorInfo, OutputLayer, Outputs } from "../../api/types";
import { specLayers, useOperatorCatalog, type SpecLayer } from "../architecture/specLayers";

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const listWords = (xs: string[]) =>
  xs.length <= 1 ? (xs[0] ?? "") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;

/** True when a layer is added into the score (not an override mask applied after the sum). */
function isWeighted(l: SpecLayer, catalog: OperatorInfo[] | null): boolean {
  const info = catalog?.find((o) => o.name === l.op.op);
  if (catalog && !info) return false; // unknown operator: the server skips it too
  return info ? info.role !== "override" : l.op.op !== "override";
}

export interface PublicView {
  /** Plain-words parts the public gets; empty means nothing. */
  parts: string[];
  /** The server would reject the saved visibilities (public score over a private weighted layer). */
  invalidScore: boolean;
  weighted: SpecLayer[];
  /** Weighted layers (and score) that "Make the risk map public" would change. */
  toPublish: { score: boolean; layers: SpecLayer[] };
  labelsOff: boolean;
}

export function publicView(spec: AppSpec, catalog: OperatorInfo[] | null): PublicView {
  const all = specLayers(spec);
  const publicIds = new Set((spec.outputs?.layers ?? []).filter((l) => l.visibility === "public").map((l) => l.id));
  const weighted = all.filter((l) => isWeighted(l, catalog));
  const publicWeighted = weighted.filter((l) => publicIds.has(l.layerId));
  const allWeightedPublic = weighted.length > 0 && publicWeighted.length === weighted.length;
  const scorePublic = publicIds.has("score") && allWeightedPublic;
  const invalidScore = publicIds.has("score") && !allWeightedPublic && weighted.length > 0;
  const privateOverrides = all.filter((l) => !isWeighted(l, catalog) && !publicIds.has(l.layerId));
  const parts: string[] = [];

  // the map
  const publicLayers = all.filter((l) => publicIds.has(l.layerId));
  if (scorePublic) parts.push("The risk map");
  else if (publicLayers.length > 0)
    parts.push(`${plural(publicLayers.length, "layer")} of the risk map, but not the combined score`);

  // hotspots
  if (allWeightedPublic) parts.push(privateOverrides.length ? "its hotspots, except where authority zones apply" : "its hotspots");
  else if (publicWeighted.length > 0) parts.push("hotspots made only of public layers");

  // place labels
  const pl = spec.outputs?.poi_labels;
  const mode = pl?.mode ?? "auto";
  const set = (pl?.label_set ?? []).filter((x) => !x.sensitive);
  const destLabels = mode === "off" ? [] : set.filter((x) => x.destination).map((x) => x.label);
  const otherLabels = mode === "off" || publicWeighted.length === 0 ? [] : set.filter((x) => !x.destination).map((x) => x.label);
  const shownLabels = [...destLabels, ...otherLabels];
  if (shownLabels.length) parts.push(`place labels (${listWords(shownLabels.map((x) => `“${x}”`))})`);

  // directions
  const r = spec.routing;
  const points = r?.destination_points?.length ?? 0;
  if (r?.enabled && (points > 0 || destLabels.length > 0)) {
    const to = [
      points > 0 ? plural(points, "fixed point") : "",
      destLabels.length ? `places labelled ${listWords(destLabels.map((x) => `“${x}”`))}` : "",
    ].filter(Boolean);
    parts.push(`directions to ${listWords(to)}`);
  }

  return {
    parts,
    invalidScore,
    weighted,
    toPublish: { score: !scorePublic, layers: weighted.filter((l) => !publicIds.has(l.layerId)) },
    labelsOff: mode === "off",
  };
}

/** outputs.layers with score and every weighted layer public; every other entry kept as it is. */
function publishLayers(spec: AppSpec, weighted: SpecLayer[]): OutputLayer[] {
  const want = new Set(["score", ...weighted.map((l) => l.layerId)]);
  const kept = (spec.outputs?.layers ?? []).map((l) => (want.has(l.id) ? { ...l, visibility: "public" as const } : l));
  const have = new Set(kept.map((l) => l.id));
  return [...kept, ...[...want].filter((id) => !have.has(id)).map((id) => ({ id, visibility: "public" as const }))];
}

const capitalise = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

export function PublicViewBox({
  spec,
  dirty,
  onSaved,
}: {
  /** The saved proposal (fixes apply to it, never to unsaved edits). */
  spec: AppSpec;
  dirty: boolean;
  onSaved: (spec: AppSpec) => void;
}) {
  const catalog = useOperatorCatalog();
  const v = publicView(spec, catalog);
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [undo, setUndo] = useState<{ what: string; outputs: Outputs } | null>(null);
  const [naming, setNaming] = useState(false);
  const [labelName, setLabelName] = useState("");

  const put = async (outputs: Outputs, done: (prev: Outputs) => void) => {
    setBusy(true);
    setErrors([]);
    const prev = structuredClone(spec.outputs ?? {});
    try {
      const r = await api.updateSpec(spec.app_id, { ...spec, outputs });
      onSaved(r.spec);
      done(prev);
    } catch (e) {
      setErrors(e instanceof ApiError && e.details.length ? e.details : [e instanceof Error ? e.message : String(e)]);
    } finally {
      setBusy(false);
    }
  };

  const inputsToPublish = [...new Set(v.toPublish.layers.map((l) => l.input.id))];
  const canPublish = v.weighted.length > 0 && (v.toPublish.score || v.toPublish.layers.length > 0);
  const publishLine = [
    v.toPublish.score ? "the combined score" : "",
    inputsToPublish.length ? `the ${inputsToPublish.length === 1 ? "layer" : "layers"} from ${listWords(inputsToPublish)}` : "",
  ]
    .filter(Boolean)
    .join(", plus ");

  const makePublic = () =>
    void put({ ...spec.outputs, layers: publishLayers(spec, v.weighted) }, (prev) => setUndo({ what: "The risk map is now public.", outputs: prev }));

  const labelsOn = (name?: string) => {
    const pl = spec.outputs?.poi_labels ?? {};
    const label_set = name ? [{ label: name, sensitive: false, destination: false }] : (pl.label_set ?? []);
    void put({ ...spec.outputs, poi_labels: { ...pl, mode: "auto", label_set } }, (prev) => {
      setNaming(false);
      setLabelName("");
      setUndo({ what: "Place labels are on.", outputs: prev });
    });
  };

  const nothing = v.parts.length === 0;
  const blocked = dirty || busy;

  return (
    <section aria-labelledby="rv-public" className="bld-card p-4 sm:p-5">
      <div className="flex items-start gap-2.5">
        <Eye aria-hidden className="mt-1 h-4 w-4 shrink-0 text-mutedTeal" />
        <div className="min-w-0 flex-1 space-y-2">
          <h2 id="rv-public" className="bld-h2">
            What the public will see
          </h2>
          <p className="text-[13px] text-ink">
            Anyone with the app's public link, without your authority key. Raw reports always stay private.
          </p>
          <p
            aria-live="polite"
            className={nothing || v.invalidScore ? "rounded-xl bg-attnBg px-3 py-2 text-[14px] font-semibold text-attn" : "text-[15px] font-semibold text-slate"}
          >
            {nothing
              ? "Nothing: the map is private, there are no place labels and no directions."
              : capitalise(v.parts.join(" · "))}
          </p>
          {v.invalidScore && (
            <p className="text-[13px] text-attn">
              The combined score is marked public while some of its layers are private; the server will refuse this.
            </p>
          )}

          {(canPublish || v.labelsOff || undo || errors.length > 0) && (
            <div className="space-y-2 pt-1">
              {dirty && (canPublish || v.labelsOff) && (
                <p className="text-[12.5px] text-ink">Save or discard your edits below to use these.</p>
              )}
              {canPublish && (
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                  <button type="button" disabled={blocked} onClick={makePublic} className="bld-btn bld-btn-secondary bld-btn-sm">
                    <Eye aria-hidden className="h-3.5 w-3.5" /> Make the risk map public
                  </button>
                  <span className="text-[13px] text-ink">
                    Makes public: {publishLine}.
                  </span>
                </div>
              )}
              {v.labelsOff && !naming && (
                <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                  <button
                    type="button"
                    disabled={blocked}
                    onClick={() => ((spec.outputs?.poi_labels?.label_set ?? []).length ? labelsOn() : setNaming(true))}
                    className="bld-btn bld-btn-secondary bld-btn-sm"
                  >
                    <MapPin aria-hidden className="h-3.5 w-3.5" /> Turn on place labels
                  </button>
                  <span className="text-[13px] text-ink">The AI names places only when the data shows somewhere worth naming.</span>
                </div>
              )}
              {naming && (
                <form
                  className="flex flex-wrap items-center gap-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    const name = labelName.trim();
                    if (name) labelsOn(name);
                  }}
                >
                  <label htmlFor="rv-public-label" className="text-[13px] text-ink">
                    Kind of place to name
                  </label>
                  <input
                    id="rv-public-label"
                    autoFocus
                    value={labelName}
                    maxLength={60}
                    onChange={(e) => setLabelName(e.target.value)}
                    placeholder="e.g. problem spot"
                    className="bld-field is-sm w-[min(16rem,100%)]"
                  />
                  <button type="submit" disabled={blocked || !labelName.trim()} className="bld-btn bld-btn-secondary bld-btn-sm">
                    Turn on
                  </button>
                  <button type="button" onClick={() => setNaming(false)} className="bld-btn bld-btn-quiet bld-btn-sm">
                    Cancel
                  </button>
                </form>
              )}
              {undo && (
                <p role="status" className="flex flex-wrap items-center gap-2 text-[13px] text-slate">
                  {undo.what}
                  <button
                    type="button"
                    disabled={blocked}
                    onClick={() => void put(undo.outputs, () => setUndo(null))}
                    className="bld-btn bld-btn-quiet bld-btn-sm"
                  >
                    <Undo2 aria-hidden className="h-3.5 w-3.5" /> Undo
                  </button>
                </p>
              )}
              {errors.length > 0 && (
                <ul role="alert" className="list-disc space-y-0.5 pl-5 text-[13px] font-semibold text-danger">
                  {errors.map((x) => (
                    <li key={x} className="[overflow-wrap:anywhere]">
                      {x}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
