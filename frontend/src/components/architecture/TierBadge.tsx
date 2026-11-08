// Tier badge: locked (solid deep, lock icon: never editable), stiff (teal, ±10%/h), flexible (reed green, ±25%/step).
import { Lock, Waves, Wind } from "lucide-react";
import type { Tier } from "../../api/types";
import { TIER_HELP, TIER_RULE } from "./specLayers";

/** Row/card surface per tier, so the tier reads at a glance even before the badge. */
export const TIER_SURFACE: Record<Tier, string> = {
  locked: "border-tierLocked/[0.14] bg-tierLocked/[0.06]",
  stiff: "border-tierStiff/[0.18] bg-tierStiff/[0.07]",
  flexible: "border-tierFlex/[0.2] bg-tierFlex/[0.07]",
};

const BADGE: Record<Tier, string> = {
  locked: "bg-tierLocked text-cream",
  stiff: "bg-tierStiff/[0.14] text-tierStiff",
  flexible: "bg-tierFlex/[0.16] text-tierFlex",
};

const ICON = { locked: Lock, stiff: Waves, flexible: Wind } as const;

export function TierBadge({ tier, compact = false }: { tier: Tier; compact?: boolean }) {
  const Icon = ICON[tier];
  return (
    <span
      title={TIER_HELP[tier]}
      className={`wp-chip !text-[11.5px] font-bold ${BADGE[tier]}`}
    >
      <Icon aria-hidden className="h-3 w-3" strokeWidth={2.25} />
      {tier}
      {!compact && <span className="font-mono font-normal opacity-80">{TIER_RULE[tier]}</span>}
    </span>
  );
}
