// Past Projects: the client-side record of apps built in this browser (there is no list-apps endpoint).
import { ArrowLeft, FolderOpen } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { dropSpecSnapshot, listProjects, removeProject, type ProjectRecord } from "../api/authorityKey";
import { ProjectCard } from "../components/past-projects/ProjectCard";
import { usePrefersReducedMotion } from "../hooks/usePrefersReducedMotion";

export function PastProjects() {
  const reduced = usePrefersReducedMotion();
  const [projects, setProjects] = useState<ProjectRecord[]>([]);

  useEffect(() => {
    setProjects(listProjects());
  }, []);

  const onRemove = (appId: string) => {
    removeProject(appId);
    dropSpecSnapshot(appId);
    setProjects(listProjects());
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
        transition={{ duration: 0.45, ease: [0.22, 0.8, 0.3, 1] }}
      >
        <Link to="/" className="inline-flex items-center gap-1.5 text-sm text-ink transition hover:text-slate">
          <ArrowLeft size={14} /> Home
        </Link>
        <h1 className="mt-3 text-2xl font-semibold text-slate">Past Projects</h1>
        <p className="mt-1 text-sm text-ink">Apps you've built in this browser. Opening an active one asks for its authority key unless it was remembered.</p>

        {projects.length === 0 ? (
          <div className="mt-8 flex flex-col items-center gap-3 rounded-3xl border border-dashed border-softAqua bg-washedSky/50 px-6 py-16 text-center text-ink">
            <FolderOpen size={28} className="text-mutedTeal" />
            <p className="text-slate">Nothing saved yet.</p>
            <p className="max-w-xs text-sm">Projects appear here after you compile one, or when you press Save Project on its architecture page.</p>
            <Link
              to="/sources"
              className="mt-2 rounded-full bg-[#4F80B0] px-4 py-2 text-sm font-semibold text-cream shadow-sm transition hover:-translate-y-px"
            >
              Start a project
            </Link>
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
                  transition={{ duration: 0.3, delay: reduced ? 0 : i * 0.04 }}
                >
                  <ProjectCard project={p} onRemove={onRemove} />
                </motion.li>
              ))}
            </AnimatePresence>
          </ul>
        )}
      </motion.div>
    </main>
  );
}
