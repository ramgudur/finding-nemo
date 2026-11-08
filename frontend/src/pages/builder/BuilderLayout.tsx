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
    <main className="bld wp-ground relative min-h-screen overflow-x-hidden">
      {/* Caustic light across the top of the page only (the Home pond's light, seen from the work surface). */}
      <div aria-hidden className="wp-caustic" />
      {/* Water band so content scrolling under the fixed header fades instead of colliding with the logo. */}
      <div
        aria-hidden
        className="pointer-events-none fixed inset-x-0 top-0 z-10 h-20 bg-gradient-to-b from-washedSky via-washedSky/85 to-transparent"
      />
      <div
        className={`relative mx-auto flex flex-col gap-8 px-4 pb-[calc(10rem+env(safe-area-inset-bottom))] pt-24 sm:px-6 sm:pt-28 ${
          step === "sources" ? "max-w-[1120px]" : "max-w-3xl"
        }`}
      >
        {step && (
          <div className="flex items-center gap-2 sm:flex-wrap sm:gap-3">
            <Stepper current={step} complete={complete} />
            {!draftIsEmpty(draft) && (
              <button
                type="button"
                onClick={startOver}
                className="bld-btn bld-btn-quiet bld-btn-sm tap ml-auto max-sm:px-0"
              >
                <RotateCcw size={13} aria-hidden /> <span className="max-sm:sr-only">Start over</span>
              </button>
            )}
          </div>
        )}
        <div key={pathname} className="step-in flex flex-col gap-8">
          <Outlet />
        </div>
      </div>
      {/* Steps render a fixed StepFooter; the bottom padding above keeps content clear of it. */}
      {toast}
    </main>
  );
}
