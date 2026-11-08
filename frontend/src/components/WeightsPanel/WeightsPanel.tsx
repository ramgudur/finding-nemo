// Current layer weights with tier and pin state; the dev can pin/unpin (recorded as a Change).
// Locked weights show a lock and no pin control: nothing can move them.
import { Lock, Pin, PinOff } from "lucide-react";
import type { WeightsView } from "../../api/types";
import { TierBadge } from "../architecture/TierBadge";

interface Props {
  view: WeightsView;
  busy: boolean;
  onPin: (layerId: string, pinned: boolean) => void;
}

export function WeightsPanel({ view, busy, onPin }: Props) {
  return (
    <div className="space-y-2">
      <ul className="divide-y divide-paleWater/80 overflow-hidden rounded-2xl border border-paleWater bg-white/35">
        {view.layers.map((l) => (
          <li key={l.layer_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
            <span className="min-w-[9rem] flex-1 break-all font-mono text-[12.5px] text-slate">{l.layer_id}</span>
            <TierBadge tier={l.tier} compact />
            <span className="w-14 text-right font-mono text-[12.5px] tabular-nums text-slate">{l.weight.toFixed(3)}</span>
            {l.tier === "locked" ? (
              <span className="inline-flex w-[76px] items-center justify-center gap-1 text-[11px] text-inkSoft" title="Locked: nothing can move this weight">
                <Lock aria-hidden className="h-3 w-3" /> fixed
              </span>
            ) : (
              <button
                type="button"
                disabled={busy}
                aria-pressed={l.pinned}
                aria-label={`${l.pinned ? "unpin" : "pin"} ${l.layer_id}`}
                onClick={() => onPin(l.layer_id, !l.pinned)}
                className={`inline-flex w-[76px] items-center justify-center gap-1 rounded-full px-2 py-1 text-[11px] font-semibold transition-[transform,box-shadow,background-color] duration-150 hover:-translate-y-px disabled:opacity-50 ${
                  l.pinned
                    ? "bg-coral/25 text-danger ring-1 ring-coral/60"
                    : "bg-white/60 text-ink ring-1 ring-paleWater hover:shadow-sm"
                }`}
              >
                {l.pinned ? <PinOff aria-hidden className="h-3 w-3" /> : <Pin aria-hidden className="h-3 w-3" />}
                {l.pinned ? "unpin" : "pin"}
              </button>
            )}
          </li>
        ))}
      </ul>
      <p className="text-[12px] text-ink">
        Total flexible weight <span className="font-mono tabular-nums text-slate">{view.flexible_total.toFixed(3)}</span>{" "}
        <span className="text-inkSoft">stays fixed when flexible weights are renormalized. A pinned weight is never moved by an agent.</span>
      </p>
    </div>
  );
}
