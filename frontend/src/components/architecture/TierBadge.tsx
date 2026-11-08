// Tier badge: locked (stone, lock icon), stiff (sea glass, ±10%/h), flexible (soft aqua, ±25%/step).
import { Lock, Waves, Wind } from "lucide-react";
import type { Tier } from "../../api/types";
import { TIER_HELP, TIER_RULE } from "./specLayers";

/** Row/card surface per tier, so the tier reads at a glance even before the badge. */
export const TIER_SURFACE: Record<Tier, string> = {
  locked: "border-[#C9C2AE] bg-[#E6E1D3]/80",
  stiff: "border-seaGlass/60 bg-[#D3E6E3]/80",
  flexible: "border-softAqua bg-[#E3F2F3]/85",
};

const BADGE: Record<Tier, string> = {
  locked: "bg-softStone text-slate ring-1 ring-[#BDB49C]",
  stiff: "bg-seaGlass/35 text-[#2F6265] ring-1 ring-seaGlass/70",
  flexible: "bg-softAqua/55 text-[#2E6670] ring-1 ring-softAqua",
};

const ICON = { locked: Lock, stiff: Waves, flexible: Wind } as const;

export function TierBadge({ tier, compact = false }: { tier: Tier; compact?: boolean }) {
  const Icon = ICON[tier];
  return (
    <span
      title={TIER_HELP[tier]}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold ${BADGE[tier]}`}
    >
      <Icon aria-hidden className="h-3 w-3" strokeWidth={2.25} />
      {tier}
      {!compact && <span className="font-mono font-normal opacity-80">{TIER_RULE[tier]}</span>}
    </span>
  );
}
