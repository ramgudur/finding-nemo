// Persistent chrome: logo always returns to "/" without touching the draft store (so an in-progress
// build resumes), and a static profile indicator -- there is no login system in this platform.
import { User } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

/** Tiny double wave under "nemo" in the logo. */
function LogoWave() {
  return (
    <svg
      className="pointer-events-none absolute left-1/2 top-full mt-[3px] h-[8px] w-[88%] -translate-x-1/2"
      viewBox="0 0 48 9"
      fill="none"
      aria-hidden="true"
    >
      <path d="M1 3 C 5 0.6, 9 0.6, 13 3 S 21 5.4, 25 3 S 33 0.6, 37 3 S 44 5, 47 3" stroke="#5F8FB0" strokeWidth="1.5" strokeLinecap="round" />
      <path d="M7 7 C 11 5, 15 5, 19 7 S 27 9, 31 7 S 38 5.4, 41 6.6" stroke="#5F8FB0" strokeOpacity="0.65" strokeWidth="1.3" strokeLinecap="round" />
    </svg>
  );
}

export function Header() {
  const [health, setHealth] = useState<"ok" | "down" | "checking">("checking");
  // Transparent over the top of a page (and over the home scene); once content scrolls under it the
  // bar gets a solid misty backing so it never sits on top of text.
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 4);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

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
    <header
      className={`fixed inset-x-0 top-0 z-30 flex items-center justify-between px-5 py-5 transition-[background-color,box-shadow,padding] duration-200 ease-out sm:px-8 ${
        scrolled
          ? "bg-mist/90 py-3 shadow-[0_6px_18px_-10px_rgba(53,89,107,0.35)] backdrop-blur-md sm:py-3.5"
          : "pointer-events-none sm:py-6"
      }`}
    >
      <Link
        to="/"
        className="pointer-events-auto relative inline-flex flex-col font-display text-[26px] font-medium leading-none text-slate transition-opacity hover:opacity-80 sm:text-[28px]"
        aria-label="finding nemo, go home"
      >
        <span>
          finding <span className="relative inline-block">nemo<LogoWave /></span>
        </span>
      </Link>
      <div className="pointer-events-auto flex items-center gap-2">
        <span
          title={health === "ok" ? "backend reachable" : health === "down" ? "backend unreachable" : "checking"}
          className={`h-1.5 w-1.5 rounded-full ${health === "ok" ? "bg-seaGlass" : health === "down" ? "bg-coral" : "bg-softStone"}`}
        />
        <button
          type="button"
          className="flex h-10 w-10 items-center justify-center rounded-full border border-white/70 bg-white/55 text-slate shadow-[0_4px_14px_-6px_rgba(47,88,114,0.35)] backdrop-blur-[2px] transition hover:bg-white/70"
          aria-label="profile"
        >
          <User size={18} strokeWidth={2.1} />
        </button>
      </div>
    </header>
  );
}
