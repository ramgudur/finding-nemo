// Rule 8: authority data (weights, verification, trace, changes) needs the app's authority key.
// The key is stored per viewer via authorityKey.ts; it is never echoed or logged.
import { KeyRound } from "lucide-react";
import { useState } from "react";
import { saveKey } from "../../api/authorityKey";

export function AuthorityKeyPrompt({
  appId,
  rejected,
  onKey,
}: {
  appId: string;
  rejected: boolean;
  onKey: (key: string) => void;
}) {
  const [value, setValue] = useState("");
  const [remember, setRemember] = useState(false);
  const submit = () => {
    const k = value.trim();
    if (!k) return;
    saveKey(appId, k, remember);
    setValue("");
    onKey(k);
  };
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      className="space-y-2 rounded-2xl border border-paleWater bg-white/40 p-3.5"
    >
      <div className="flex items-center gap-2">
        <KeyRound aria-hidden className="h-4 w-4 text-mutedTeal" />
        <span className="text-[13px] font-semibold text-slate">Authority key needed</span>
      </div>
      <p className="text-[12px] text-ink">
        Weights, verification, the agent trace and the Change log are authority-only. Paste the key shown at activation.
      </p>
      {rejected && (
        <p role="alert" className="rounded-lg bg-coral/15 px-2 py-1 text-[12px] font-semibold text-[#7A4232]">
          Key rejected for this app.
        </p>
      )}
      <div className="flex gap-1.5">
        <input
          type="password"
          autoComplete="off"
          aria-label="authority key"
          placeholder="authority key"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="min-w-0 flex-1 rounded-lg border border-paleWater bg-white/70 px-2 py-1.5 font-mono text-[12px] text-slate placeholder:text-inkSoft/70"
        />
        <button
          type="submit"
          disabled={!value.trim()}
          className="rounded-lg bg-mutedTeal px-3 text-[12px] font-semibold text-cream shadow-sm transition-[transform,box-shadow] duration-150 hover:-translate-y-px hover:shadow-md disabled:opacity-50"
        >
          Use key
        </button>
      </div>
      <label className="flex items-center gap-1.5 text-[11.5px] text-inkSoft">
        <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} className="accent-mutedTeal" />
        remember on this device
      </label>
    </form>
  );
}
