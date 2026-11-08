// Deterministic (no LLM) description of one layer, built from the input's kind/shape/meaning and the operator.
// Domain-neutral: the only domain words are the dev's own, quoted from `meaning`.
import type { InputShape, InputSpec, OperatorConfig, OperatorInfo } from "../../api/types";

const SHAPE_WORDS: Record<InputShape, string> = {
  point: "point events",
  track: "movement tracks",
  fixed_point: "fixed authority points",
  field: "gridded or station readings",
  document: "geocoded documents",
};

/** Used when GET /v1/operators hasn't loaded; mirrors the registry's one-line descriptions in spirit. */
const FALLBACK_OP: Record<string, string> = {
  flow_source: "net outflow of moving entities per cell, plus a back-projected source or sink point",
  density: "smoothed density with half-life decay, trust weights and duplicate removal",
  dwell: "stop detection: entities staying put, as stationary clusters",
  field: "readings interpolated onto the grid, optionally carried along a vector field",
  event_points: "confidence × source reliability, decaying over time",
  override: "score forced to 1.0 within a radius until cleared",
};

function source(input: InputSpec): string {
  if (input.kind === "push") return `${SHAPE_WORDS[input.shape]} your app POSTs`;
  const c = input.connector;
  if (!c) return `${SHAPE_WORDS[input.shape]} the platform will collect (connector pending)`;
  const what = c.preset ? `the ${c.preset} preset` : c.type === "local_file" ? "a local text file" : `a ${c.type === "rss" ? "RSS" : "JSON"} connector`;
  return `${SHAPE_WORDS[input.shape]} polled from ${what}`;
}

function lowerFirst(s: string): string {
  const t = s.trim().replace(/\.$/, "");
  return t ? t[0]!.toLowerCase() + t.slice(1) : t;
}

/** Sentence 1: what the layer computes. Sentence 2: how it enters the score. */
export function describeLayer(input: InputSpec, op: OperatorConfig, info?: OperatorInfo | null): string {
  const what = info?.description ? lowerFirst(info.description) : FALLBACK_OP[op.op] ?? `the ${op.op} operator`;
  const isOverride = info ? info.role === "override" : op.op === "override";
  const radius = typeof op.radius_m === "number" ? `Radius ${op.radius_m} m; it` : "It";
  const how = isOverride
    ? `${radius} always wins over the weighted layers.`
    : `Normalized, then weighted ${op.weight} in the sum.`;
  const head = what.charAt(0).toUpperCase() + what.slice(1);
  return `${head}. Fed by ${source(input)}. ${how}`;
}

export function LayerDescription({
  input,
  op,
  info,
  showMeaning = false,
}: {
  input: InputSpec;
  op: OperatorConfig;
  info?: OperatorInfo | null;
  showMeaning?: boolean;
}) {
  return (
    <span className="block space-y-1 text-[12.5px] leading-snug text-ink">
      <span className="block">{describeLayer(input, op, info)}</span>
      {showMeaning && input.meaning && (
        <span className="block border-l-2 border-sage/70 pl-2 italic text-inkSoft">“{input.meaning}”</span>
      )}
    </span>
  );
}

export default LayerDescription;
