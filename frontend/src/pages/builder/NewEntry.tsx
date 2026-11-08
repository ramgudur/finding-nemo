// /new: start or resume a build. An empty or in-progress draft goes straight to the step where it left off.
// A draft that already finished (compiled, or its app is live) asks first, so "Start Project" never walks
// the dev into a guaranteed 409 by recompiling a live app.
import { ArrowRight, Plus } from "lucide-react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { useActiveIds, useDraft } from "../../components/builder/useDraft";
import { SLUG, draftIsEmpty, firstIncompleteStep } from "../../components/sources/sourceUtils";
import { useBuilderStore } from "../../store/useBuilderStore";

export function NewEntry() {
  const navigate = useNavigate();
  const draft = useDraft();
  const activeIds = useActiveIds();
  const compiledAt = useBuilderStore((s) => s.compiledAt);
  const resetDraft = useBuilderStore((s) => s.resetDraft);

  const live = SLUG.test(draft.appId) && activeIds.includes(draft.appId);
  if (draftIsEmpty(draft)) return <Navigate to="/new/about" replace />;
  if (!live && !compiledAt) return <Navigate to={`/new/${firstIncompleteStep(draft, activeIds)}`} replace />;

  const title = draft.name.trim() || draft.appId;
  const appPath = `/apps/${encodeURIComponent(draft.appId)}`;
  const startNew = () => {
    resetDraft();
    navigate("/new/about");
  };

  return (
    <section aria-labelledby="resume-h" className="bld-card flex flex-col gap-5 px-6 py-6">
      <div className="flex flex-col gap-1.5">
        <h1 id="resume-h" className="bld-title">
          {live ? `${title} is already live` : `Resume ${title}?`}
        </h1>
        <p className="max-w-[60ch] text-[15px] text-ink">
          {live
            ? "A live app can't be compiled again. Open it to watch it run, or start a new app."
            : `You compiled this draft${compiledAt ? ` on ${new Date(compiledAt).toLocaleString()}` : ""}. Resume to change it and compile again, or start a new app.`}
        </p>
        {draft.intent && <p className="line-clamp-2 max-w-[60ch] text-sm italic text-ink">“{draft.intent}”</p>}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        {live ? (
          <Link
            to={appPath}
            className="bld-btn bld-btn-primary tap"
          >
            Open {title} <ArrowRight size={16} aria-hidden />
          </Link>
        ) : (
          <>
            <Link
              to="/new/review"
              className="bld-btn bld-btn-primary tap"
            >
              Resume draft <ArrowRight size={16} aria-hidden />
            </Link>
            <Link
              to={`${appPath}/review`}
              className="bld-btn bld-btn-quiet tap underline underline-offset-[3px]"
            >
              See its proposal
            </Link>
          </>
        )}
        <button
          type="button"
          onClick={startNew}
          className="bld-btn bld-btn-secondary tap"
        >
          <Plus size={16} aria-hidden /> Start new
        </button>
      </div>
    </section>
  );
}
