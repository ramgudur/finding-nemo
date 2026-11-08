// Past Projects: the client-side record of apps built in this browser (there is no list-apps endpoint).
import { ArrowLeft, FolderOpen } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  dropSpecSnapshot,
  listProjects,
  loadSpecSnapshot,
  recordProject,
  removeProject,
  saveSpecSnapshot,
  type ProjectRecord,
} from "../api/authorityKey";
import { useUndoToast } from "../components/builder/UndoToast";
import { ProjectCard } from "../components/past-projects/ProjectCard";
import { usePrefersReducedMotion } from "../hooks/usePrefersReducedMotion";

export function PastProjects() {
  const reduced = usePrefersReducedMotion();
  const [projects, setProjects] = useState<ProjectRecord[]>([]);
  const { show, node: toast } = useUndoToast();

  useEffect(() => {
    setProjects(listProjects());
  }, []);

  // Removes from this list only (the app on the server is untouched); Undo restores the record and the
  // pending-spec snapshot exactly as they were.
  const onRemove = (appId: string) => {
    const record = projects.find((p) => p.appId === appId);
    if (!record) return;
    const snapshot = loadSpecSnapshot(appId);
    removeProject(appId);
    dropSpecSnapshot(appId);
    setProjects(listProjects());
    show(`Removed ${record.name?.trim() || appId}`, () => {
      recordProject(record);
      if (snapshot) saveSpecSnapshot(snapshot);
      setProjects(listProjects());
    });
  };

  return (
    <main className="watercolor-grain relative min-h-screen overflow-x-hidden px-4 pb-16 pt-24 sm:px-10">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(900px 420px at 15% -5%, rgba(240,235,221,0.7), transparent 60%), radial-gradient(700px 500px at 100% 40%, rgba(169,210,213,0.4), transparent 65%)",
        }}
      />
      <motion.div
        className="relative mx-auto max-w-2xl"
        initial={reduced ? false : { opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
      >
        <Link to="/" className="inline-flex items-center gap-1.5 rounded-md text-sm text-ink transition-colors duration-150 hover:text-slate">
          <ArrowLeft size={14} /> Home
        </Link>
        <div className="mt-3 flex flex-wrap items-end gap-3">
          <h1 className="text-2xl font-semibold text-slate">Past Projects</h1>
          <Link
            to="/new"
            className="ml-auto rounded-full bg-primary px-4 py-2 text-sm font-semibold text-cream shadow-sm transition-colors duration-150 hover:bg-primaryHover"
          >
            Start a project
          </Link>
        </div>
        <p className="mt-1 text-sm text-ink">
          Apps you've built in this browser. Opening a live one asks for its authority key unless you chose to remember it.
        </p>

        {projects.length === 0 ? (
          <div className="mt-8 flex flex-col items-center gap-3 rounded-3xl border border-dashed border-softAqua bg-washedSky/50 px-6 py-16 text-center text-ink">
            <FolderOpen size={28} className="text-mutedTeal" />
            <p className="text-slate">Nothing saved yet.</p>
            <p className="max-w-xs text-sm">A project appears here as soon as you compile it.</p>
          </div>
        ) : (
          <ul className="mt-6 flex flex-col gap-3">
            <AnimatePresence initial={!reduced}>
              {projects.map((p, i) => (
                <motion.li
                  key={p.appId}
                  layout={!reduced}
                  initial={reduced ? false : { opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={reduced ? { opacity: 0 } : { opacity: 0, x: 24 }}
                  transition={{ duration: 0.22, delay: reduced ? 0 : i * 0.03 }}
                >
                  <ProjectCard project={p} onRemove={onRemove} />
                </motion.li>
              ))}
            </AnimatePresence>
          </ul>
        )}
      </motion.div>
      {toast}
    </main>
  );
}
