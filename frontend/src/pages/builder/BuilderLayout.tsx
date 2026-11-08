// /new/* shell: the watercolor sky, the step track, and a guard so a step is only shown once every step
// before it is complete (a deep link to /new/review with an empty draft lands on the first unfinished step).
import { RotateCcw } from "lucide-react";
import { Navigate, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Stepper } from "../../components/builder/Stepper";
import { useUndoToast } from "../../components/builder/UndoToast";
import { useActiveIds, useDraft } from "../../components/builder/useDraft";
import { STEPS, draftIsEmpty, stepProblems } from "../../components/sources/sourceUtils";
import { useBuilderStore } from "../../store/useBuilderStore";
import type { StepId } from "../../components/sources/sourceUtils";

export function BuilderLayout() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { show, node: toast } = useUndoToast();
  const draft = useDraft();
  const activeIds = useActiveIds();
  const step = STEPS.find((s) => pathname.startsWith(`/new/${s.id}`))?.id ?? null;

  const complete = Object.fromEntries(
    STEPS.map((s) => [s.id, s.id === "review" ? false : stepProblems(s.id, draft, activeIds).length === 0]),
  ) as Record<StepId, boolean>;

  // Clear the whole draft; Undo puts every draft field back.
  const startOver = () => {
    const st = useBuilderStore.getState();
    const saved = {
      name: st.name,
      appId: st.appId,
      appIdCustom: st.appIdCustom,
      intent: st.intent,
      bbox: st.bbox,
      cellM: st.cellM,
      destinationPoints: st.destinationPoints,
      sources: st.sources,
      compiledAt: st.compiledAt,
    };
    st.resetDraft();
    navigate("/new/about");
    show("Draft cleared", () => useBuilderStore.setState(saved));
  };

  if (step) {
    const i = STEPS.findIndex((s) => s.id === step);
    const missing = STEPS.slice(0, i).find((s) => !complete[s.id]);
    if (missing) return <Navigate to={`/new/${missing.id}`} replace />;
  }

  return (
    <main className="sky-wash relative min-h-screen overflow-x-hidden">
      <div aria-hidden className="sky-mist pointer-events-none fixed inset-0 overflow-hidden">
        <span className="mist-a" />
        <span className="mist-b" />
        <span className="mist-c" />
      </div>
      <div aria-hidden className="paper-grain pointer-events-none fixed inset-0" />
      {/* Mist band so content scrolling under the fixed header fades instead of colliding with the logo. */}
      <div
        aria-hidden
        className="pointer-events-none fixed inset-x-0 top-0 z-10 h-20 bg-gradient-to-b from-[#d2e6ec] via-[#d2e6ec]/85 to-transparent"
      />
      <div className="relative mx-auto flex max-w-3xl flex-col gap-6 px-4 pb-[calc(10rem+env(safe-area-inset-bottom))] pt-24 sm:px-6 sm:pt-28">
        {step && (
          <div className="flex items-center gap-2 sm:flex-wrap sm:gap-3">
            <Stepper current={step} complete={complete} />
            {!draftIsEmpty(draft) && (
              <button
                type="button"
                onClick={startOver}
                className="tap ml-auto flex items-center justify-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold text-ink transition-colors duration-150 hover:bg-white/50 hover:text-slate max-sm:px-0"
              >
                <RotateCcw size={13} aria-hidden /> <span className="max-sm:sr-only">Start over</span>
              </button>
            )}
          </div>
        )}
        <div key={pathname} className="step-in flex flex-col gap-6">
          <Outlet />
        </div>
      </div>
      {/* Steps render a fixed StepFooter; the bottom padding above keeps content clear of it. */}
      {toast}
    </main>
  );
}
