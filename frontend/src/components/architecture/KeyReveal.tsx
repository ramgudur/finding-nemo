// The one-time key reveal after /activate. The platform can't show these keys again.
import { Check, Copy, KeyRound, TriangleAlert } from "lucide-react";
import { useState } from "react";
import type { ActivateResponse } from "../../api/types";

function CopyField({ label, hint, value }: { label: string; hint: string; value: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      // clipboard unavailable (insecure origin): the value stays selectable below
    }
  };
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[12.5px] font-semibold text-slate">{label}</span>
        <span className="text-[11px] text-inkSoft">{hint}</span>
      </div>
      <div className="flex items-stretch gap-1.5">
        <input
          readOnly
          value={value}
          aria-label={label}
          onFocus={(e) => e.currentTarget.select()}
          className="min-w-0 flex-1 rounded-lg border border-paleWater bg-white/70 px-2 py-1.5 font-mono text-[12px] text-slate"
        />
        <button
          type="button"
          onClick={() => void copy()}
          className="inline-flex items-center gap-1 rounded-lg bg-mutedTeal px-2.5 text-[12px] font-semibold text-cream shadow-sm transition-[transform,box-shadow] duration-150 hover:-translate-y-px hover:shadow-md"
        >
          {copied ? <Check aria-hidden className="h-3.5 w-3.5" /> : <Copy aria-hidden className="h-3.5 w-3.5" />}
          {copied ? "copied" : "copy"}
        </button>
      </div>
    </div>
  );
}

export function KeyReveal({ keys, onDismiss }: { keys: ActivateResponse; onDismiss: () => void }) {
  return (
    <section className="space-y-3 rounded-2xl border border-coral/50 bg-cream/80 p-4 shadow-[0_8px_24px_-14px_rgba(217,147,126,0.7)]">
      <div className="flex items-center gap-2">
        <KeyRound aria-hidden className="h-4 w-4 text-mutedTeal" />
        <h3 className="text-[14px] font-bold text-slate">
          <span className="font-mono">{keys.app_id}</span> is active
        </h3>
      </div>
      <p className="flex items-start gap-1.5 rounded-xl bg-coral/15 px-2.5 py-2 text-[12.5px] text-[#7A4232]">
        <TriangleAlert aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        These keys are shown once. Copy them now: the platform can't show them again.
      </p>
      <CopyField label="Ingest key" hint="your app sends it as X-Ingest-Key" value={keys.ingest_key} />
      <CopyField label="Authority key" hint="opens this console's authority view" value={keys.authority_key} />
      <p className="text-[11.5px] text-inkSoft">The authority key is kept for this browser tab only.</p>
      <button
        type="button"
        onClick={onDismiss}
        className="rounded-full bg-white/70 px-3 py-1 text-[12px] font-semibold text-ink ring-1 ring-paleWater transition-[transform,box-shadow] duration-150 hover:-translate-y-px hover:shadow-sm"
      >
        I've copied both keys
      </button>
    </section>
  );
}
