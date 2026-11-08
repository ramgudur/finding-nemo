// Page frame for Architecture: a header strip, the two panels (heatmap left, layer stack right), the
// connector-diagram band and the footer. Faded watercolor console, never dark. Panels stack below lg.
import { motion, type Variants } from "motion/react";
import type { ReactNode } from "react";
import { usePrefersReducedMotion } from "../../hooks/usePrefersReducedMotion";

interface Props {
  header: ReactNode;
  left: ReactNode;
  right: ReactNode;
  diagram: ReactNode;
  footer: ReactNode;
  overlay?: ReactNode;
}

export default function ArchitectureLayout({ header, left, right, diagram, footer, overlay }: Props) {
  const reduced = usePrefersReducedMotion();
  const page: Variants = {
    hidden: reduced ? { opacity: 0 } : { opacity: 0, x: 28 },
    show: {
      opacity: 1,
      x: 0,
      transition: { duration: reduced ? 0.01 : 0.5, ease: [0.22, 0.8, 0.3, 1], staggerChildren: reduced ? 0 : 0.11, delayChildren: reduced ? 0 : 0.08 },
    },
  };
  const item: Variants = {
    hidden: reduced ? { opacity: 0 } : { opacity: 0, y: 14 },
    show: { opacity: 1, y: 0, transition: { duration: reduced ? 0.01 : 0.55, ease: [0.22, 0.8, 0.3, 1] } },
  };

  return (
    <div className="watercolor-grain relative min-h-screen overflow-x-hidden">
      {/* atmospheric wash behind the console: soft mist, light from the upper left */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(1200px 520px at 12% -6%, rgba(240,235,221,0.75), transparent 60%), radial-gradient(900px 600px at 100% 30%, rgba(169,210,213,0.45), transparent 65%), linear-gradient(180deg, #DCEEF1 0%, #D5E8EB 55%, #DCEEF1 100%)",
        }}
      />
      <motion.main
        variants={page}
        initial={reduced ? false : "hidden"}
        animate="show"
        className="relative mx-auto flex max-w-[1440px] flex-col gap-5 px-4 pb-10 pt-24 sm:px-8"
      >
        <motion.div variants={item}>{header}</motion.div>
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1.08fr)_minmax(0,1fr)]">
          <motion.section variants={item} aria-label="Heatmap" className="min-w-0">
            {left}
          </motion.section>
          <motion.section variants={item} aria-label="Layer stack" className="min-w-0">
            {right}
          </motion.section>
        </div>
        <motion.section
          variants={item}
          aria-label="Data flow"
          className="rounded-3xl border border-softAqua/60 bg-washedSky/55 p-4 shadow-[0_6px_30px_rgba(53,89,107,0.07)] backdrop-blur-[2px] sm:p-6"
        >
          {diagram}
        </motion.section>
        <motion.div variants={item}>{footer}</motion.div>
      </motion.main>
      {overlay}
    </div>
  );
}
