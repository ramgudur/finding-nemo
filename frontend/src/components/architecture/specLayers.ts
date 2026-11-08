// Shared, deterministic helpers for the right panel: layer ids, tier copy and the operator catalogue.
import { useEffect, useState } from "react";
import { ApiError, api } from "../../api/client";
import type { AppSpec, InputShape, InputSpec, OperatorConfig, OperatorInfo, Tier, Visibility } from "../../api/types";

export const layerIdOf = (inputId: string, o: OperatorConfig) => o.layer_id || `${inputId}.${o.op}`;

export interface SpecLayer {
  layerId: string;
  input: InputSpec;
  op: OperatorConfig;
  /** Visibility of this layer on /map (outputs.layers), default authority. */
  visibility: Visibility;
}

/** One entry per operator layer across all inputs, in spec order. */
export function specLayers(spec: AppSpec): SpecLayer[] {
  const out = spec.outputs?.layers ?? [];
  return (spec.inputs ?? []).flatMap((input) =>
    (input.operators ?? []).map((op) => {
      const layerId = layerIdOf(input.id, op);
      return { layerId, input, op, visibility: out.find((l) => l.id === layerId)?.visibility ?? "authority" };
    }),
  );
}

export const TIERS: Tier[] = ["locked", "stiff", "flexible"];

/** What each tier lets an agent do to a weight (CLAUDE.md rule 5); copy only, the guards enforce it. */
export const TIER_RULE: Record<Tier, string> = {
  locked: "never changes",
  stiff: "±10%/h",
  flexible: "±25%/step",
};

export const TIER_HELP: Record<Tier, string> = {
  locked: "Authority input: no agent can ever change this weight.",
  stiff: "Agents may move it at most ±10% per hour, and only when authority confirmation decides the backtest.",
  flexible: "Agents may move it at most ±25% per step, after a passing held-out backtest; flexible weights renormalize to a fixed sum.",
};

/** Fallback when GET /v1/operators hasn't loaded: the registered operators and the shapes each accepts. */
export const FALLBACK_OPERATOR_SHAPES: Record<string, InputShape[]> = {
  flow_source: ["track"],
  density: ["point", "track"],
  dwell: ["track"],
  field: ["field"],
  event_points: ["document"],
  override: ["fixed_point"],
};

let catalogue: OperatorInfo[] | null = null;
let pending: Promise<OperatorInfo[]> | null = null;

/** The public operator catalogue, fetched once per page load; null until (or unless) it loads. */
export function useOperatorCatalog(): OperatorInfo[] | null {
  const [ops, setOps] = useState<OperatorInfo[] | null>(catalogue);
  useEffect(() => {
    if (catalogue) return;
    let live = true;
    pending ??= api.operators().then((x) => (catalogue = x));
    pending.then(
      (x) => live && setOps(x),
      () => {
        pending = null; // try again on the next mount
      },
    );
    return () => {
      live = false;
    };
  }, []);
  return ops;
}

export function operatorChoices(shape: InputShape, catalog: OperatorInfo[] | null): string[] {
  if (catalog) return catalog.filter((o) => o.shapes.includes(shape)).map((o) => o.name);
  return Object.keys(FALLBACK_OPERATOR_SHAPES).filter((n) => FALLBACK_OPERATOR_SHAPES[n]?.includes(shape));
}

/** A readable sentence for any API failure; `details` (a 422 list) is joined by the caller when wanted. */
export function errorText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}

export const fmtWeight = (w: number) => (Number.isFinite(w) ? w.toFixed(3) : "—");
