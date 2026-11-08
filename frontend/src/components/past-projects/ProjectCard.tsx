// One app built in this browser: its name (or id), what it's for, whether it's live, and where it opens.
// A proposed app opens its review; a live one opens its console.
import { ArrowRight, Trash2 } from "lucide-react";
import { Link } from "react-router-dom";
import type { ProjectRecord } from "../../api/authorityKey";

export function ProjectCard({ project, onRemove }: { project: ProjectRecord; onRemove: (appId: string) => void }) {
  const active = project.status === "active";
  const title = project.name?.trim() || project.appId;
  const to = `/apps/${encodeURIComponent(project.appId)}${active ? "" : "/review"}`;
  return (
    <div className="group relative flex items-center justify-between gap-4 rounded-2xl border border-softAqua/60 bg-cream/65 px-5 py-4 shadow-[0_2px_12px_rgba(53,89,107,0.05)] transition-[background-color,box-shadow] duration-200 focus-within:bg-cream/90 hover:bg-cream/90 hover:shadow-[0_6px_20px_rgba(53,89,107,0.08)]">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="truncate text-[16px] font-semibold text-slate">{title}</h2>
          <span
            className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold ${
              active ? "bg-seaGlass/40 text-slate" : "bg-washedSky text-ink ring-1 ring-softStone"
            }`}
          >
            {active ? "Live" : "Awaiting your review"}
          </span>
        </div>
        {project.name?.trim() && <p className="font-mono text-[11px] text-ink">{project.appId}</p>}
        <p className="mt-1 line-clamp-2 max-w-[60ch] text-sm text-ink">{project.intent || "No intent recorded."}</p>
        <p className="mt-0.5 text-[11px] text-ink">Updated {new Date(project.updatedAt).toLocaleString()}</p>
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        <button
          type="button"
          onClick={() => onRemove(project.appId)}
          className="rounded-full p-2 text-ink transition-colors duration-150 hover:bg-white/60 hover:text-danger"
          aria-label={`Remove ${title} from this list`}
          title="Remove from this list (the app itself is not deleted)"
        >
          <Trash2 size={15} aria-hidden />
        </button>
        <Link
          to={to}
          className="flex items-center gap-1 rounded-full bg-primary px-3.5 py-1.5 text-sm font-semibold text-cream shadow-sm transition-colors duration-150 hover:bg-primaryHover"
        >
          {active ? "Open" : "Review"} <ArrowRight size={14} aria-hidden />
          <span className="sr-only"> {title}</span>
        </Link>
      </div>
    </div>
  );
}
