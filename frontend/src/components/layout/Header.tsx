// Persistent chrome: logo always returns to "/" without touching the draft store (so an in-progress
// build resumes), and a static profile indicator -- there is no login system in this platform.
import { User } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

export function Header() {
  const [health, setHealth] = useState<"ok" | "down" | "checking">("checking");

  useEffect(() => {
    let alive = true;
    fetch("/v1/health")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((b: { status: string }) => alive && setHealth(b.status === "ok" ? "ok" : "down"))
      .catch(() => alive && setHealth("down"));
    return () => {
      alive = false;
    };
  }, []);

  return (
    <header className="pointer-events-none fixed inset-x-0 top-0 z-30 flex items-center justify-between px-6 py-5 sm:px-8">
      <Link
        to="/"
        className="pointer-events-auto font-display text-xl text-slate drop-shadow-sm transition-opacity hover:opacity-80"
        aria-label="finding nemo, go home"
      >
        finding nemo
      </Link>
      <div className="pointer-events-auto flex items-center gap-2">
        <span
          title={health === "ok" ? "backend reachable" : health === "down" ? "backend unreachable" : "checking"}
          className={`h-1.5 w-1.5 rounded-full ${health === "ok" ? "bg-seaGlass" : health === "down" ? "bg-coral" : "bg-softStone"}`}
        />
        <button
          type="button"
          className="flex h-9 w-9 items-center justify-center rounded-full bg-cream/80 text-slate shadow-sm backdrop-blur-sm transition hover:bg-cream"
          aria-label="profile"
        >
          <User size={16} />
        </button>
      </div>
    </header>
  );
}
