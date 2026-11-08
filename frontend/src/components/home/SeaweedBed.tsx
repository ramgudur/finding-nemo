// Seaweed framing (DESIGN.md §1, "Seaweed"): the left edge from the bottom-left up to about y 30%,
// a few from the bottom centre (x 33-42%), and the right edge from the bottom-right up to the
// top-right, behind the log. Seen from above, blades lie along the water and wave sideways.
import { useMemo } from "react";
import { pctToWorld } from "./pond";
import Seaweed, { type Blade } from "./Seaweed";
import { lerp, narrowScale, rng, useAspect, useMobileFlag } from "./shaders/propsCommon";

const DEG = Math.PI / 180;

function makeBlades(aspect: number, mobile: boolean): Blade[] {
  const r = rng(20261108);
  // Blades shrink less than other props on phones, or they vanish into the edges.
  const k = Math.max(0.8, narrowScale(aspect));
  const out: Blade[] = [];
  const push = (xPct: number, yPct: number, rot: number, len: number) => {
    const [x, y] = pctToWorld(xPct, yPct, aspect);
    out.push({
      x,
      y,
      rot,
      len: len * k,
      width: lerp(4.0, 7.0, r()) * k,
      curl: lerp(-0.4, 0.4, r()),
      phase: r() * Math.PI * 2,
      twist: lerp(2.0, 5.0, r()),
      speed: lerp(0.35, 0.85, r()),
      amp: lerp(0.05, 0.12, r()),
      tone: r(),
      alpha: lerp(0.6, 0.85, r()),
    });
  };

  // Left edge: rising from the bottom-left; lower blades lean further into the pond.
  const left = 24;
  for (let i = 0; i < left; i++) {
    const f = i / (left - 1); // 0 = bottom, 1 = top of the band
    const y = lerp(108, 34, f) + lerp(-3, 3, r());
    const x = lerp(-4, 3, r()) + (1 - f) * lerp(0, 8, r());
    const lean = lerp(-70, -25, f) + lerp(-12, 12, r());
    push(x, y, lean * DEG, lerp(20, 42, r()) * lerp(1.1, 0.7, f));
  }
  // Bottom centre (x 33-42%).
  for (let i = 0; i < 7; i++) {
    const x = lerp(33, 42, i / 6) + lerp(-1, 1, r());
    push(x, lerp(102, 108, r()), lerp(-22, 22, r()) * DEG, lerp(14, 26, r()));
  }
  // Right edge: bottom-right up to the top-right (behind the log). Upper blades hang down-left.
  const right = 24;
  for (let i = 0; i < right; i++) {
    const f = i / (right - 1); // 0 = bottom, 1 = top
    const y = lerp(108, -6, f) + lerp(-3, 3, r());
    const x = lerp(97, 104, r());
    const lean = f < 0.62 ? lerp(25, 70, f / 0.62) : lerp(85, 140, (f - 0.62) / 0.38);
    push(x, y, (lean + lerp(-12, 12, r())) * DEG, lerp(20, 40, r()));
  }

  // Mobile: about half the blades (every other one keeps the framing even).
  return mobile ? out.filter((_, i) => i % 2 === 0) : out;
}

export default function SeaweedBed() {
  const aspect = useAspect();
  const mobile = useMobileFlag();
  const blades = useMemo(() => makeBlades(aspect, mobile), [aspect, mobile]);
  return <Seaweed blades={blades} />;
}
