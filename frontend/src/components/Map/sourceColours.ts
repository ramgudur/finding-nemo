// "Colour by source" (authority view): each input gets a fixed hue, and each score cell is tinted by
// the input whose weighted, normalized layers add the most to it there (max over inputs of Σ wᵢ·normᵢ).
// The layers come from the authority MapResponse (already normalized, before weights) and the weights
// from /weights, so this is the engine's own sum split back into its parts; nothing is estimated.
// Vector-field inputs never tint (they move other layers; their arrows show them). Rule 3: hues are
// assigned by spec order, never by what an input is.
import type { AppSpec, MapResponse, WeightsView } from "../../api/types";
import { specLayers } from "../architecture/specLayers";
import { vectorLayers } from "../architecture/vectorField";

/** Categorical hues (tokens.css --c-src-1..6), checked for >= 3:1 contrast on the cream sheet and the map
 * tiles. Blue/teal (the water) and coral (reserved for risk) are left out on purpose. */
export const SOURCE_HUES = [1, 2, 3, 4, 5, 6].map((i) => `rgb(var(--c-src-${i}))`);
/** Cells with score but no weighted contribution (an authority override): the risk colour. */
export const OVERRIDE_HUE = "rgb(var(--c-heat-5))";

export interface SourceInput {
  inputId: string;
  /** Weighted layer ids of this input that /weights knows. */
  layerIds: string[];
  /** Null for vector-field inputs (they never tint). */
  hue: string | null;
  weight: number;
}

/** Inputs in spec order with their hue and summed weight. */
export function sourceInputs(spec: AppSpec | null, weights: WeightsView | null): SourceInput[] {
  if (!spec) return [];
  const w = new Map((weights?.layers ?? []).map((l) => [l.layer_id, l.weight]));
  const vectorInputs = new Set(vectorLayers(spec).map((v) => v.inputId));
  const byInput = new Map<string, string[]>();
  for (const l of specLayers(spec)) {
    if (!w.has(l.layerId)) continue; // overrides and unknown layers are not in the weighted sum
    byInput.set(l.input.id, [...(byInput.get(l.input.id) ?? []), l.layerId]);
  }
  const out: SourceInput[] = [];
  let k = 0;
  for (const input of spec.inputs ?? []) {
    const ids = byInput.get(input.id);
    if (!ids) continue;
    const vector = vectorInputs.has(input.id);
    out.push({
      inputId: input.id,
      layerIds: ids,
      hue: vector ? null : (SOURCE_HUES[k++ % SOURCE_HUES.length] ?? null),
      weight: ids.reduce((s, id) => s + (w.get(id) ?? 0), 0),
    });
  }
  return out;
}

export const cellKey = (row: number, col: number) => `${row}|${col}`;

/** Dominant input per score cell ("row|col" → input id, or null for an override-only cell). */
export function dominantInputs(
  map: MapResponse | null,
  inputs: SourceInput[],
  weightOf: (layerId: string) => number,
): Map<string, string | null> {
  const out = new Map<string, string | null>();
  const score = map?.layers.score;
  if (!map || !score) return out;
  const contrib = new Map<string, Map<string, number>>(); // cell -> input -> Σ w·norm
  for (const inp of inputs) {
    if (!inp.hue) continue;
    for (const id of inp.layerIds) {
      const w = weightOf(id);
      if (!(w > 0)) continue;
      for (const f of map.layers[id]?.features ?? []) {
        const k = cellKey(f.properties.row, f.properties.col);
        let m = contrib.get(k);
        if (!m) contrib.set(k, (m = new Map()));
        m.set(inp.inputId, (m.get(inp.inputId) ?? 0) + w * f.properties.value); // missing cells count as 0
      }
    }
  }
  for (const f of score.features) {
    const k = cellKey(f.properties.row, f.properties.col);
    let best: string | null = null;
    let top = 0;
    for (const [id, v] of contrib.get(k) ?? []) {
      if (v > top) {
        top = v;
        best = id;
      }
    }
    out.set(k, best);
  }
  return out;
}

/** "row|col" → fill colour for the score layer, from the dominant input. */
export function sourceTint(dominant: Map<string, string | null>, inputs: SourceInput[]): Map<string, string> {
  const hue = new Map(inputs.map((i) => [i.inputId, i.hue]));
  const out = new Map<string, string>();
  for (const [k, id] of dominant) out.set(k, (id && hue.get(id)) || OVERRIDE_HUE);
  return out;
}
