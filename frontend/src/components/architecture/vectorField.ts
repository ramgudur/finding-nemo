// Vector-field layers (#284): a `field` operator with `vector_speed_field` + `vector_dir_field` reads a
// vector (magnitude in m/s, direction in degrees) from each event, and with `shift_layer` carries
// another layer along it before scoring. With weight 0 it adds no cells of its own, so its map layer
// is empty by design. These helpers read everything from the spec's operator params; nothing here
// names a domain (rule 3): the label comes from the dev's own field name.
import type { AppSpec, Event } from "../../api/types";
import { specLayers, type SpecLayer } from "./specLayers";

export const DEFAULT_SHIFT_HORIZON_S = 600; // engine default (FieldParams.shift_horizon_s)

export interface VectorLayer {
  layerId: string;
  inputId: string;
  speedField: string;
  dirField: string;
  convention: "from" | "to";
  /** Layer id carried along the field, or null when the field only describes. */
  shiftLayer: string | null;
  horizonS: number;
  /** Weight 0: the layer only moves another layer and adds nothing to the sum. */
  shiftOnly: boolean;
  /** A readable name from the dev's speed field (e.g. "flow_speed" → "Flow"), else the input id. */
  name: string;
}

export interface VectorReading {
  speed: number; // m/s
  /** Direction of travel, degrees clockwise from north (convention already resolved). */
  towardDeg: number;
  /** Where it comes from, degrees clockwise from north. */
  fromDeg: number;
  ts: string;
}

const str = (v: unknown) => (typeof v === "string" && v ? v : null);

function nameOf(speedField: string, inputId: string): string {
  const words = speedField
    .split(/[_\s-]+/)
    .filter((w) => w && !/^(speed|magnitude|mag|velocity|vel|spd|\d+\w*)$/i.test(w));
  const base = words.length ? words.join(" ") : inputId.replace(/[_-]+/g, " ");
  return base.charAt(0).toUpperCase() + base.slice(1);
}

export function vectorLayerOf(l: SpecLayer): VectorLayer | null {
  const op = l.op as Record<string, unknown>;
  const speedField = str(op.vector_speed_field);
  const dirField = str(op.vector_dir_field);
  if (!speedField || !dirField) return null;
  const horizon = typeof op.shift_horizon_s === "number" && op.shift_horizon_s >= 0 ? op.shift_horizon_s : DEFAULT_SHIFT_HORIZON_S;
  return {
    layerId: l.layerId,
    inputId: l.input.id,
    speedField,
    dirField,
    convention: op.vector_dir_convention === "to" ? "to" : "from",
    shiftLayer: str(op.shift_layer),
    horizonS: horizon,
    shiftOnly: l.op.weight === 0,
    name: nameOf(speedField, l.input.id),
  };
}

export function vectorLayers(spec: AppSpec | null): VectorLayer[] {
  return spec ? specLayers(spec).flatMap((l) => vectorLayerOf(l) ?? []) : [];
}

const norm360 = (d: number) => ((d % 360) + 360) % 360;

/** The latest event's vector, or null when the fields are missing or not numbers. */
export function readVector(v: VectorLayer, e: Event | undefined): VectorReading | null {
  if (!e) return null;
  const speed = Number(e[v.speedField]);
  const deg = Number(e[v.dirField]);
  if (!Number.isFinite(speed) || !Number.isFinite(deg) || speed < 0) return null;
  const towardDeg = norm360(v.convention === "to" ? deg : deg + 180);
  return { speed, towardDeg, fromDeg: norm360(towardDeg + 180), ts: e.ts };
}

const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
export const compass = (deg: number) => COMPASS[Math.round(norm360(deg) / 22.5) % 16]!;

export const fmtSpeed = (mps: number) => `${mps < 10 ? mps.toFixed(1) : Math.round(mps)} m/s`;

export function fmtDistance(m: number): string {
  if (m < 1000) return `${Math.round(m / 10) * 10} m`;
  return `${(m / 1000).toFixed(m < 10_000 ? 1 : 0)} km`;
}

/** How far the shifted layer is carried: speed × horizon (advection; diffusion only spreads it). */
export const driftM = (v: VectorLayer, r: VectorReading) => r.speed * v.horizonS;
