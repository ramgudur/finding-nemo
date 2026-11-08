// Hero UI as HTML over the pond (DESIGN.md §2): title, double wave mark, two pill buttons.
// The wrapper ignores pointer events so the pond still sees the cursor; only the buttons take clicks.
import { ArrowRight, Folder } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { useNavigate } from "react-router-dom";

const spring = { type: "spring", stiffness: 380, damping: 26, mass: 0.7 } as const;

export function WaveMark({ className = "" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 140 26" fill="none" aria-hidden="true">
      <path
        d="M4 8 C 15 2, 26 2, 37 8 S 59 14, 70 8 S 92 2, 103 8 S 125 14, 136 8"
        stroke="#5F8FB0"
        strokeWidth="3"
        strokeLinecap="round"
      />
      <path
        d="M18 19 C 28 14, 38 14, 48 19 S 68 24, 78 19 S 98 14, 108 19 S 118 22, 122 20"
        stroke="#5F8FB0"
        strokeOpacity="0.7"
        strokeWidth="2.6"
        strokeLinecap="round"
      />
    </svg>
  );
}

export default function HomeOverlay() {
  const navigate = useNavigate();
  const reduce = useReducedMotion();
  const hover = reduce ? undefined : { y: -3, transition: spring };
  const tap = reduce ? undefined : { y: 0, scale: 0.97, transition: spring };

  return (
    <div className="pointer-events-none absolute inset-0 z-10 select-none">
      <div className="absolute inset-x-0 top-[38%] flex -translate-y-1/2 flex-col items-center px-4">
        <motion.h1
          initial={reduce ? false : { opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }}
          className="font-display font-medium leading-none tracking-[-0.01em] text-[#2F5872]"
          style={{
            fontSize: "clamp(3.25rem, 6.6vw, 7.5rem)",
            textShadow: "0 1px 0 rgba(255,255,255,0.35), 0 6px 28px rgba(240,250,250,0.55)",
          }}
        >
          finding nemo
        </motion.h1>
        <WaveMark className="mt-[clamp(12px,1.3vw,24px)] h-auto w-[clamp(110px,14vw,220px)]" />
      </div>

      <motion.div
        initial={reduce ? false : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.9, delay: 0.15, ease: [0.22, 1, 0.36, 1] }}
        className="absolute inset-x-0 top-[57%] flex -translate-y-1/2 flex-wrap items-center justify-center gap-x-[clamp(12px,3vw,56px)] gap-y-3 px-4"
      >
        <motion.button
          type="button"
          whileHover={hover}
          whileTap={tap}
          onClick={() => navigate("/new")}
          className="pointer-events-auto inline-flex items-center gap-2.5 rounded-full border border-white/60 bg-primary px-[clamp(20px,2vw,34px)] py-[clamp(11px,1vw,16px)] font-sans text-[clamp(15px,1.15vw,19px)] font-semibold text-[#FBF8F0] shadow-[0_8px_24px_-10px_rgba(47,88,114,0.55),inset_0_1px_0_rgba(255,255,255,0.25)] outline-none transition-[background-color,box-shadow] hover:bg-primaryHover hover:shadow-[0_14px_30px_-12px_rgba(47,88,114,0.6),inset_0_1px_0_rgba(255,255,255,0.25)] focus-visible:ring-2 focus-visible:ring-white/80 focus-visible:ring-offset-2 focus-visible:ring-offset-primary"
        >
          Start Project
          <ArrowRight size={18} strokeWidth={2.4} aria-hidden="true" />
        </motion.button>
        <motion.button
          type="button"
          whileHover={hover}
          whileTap={tap}
          onClick={() => navigate("/projects")}
          className="pointer-events-auto inline-flex items-center gap-2.5 rounded-full border border-white/70 bg-[#F3EFE4]/85 px-[clamp(20px,2vw,34px)] py-[clamp(11px,1vw,16px)] font-sans text-[clamp(15px,1.15vw,19px)] font-semibold text-[#3F6378] shadow-[0_8px_24px_-12px_rgba(47,88,114,0.4)] outline-none backdrop-blur-[2px] transition-shadow hover:shadow-[0_14px_30px_-14px_rgba(47,88,114,0.45)] focus-visible:ring-2 focus-visible:ring-[#4F80B0]/60 focus-visible:ring-offset-2 focus-visible:ring-offset-transparent"
        >
          <Folder size={18} strokeWidth={2.2} className="text-[#4F80B0]" aria-hidden="true" />
          Past Projects
        </motion.button>
      </motion.div>
    </div>
  );
}
