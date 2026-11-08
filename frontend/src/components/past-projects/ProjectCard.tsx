import { ArrowRight, Trash2 } from "lucide-react";
import { Link } from "react-router-dom";
import type { ProjectRecord } from "../../api/authorityKey";

export function ProjectCard({ project, onRemove }: { project: ProjectRecord; onRemove: (appId: string) => void }) {
  return (
    <div className="group flex items-center justify-between gap-4 rounded-2xl border border-softAqua/60 bg-white/50 px-5 py-4 shadow-sm backdrop-blur-sm transition hover:bg-white/70">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="truncate font-mono text-sm font-medium text-slate">{project.appId}</span>
          <span
            className={`rounded-full px-2 py-0.5 text-xs ${
              project.status === "active" ? "bg-seaGlass/30 text-mutedTeal" : "bg-cream text-ink"
            }`}
          >
            {project.status === "active" ? "active" : "proposed"}
          </span>
        </div>
        <p className="mt-1 truncate text-sm text-ink">{project.intent || "(no intent recorded)"}</p>
        <p className="mt-0.5 font-mono text-xs text-inkSoft">{new Date(project.updatedAt).toLocaleString()}</p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <button
          type="button"
          onClick={() => onRemove(project.appId)}
          className="rounded-full p-2 text-inkSoft opacity-0 transition hover:bg-coral/10 hover:text-coral group-hover:opacity-100"
          aria-label={`remove ${project.appId}`}
        >
          <Trash2 size={15} />
        </button>
        <Link
          to={`/architecture/${encodeURIComponent(project.appId)}`}
          className="flex items-center gap-1 rounded-full bg-slate/90 px-3 py-1.5 text-sm text-cream transition hover:bg-slate"
        >
          Open <ArrowRight size={14} />
        </Link>
      </div>
    </div>
  );
}
