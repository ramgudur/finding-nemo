// The app id gates the rest of the page: suggestions and compile are scoped to /v1/apps/{app}.
import { Check } from "lucide-react";
import { SLUG } from "./sourceUtils";

export function AppIdField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const valid = SLUG.test(value);
  const touched = value.length > 0;
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor="app-id" className="text-xs font-semibold uppercase tracking-[0.14em] text-inkSoft">
        App id
      </label>
      <div
        className={`flex items-center gap-2 rounded-2xl border bg-cream/70 px-4 py-2.5 shadow-[0_6px_24px_-14px_rgba(53,89,107,0.45)] backdrop-blur-[2px] transition-colors focus-within:bg-cream/90 ${
          touched && !valid ? "border-coral/70" : "border-white/70"
        }`}
      >
        <span className="font-mono text-sm text-inkSoft">/v1/apps/</span>
        <input
          id="app-id"
          value={value}
          onChange={(e) => onChange(e.target.value.toLowerCase().replace(/\s+/g, "-"))}
          placeholder="my-app"
          spellCheck={false}
          autoComplete="off"
          className="min-w-0 flex-1 bg-transparent font-mono text-base text-slate outline-none placeholder:text-inkSoft/60"
          aria-invalid={touched && !valid}
          aria-describedby="app-id-hint"
        />
        {valid && <Check size={16} className="shrink-0 text-mutedTeal" aria-label="valid id" />}
      </div>
      <p id="app-id-hint" className={`text-xs ${touched && !valid ? "text-coral" : "text-inkSoft"}`}>
        {touched && !valid
          ? "Start with a letter or digit; then lowercase letters, digits, - or _ (up to 64)."
          : valid
            ? "Name set. Describe the area, intent and sources below."
            : "Name the app first; everything below is scoped to it."}
      </p>
    </div>
  );
}
