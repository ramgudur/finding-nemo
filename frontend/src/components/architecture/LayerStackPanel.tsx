// PLACEHOLDER — replaced by fe-arch-left / fe-arch-right
import type { ActivateResponse, AppSpec } from "../../api/types";

export interface LayerStackPanelProps {
  spec: AppSpec;
  appId: string;
  authorityKey: string | null;
  active: boolean;
  selectedLayerId: string | null;
  onSelectLayer: (id: string | null) => void;
  onSpecSaved: (spec: AppSpec) => void;
  onActivated: (resp: ActivateResponse) => void;
}

export default function LayerStackPanel({ spec, selectedLayerId, onSelectLayer }: LayerStackPanelProps) {
  const layers = (spec.inputs ?? []).flatMap((i) => (i.operators ?? []).map((o) => o.layer_id ?? `${i.id}.${o.op}`));
  return (
    <div className="flex h-full min-h-[360px] flex-col gap-2 rounded-3xl border border-dashed border-seaGlass/70 bg-washedSky/50 p-6">
      <p className="text-sm font-semibold text-slate">Layer stack panel</p>
      {layers.map((id) => (
        <button
          key={id}
          type="button"
          onClick={() => onSelectLayer(selectedLayerId === id ? null : id)}
          className={`rounded-xl px-3 py-1.5 text-left font-mono text-xs ${selectedLayerId === id ? "bg-seaGlass/40 text-slate" : "bg-cream/60 text-ink"}`}
        >
          {id}
        </button>
      ))}
    </div>
  );
}
