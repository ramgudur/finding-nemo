// Plain-language copy for every builder and review setting: a short name plus one line on what it does to
// the dev's map. Copy only; the platform's guards enforce the real rules (CLAUDE.md rule 5).
import type { InputShape, Tier, VerificationMethod, Visibility } from "../../api/types";

export interface Plain {
  name: string;
  line: string;
}

export const TIER_PLAIN: Record<Tier, Plain> = {
  locked: { name: "Locked", line: "Never changes. Only you set it." },
  stiff: { name: "Stiff", line: "The AI may adjust it slowly, and only with proof from a confirmed report." },
  flexible: { name: "Flexible", line: "The AI may tune it as evidence comes in, after testing each change." },
};

/** The longer rule behind each tier, for the "?" panel. */
export const TIER_DETAIL =
  "A tier decides how far the AI may move this source's weight on the map. Locked weights never move. Stiff ones move at most 10% an hour, and only when a confirmed report backs the change. Flexible ones move at most 25% a step, and only after a test on held-back data passes. You can undo any change, and tiers are fixed once you confirm.";

export const OPERATOR_PLAIN: Record<string, Plain> = {
  density: { name: "Clusters", line: "Where reports cluster." },
  flow_source: { name: "Movement", line: "Where things are moving away from, or toward." },
  dwell: { name: "Stops", line: "Where things stop and stay." },
  field: { name: "Spread value", line: "A value spread over the area, such as readings from stations." },
  event_points: { name: "Named places", line: "Places named in news or documents." },
  override: { name: "Confirmed zone", line: "A confirmed zone that always wins over everything else." },
};

export function operatorPlain(op: string): Plain {
  return OPERATOR_PLAIN[op] ?? { name: op.replace(/_/g, " "), line: "A way of turning this source into a map layer." };
}

export const OPERATOR_DETAIL =
  "Each way of reading a source makes one layer of the map. The layers are added together, each times its weight, to make the score from 0 to 1. A higher weight means that layer counts for more.";

export const VERIFICATION_PLAIN: Record<VerificationMethod, Plain> = {
  authority_confirmation: {
    name: "Confirmed reports",
    line: "When an official or confirmed report arrives, how early and how close the map already was.",
  },
  predictive_check: {
    name: "Predicts new data",
    line: "Whether new reports land where the map was already hot, leaving each source out of its own check.",
  },
  cross_input_agreement: {
    name: "Sources agree",
    line: "Whether different sources point at the same places.",
  },
};

export const VERIFICATION_DETAIL =
  "Before the AI changes a weight, it replays recent data and checks that the map gets better. The first check that has data decides, and no other check may get worse. Until at least two checks have data, the AI changes nothing.";

export const VISIBILITY_PLAIN: Record<Visibility, Plain> = {
  public: { name: "Public", line: "Anyone with the app's link can see it." },
  authority: { name: "Private", line: "Only you, with the app's authority key." },
};

export const SHAPE_PLAIN: Record<InputShape, string> = {
  point: "single reports",
  track: "moving tracks",
  fixed_point: "fixed points",
  field: "measurements over the area",
  document: "documents",
};

export const PLAUSIBILITY_DETAIL =
  "These limits catch fake or broken reports. A sender moving faster than the top speed, or at a perfectly constant speed, or in a perfectly straight line, is flagged and trusted less. Leave a limit empty to switch that check off. Checks that hold for anything (the same spot sent by many accounts, bursts of new senders) always run.";

export const CELL_SIZE_DETAIL =
  "The map is scored on a grid of square cells. Smaller cells give sharper hotspots but there are more of them, so a bigger area needs bigger cells to stay under the limit.";

export const DESTINATION_DETAIL =
  "If your app gives people directions, these are the places it can send them, such as a meeting point. Routes avoid hot areas on the way. Every destination must be inside the area.";

export const HOTSPOT_DETAIL = "Cells with a score at or above this value join up into a hotspot. Lower it to see more hotspots; raise it to see only the strongest.";

export const LABEL_MODE_PLAIN: Record<"auto" | "on" | "off", Plain> = {
  auto: { name: "When useful", line: "The AI names places only when the data shows somewhere worth naming." },
  on: { name: "Always", line: "The AI looks for places to name on every run." },
  off: { name: "Off", line: "No places are named." },
};

export const LABELS_DETAIL =
  "The AI can pin a name on a place where something keeps happening, using only spots the map already found. A private label is seen only with your key. A destination label is a place people can be routed to, so it is always public.";

export const ROUTING_DETAIL =
  "End users send their own position and get a short instruction toward the nearest reachable destination, steering around high scores. Nobody can look up anyone else's position.";

export const IMPASSABLE_DETAIL = "Routes never cross a cell whose score is at or above this value.";
export const ALPHA_DETAIL = "How hard routes steer around warmer cells. 0 takes the shortest path; higher values take longer, safer detours.";
