import { FolderOpen } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listProjects, removeProject, type ProjectRecord } from "../api/authorityKey";
import { ProjectCard } from "../components/past-projects/ProjectCard";

export function PastProjects() {
  const [projects, setProjects] = useState<ProjectRecord[]>([]);

  useEffect(() => {
    setProjects(listProjects());
  }, []);

  const onRemove = (appId: string) => {
    removeProject(appId);
    setProjects(listProjects());
  };

  return (
    <main className="min-h-screen px-6 pb-16 pt-28 sm:px-10">
      <div className="mx-auto max-w-2xl">
        <h1 className="font-display text-2xl text-slate">Past projects</h1>
        <p className="mt-1 text-sm text-ink">Apps you've built on this device.</p>

        {projects.length === 0 ? (
          <div className="mt-8 flex flex-col items-center gap-3 rounded-2xl border border-dashed border-softAqua py-16 text-center text-ink">
            <FolderOpen size={28} className="text-inkSoft" />
            <p>Nothing here yet.</p>
            <Link to="/sources" className="text-sm text-mutedTeal underline underline-offset-2">
              Start a project
            </Link>
          </div>
        ) : (
          <div className="mt-6 flex flex-col gap-3">
            {projects.map((p) => (
              <ProjectCard key={p.appId} project={p} onRemove={onRemove} />
            ))}
          </div>
        )}
      </div>
    </main>
  );
}
