import { ArrowRight, Trash2 } from "lucide-react";
import { Link } from "react-router-dom";
import type { ProjectRecord } from "../../api/authorityKey";

export function ProjectCard({ project, onRemove }: { project: ProjectRecord; onRemove: (appId: string) => void }) {
  const active = project.status === "active";
  return (
    <div className="group flex items-center justify-between gap-4 rounded-2xl border border-softAqua/60 bg-cream/60 px-5 py-4 shadow-[0_2px_12px_rgba(53,89,107,0.05)] transition hover:-translate-y-px hover:bg-cream/85 hover:shadow-[0_6px_20px_rgba(53,89,107,0.08)]">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <span className="truncate font-mono text-sm font-semibold text-slate">{project.appId}</span>
          <span
            className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold ${
              active ? "bg-seaGlass/40 text-slate" : "bg-washedSky text-ink ring-1 ring-softStone"
            }`}
          >
            {active ? "active" : "pending"}
          </span>
        </div>
        <p className="mt-1 line-clamp-2 text-sm text-ink">{project.intent || "(no intent recorded)"}</p>
        <p className="mt-0.5 font-mono text-[11px] text-inkSoft">saved {new Date(project.updatedAt).toLocaleString()}</p>
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        <button
          type="button"
          onClick={() => onRemove(project.appId)}
          className="rounded-full p-2 text-inkSoft opacity-60 transition hover:bg-coral/10 hover:text-coral focus-visible:opacity-100 group-hover:opacity-100"
          aria-label={`remove ${project.appId} from this list`}
          title="Remove from this list (the app itself is not deleted)"
        >
          <Trash2 size={15} />
        </button>
        <Link
          to={`/architecture/${encodeURIComponent(project.appId)}`}
          className="flex items-center gap-1 rounded-full bg-[#4F80B0] px-3.5 py-1.5 text-sm font-semibold text-cream shadow-sm transition hover:-translate-y-px"
        >
          Open <ArrowRight size={14} />
        </Link>
      </div>
    </div>
  );
}
