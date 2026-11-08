// PLACEHOLDER — replaced by fe-arch-left / fe-arch-right
import type { AppSpec } from "../../api/types";

export interface HeatmapPanelProps {
  spec: AppSpec;
  appId: string;
  authorityKey: string | null;
  active: boolean;
  selectedLayerId: string | null;
}

export default function HeatmapPanel({ appId, active, selectedLayerId }: HeatmapPanelProps) {
  return (
    <div className="flex h-full min-h-[360px] flex-col items-center justify-center gap-1 rounded-3xl border border-dashed border-seaGlass/70 bg-washedSky/50 p-6 text-center">
      <p className="text-sm font-semibold text-slate">Heatmap panel</p>
      <p className="font-mono text-xs text-inkSoft">
        {appId} · {active ? "active" : "not yet live"} · layer {selectedLayerId ?? "score"}
      </p>
    </div>
  );
}
