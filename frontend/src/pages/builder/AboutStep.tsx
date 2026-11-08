// Step 1: what the app is called and what it's for. The name derives the app id (shown small, editable);
// the intent is the big hero input. Nothing else is asked here.
import { Pencil } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { listProjects } from "../../api/authorityKey";
import { BigInput } from "../../components/builder/BigInput";
import { StepFooter } from "../../components/builder/StepFooter";
import { useActiveIds, useDraft } from "../../components/builder/useDraft";
import { SLUG, aboutProblems, slugFromName } from "../../components/sources/sourceUtils";
import { useBuilderStore } from "../../store/useBuilderStore";

export function AboutStep() {
  const navigate = useNavigate();
  const draft = useDraft();
  const activeIds = useActiveIds();
  const appIdCustom = useBuilderStore((s) => s.appIdCustom);
  const { setName, setAppId, setAppIdCustom, setIntent } = useBuilderStore.getState();
  const [editingId, setEditingId] = useState(false);
  const idInput = useRef<HTMLInputElement>(null);
  const nameInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!draft.name) nameInput.current?.focus({ preventScroll: true });
    // focus the name once, on arrival
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (editingId) idInput.current?.select();
  }, [editingId]);

  const problems = aboutProblems(draft, activeIds);
  const idValid = SLUG.test(draft.appId);
  const idLive = idValid && activeIds.includes(draft.appId);
  const idPending = idValid && !idLive && listProjects().some((p) => p.appId === draft.appId && p.status === "proposed");

  const onName = (v: string) => {
    setName(v);
    if (!appIdCustom) setAppId(slugFromName(v));
  };

  const next = () => {
    if (problems.length === 0) navigate("/new/area");
  };

  return (
    <>
      <header className="flex flex-col gap-1.5">
        <h1 className="text-[28px] font-semibold leading-tight tracking-[-0.02em] text-slate">About your app</h1>
        <p className="max-w-[62ch] text-[15px] text-ink">
          Name it and say what it should do, in your own words. The Compiler reads this to plan the app.
        </p>
      </header>

      <div className="flex flex-col gap-2">
        <label htmlFor="app-name" className="text-sm font-semibold text-slate">
          Name
        </label>
        <input
          ref={nameInput}
          id="app-name"
          value={draft.name}
          onChange={(e) => onName(e.target.value)}
          placeholder="e.g. Harbour watch"
          autoComplete="off"
          maxLength={80}
          className="rounded-2xl border border-white/70 bg-cream/70 px-4 py-3 text-lg text-slate shadow-[0_6px_24px_-14px_rgba(53,89,107,0.45)] transition-colors duration-150 placeholder:text-inkSoft focus:bg-cream/95"
          aria-describedby="app-id-line"
        />
        <div id="app-id-line" className="flex flex-wrap items-center gap-x-2 gap-y-1 px-1 text-[13px] text-ink">
          <span>App id:</span>
          {editingId ? (
            <input
              ref={idInput}
              value={draft.appId}
              aria-label="App id"
              aria-invalid={!idValid || idLive}
              onChange={(e) => {
                setAppIdCustom(true);
                setAppId(e.target.value.toLowerCase().replace(/\s+/g, "-"));
              }}
              onBlur={() => setEditingId(false)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === "Escape") setEditingId(false);
              }}
              spellCheck={false}
              className="w-56 rounded-lg border border-white/80 bg-white/60 px-2 py-0.5 font-mono text-[13px] text-slate"
            />
          ) : (
            <button
              type="button"
              onClick={() => setEditingId(true)}
              className="hit group relative flex items-center gap-1 rounded-md px-1 font-mono text-slate underline decoration-inkSoft/50 underline-offset-[3px] transition-colors duration-150 hover:bg-white/50"
            >
              {draft.appId || "set from the name"}
              <Pencil size={11} className="text-ink" aria-hidden />
              <span className="sr-only">edit app id</span>
            </button>
          )}
          {appIdCustom && (
            <button
              type="button"
              onClick={() => {
                setAppIdCustom(false);
                setAppId(slugFromName(draft.name));
              }}
              className="rounded-md px-1 text-ink underline underline-offset-[3px] hover:text-slate"
            >
              match the name
            </button>
          )}
          <span className="basis-full text-xs">
            {draft.appId && !idValid ? (
              <span className="text-danger">
                Start with a lowercase letter or digit; then letters, digits, - or _ (up to 64).
              </span>
            ) : idLive ? (
              <span className="text-danger">An app with this id is already live. Pick another id.</span>
            ) : idPending ? (
              "You compiled an app with this id before; compiling again replaces that proposal."
            ) : (
              "Used in your app's endpoints. It can't change once the app is live."
            )}
          </span>
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <label htmlFor="app-intent" className="text-sm font-semibold text-slate">
          What should it do?
        </label>
        <BigInput
          id="app-intent"
          label="What should the app do?"
          placeholder="describe intent for application"
          value={draft.intent}
          onChange={setIntent}
          onSubmit={next}
          submitHint="to continue"
          describedBy="intent-hint"
        />
        <p id="intent-hint" className="px-3 text-xs text-ink">
          For example: where things gather, where something is happening, and what end users should do about it.
          Shift + Enter adds a line.
        </p>
      </div>

      <StepFooter
        problems={problems}
        readyText="Next, choose the area the app covers."
        next={{ label: "Next: Area", onClick: next }}
      />
    </>
  );
}
