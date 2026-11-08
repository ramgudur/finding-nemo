// The two kohaku koi of the Home pond (DESIGN.md §1). Mounted inside PondScene's Canvas.
// Steering runs in useFrame on plain mutable objects: each koi wanders toward targets in its own
// region with a limited, smoothed turn rate, steers well clear of CALM_ZONE and the viewport edges,
// and every 20-40 s one of them swims over to follow the other for a while before they part.
// Each frame the head positions are written to pond.koi[] for the ripple layer.
import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo } from "react";
import { CALM_ZONE, pctToWorld, pond, widthPctToWorld } from "./pond";
import { KoiFish, createKoiGeometry, createKoiMaterial, type KoiState } from "./KoiFish";

type Rect = readonly [x0: number, x1: number, y0: number, y1: number]; // % of viewport

interface Brain {
  home: readonly Rect[];
  baseSpeed: number; // body lengths / s
  maxTurn: number; // rad / s
  tx: number; // current target, world units
  ty: number;
  retarget: number; // seconds until a new wander target
  // following the other koi: "approach" until close (or giving up), then "follow" for a while
  follow: "none" | "approach" | "follow";
  followUntil: number; // elapsed time at which the current follow phase ends
  speedSeed: number;
}

const KOI_LEN_PCT = 8.6;

function mulberry32(seed: number) {
  let a = seed;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Signed distance from p to an axis-aligned box (world units). */
function sdBox(px: number, py: number, cx: number, cy: number, hx: number, hy: number): number {
  const dx = Math.abs(px - cx) - hx;
  const dy = Math.abs(py - cy) - hy;
  return Math.hypot(Math.max(dx, 0), Math.max(dy, 0)) + Math.min(Math.max(dx, dy), 0);
}

function wrapAngle(a: number): number {
  let r = a;
  while (r > Math.PI) r -= 2 * Math.PI;
  while (r < -Math.PI) r += 2 * Math.PI;
  return r;
}

interface Zone {
  cx: number;
  cy: number;
  hx: number;
  hy: number;
}

/** Does segment p->q pass through the zone grown by `m` on every side? (Liang-Barsky clip) */
function segHitsZone(px: number, py: number, qx: number, qy: number, z: Zone, m: number): boolean {
  const dx = qx - px;
  const dy = qy - py;
  let t0 = 0;
  let t1 = 1;
  const edges: [number, number][] = [
    [-dx, px - (z.cx - z.hx - m)],
    [dx, z.cx + z.hx + m - px],
    [-dy, py - (z.cy - z.hy - m)],
    [dy, z.cy + z.hy + m - py],
  ];
  for (const [p, q] of edges) {
    if (p === 0) {
      if (q < 0) return false;
    } else {
      const r = q / p;
      if (p < 0) t0 = Math.max(t0, r);
      else t1 = Math.min(t1, r);
      if (t0 > t1) return false;
    }
  }
  return true;
}

/** Outward unit normal of the zone at p (numerical gradient of the SDF). */
function zoneNormal(px: number, py: number, z: Zone): [number, number] {
  const e = 0.5;
  const gx = sdBox(px + e, py, z.cx, z.cy, z.hx, z.hy) - sdBox(px - e, py, z.cx, z.cy, z.hx, z.hy);
  const gy = sdBox(px, py + e, z.cx, z.cy, z.hx, z.hy) - sdBox(px, py - e, z.cx, z.cy, z.hx, z.hy);
  const gl = Math.hypot(gx, gy) || 1;
  return [gx / gl, gy / gl];
}

export default function KoiPair() {
  const size = useThree((s) => s.size);
  const aspect0 = size.width / Math.max(1, size.height);

  // One geometry shared by both fish; materials share shader source (one program) but each fish
  // needs its own uniforms.
  const res = useMemo(
    () => ({
      geometry: createKoiGeometry(),
      bodies: [createKoiMaterial(0.13, false), createKoiMaterial(0.81, false)] as const,
      shadows: [createKoiMaterial(0.13, true), createKoiMaterial(0.81, true)] as const,
    }),
    [],
  );
  useEffect(
    () => () => {
      res.geometry.dispose();
      for (const m of [...res.bodies, ...res.shadows]) m.dispose();
    },
    [res],
  );

  // Initialised once from the first aspect; later resizes are absorbed by the per-frame bounds.
  const sim = useMemo(() => {
    const L = widthPctToWorld(KOI_LEN_PCT, aspect0);
    const [ax, ay] = pctToWorld(24, 30, aspect0);
    const [bx, by] = pctToWorld(77, 65, aspect0);
    const fish: [KoiState, KoiState] = [
      { x: ax, y: ay, heading: 0.6, speed: 0.3 * L, turnRate: 0, phase: 0, length: L },
      { x: bx, y: by, heading: Math.PI + 0.5, speed: 0.26 * L, turnRate: 0, phase: 2.1, length: L },
    ];
    // initial targets continue the reference headings: A up-right, B left-down
    const [atx, aty] = pctToWorld(30, 14, aspect0);
    const [btx, bty] = pctToWorld(60, 83, aspect0);
    const brains: [Brain, Brain] = [
      {
        home: [
          [7, 24, 16, 62],
          [7, 22, 62, 84],
        ],
        baseSpeed: 0.34,
        maxTurn: 0.55,
        tx: atx,
        ty: aty,
        retarget: 9,
        follow: "none",
        followUntil: 0,
        speedSeed: 1.3,
      },
      {
        home: [
          [76, 92, 40, 84],
          [52, 92, 76, 86],
          [78, 92, 16, 40],
        ],
        baseSpeed: 0.29,
        maxTurn: 0.5,
        tx: btx,
        ty: bty,
        retarget: 10,
        follow: "none",
        followUntil: 0,
        speedSeed: 4.7,
      },
    ];
    return { fish, brains, rng: mulberry32(20261108), nextFollow: 18, amp: { value: 1 } };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useFrame((state, rawDt) => {
    const dt = Math.min(rawDt, 0.05);
    const t = state.clock.elapsedTime;
    const aspect = state.size.width / Math.max(1, state.size.height);
    const L = widthPctToWorld(KOI_LEN_PCT, aspect);
    const reduced = pond.reducedMotion;
    sim.amp.value = reduced ? 0.55 : 1;

    const [zx0, zy0] = pctToWorld(CALM_ZONE.x0, CALM_ZONE.y0, aspect);
    const [zx1, zy1] = pctToWorld(CALM_ZONE.x1, CALM_ZONE.y1, aspect);
    const zone: Zone = {
      cx: (zx0 + zx1) / 2,
      cy: (zy0 + zy1) / 2,
      hx: Math.abs(zx1 - zx0) / 2,
      hy: Math.abs(zy1 - zy0) / 2,
    };
    const zoneDist = (px: number, py: number) => sdBox(px, py, zone.cx, zone.cy, zone.hx, zone.hy);
    const hardMargin = L * 0.3; // the head never gets closer than this (fins stay clear too)
    const softMargin = L * 1.1; // start steering away here
    // gaps between the zone and each viewport edge; route corners sit inside them, never too tight
    const gapL = zone.cx - zone.hx + 50 * aspect;
    const gapR = 50 * aspect - (zone.cx + zone.hx);
    const gapB = zone.cy - zone.hy + 50;
    const gapT = 50 - (zone.cy + zone.hy);
    const routeMargin = Math.min(L * 0.9, Math.min(gapL, gapR, gapB, gapT) * 0.4); // plan around the zone grown by this
    const offL = Math.min(L * 1.3, gapL * 0.5);
    const offR = Math.min(L * 1.3, gapR * 0.5);
    const offB = Math.min(L * 1.3, gapB * 0.5);
    const offT = Math.min(L * 1.3, gapT * 0.5);
    // viewport bounds for the head, with margin
    const bx = 50 * aspect - L * 0.5;
    const by = 50 - L * 0.5;

    // a new wander target in the koi's home; with `near`, the best of a few samples toward that point
    const pickTarget = (b: Brain, near?: KoiState) => {
      let bestD = Infinity;
      for (let n = 0, found = 0; n < 24 && found < (near ? 4 : 1); n++) {
        const r = b.home[Math.floor(sim.rng() * b.home.length)] ?? b.home[0];
        if (!r) break;
        const [wx, wy] = pctToWorld(
          r[0] + sim.rng() * (r[1] - r[0]),
          r[2] + sim.rng() * (r[3] - r[2]),
          aspect,
        );
        if (zoneDist(wx, wy) > L * 1.4) {
          found++;
          const d = near ? Math.hypot(wx - near.x, wy - near.y) : 0;
          if (d < bestD) {
            bestD = d;
            b.tx = wx;
            b.ty = wy;
          }
        }
      }
      b.retarget = 8 + sim.rng() * 8;
    };

    // every 20-40 s the koi drift toward each other (within their own regions); once they are
    // close, one swims over and follows the other for a while
    const seeking = !reduced && t > sim.nextFollow;
    if (seeking) {
      const [b0, b1] = sim.brains;
      const [f0, f1] = sim.fish;
      // only when they are already in neighbouring corners of the pond, so the swim over is short
      const near = Math.hypot(f0.x - f1.x, f0.y - f1.y) < widthPctToWorld(40, aspect);
      if (b0.follow === "none" && b1.follow === "none" && near) {
        const f = sim.rng() < 0.5 ? b0 : b1;
        f.follow = "approach";
        f.followUntil = t + 25;
        sim.nextFollow = t + 20 + sim.rng() * 20;
      } else {
      }
    }

    for (let i = 0; i < 2; i++) {
      const k = sim.fish[i as 0 | 1];
      const b = sim.brains[i as 0 | 1];
      const other = sim.fish[(1 - i) as 0 | 1];
      k.length = L;

      if (b.follow !== "none" && (t > b.followUntil || reduced)) {
        b.follow = "none"; // parting: drift back home
        pickTarget(b);
        sim.nextFollow = Math.max(sim.nextFollow, t + 20 + sim.rng() * 20);
      }
      let speedMul = 1;
      if (b.follow !== "none") {
        b.tx = other.x - Math.cos(other.heading) * L * 1.6;
        b.ty = other.y - Math.sin(other.heading) * L * 1.6;
        const d = Math.hypot(b.tx - k.x, b.ty - k.y);
        if (b.follow === "approach" && d < L * 1.5) {
          b.follow = "follow";
          b.followUntil = t + 10 + sim.rng() * 8;
        }
        speedMul = d > L * 2 ? 1.45 : 0.55 + 0.45 * (d / (L * 2));
      } else {
        b.retarget -= dt;
        // while the other koi swims over, drift toward it within this koi's own region
        const ob = sim.brains[(1 - i) as 0 | 1];
        const drawn = seeking || ob.follow === "approach";
        if (drawn && b.retarget > 4) b.retarget = 4;
        const d = Math.hypot(b.tx - k.x, b.ty - k.y);
        // arrived, or the target sits inside the turning circle (would only orbit it): move on
        const ang = Math.abs(wrapAngle(Math.atan2(b.ty - k.y, b.tx - k.x) - k.heading));
        if (b.retarget <= 0 || d < L * 1.2 || (d < L * 2.5 && ang > 1.6))
          pickTarget(b, drawn ? other : undefined);
      }

      // desired direction: toward the target, or around the calm zone via one of the corners of
      // a widened copy of it when the straight line would cross it (so the koi swing wide)
      let gx = b.tx;
      let gy = b.ty;
      // when already inside the widened zone, test visibility against a margin the koi is outside of
      const rm = Math.min(routeMargin, Math.max(0, zoneDist(k.x, k.y) - L * 0.1));
      if (segHitsZone(k.x, k.y, gx, gy, zone, rm)) {
        let best = Infinity;
        const corners: [number, number][] = [
          [zone.cx - zone.hx - offL, zone.cy - zone.hy - offB],
          [zone.cx + zone.hx + offR, zone.cy - zone.hy - offB],
          [zone.cx + zone.hx + offR, zone.cy + zone.hy + offT],
          [zone.cx - zone.hx - offL, zone.cy + zone.hy + offT],
        ];
        for (let c = 0; c < 4; c++) {
          const [cx, cy] = corners[c]!;
          if (Math.hypot(cx - k.x, cy - k.y) < L * 0.9) continue; // reached this corner already
          if (segHitsZone(k.x, k.y, cx, cy, zone, rm * 0.9)) continue;
          let rest = Math.hypot(b.tx - cx, b.ty - cy);
          if (segHitsZone(cx, cy, b.tx, b.ty, zone, routeMargin * 0.9)) {
            // one more corner needed: go via whichever neighbour is shorter
            rest = Infinity;
            for (const n of [(c + 1) % 4, (c + 3) % 4]) {
              const [nx2, ny2] = corners[n]!;
              rest = Math.min(rest, Math.hypot(nx2 - cx, ny2 - cy) + Math.hypot(b.tx - nx2, b.ty - ny2));
            }
          }
          const cost = Math.hypot(cx - k.x, cy - k.y) + rest;
          if (cost < best) {
            best = cost;
            gx = cx;
            gy = cy;
          }
        }
      }
      let dx = gx - k.x;
      let dy = gy - k.y;
      const dl = Math.hypot(dx, dy) || 1;
      dx /= dl;
      dy /= dl;

      if (b.follow === "follow" || (b.follow === "approach" && Math.hypot(b.tx - k.x, b.ty - k.y) < L * 2)) {
        // close behind the leader: swim alongside its heading instead of circling the target point
        const close = 1 - Math.min(1, Math.hypot(b.tx - k.x, b.ty - k.y) / (L * 2));
        dx += Math.cos(other.heading) * close * 1.5;
        dy += Math.sin(other.heading) * close * 1.5;
      }
      // slow meander so no stretch of the path is ruler-straight
      const meander = 0.45 * Math.sin(t * 0.37 + b.speedSeed * 3.1) + 0.25 * Math.sin(t * 0.83 + b.speedSeed);
      {
        const c = Math.cos(meander);
        const sn = Math.sin(meander);
        const rx = dx * c - dy * sn;
        dy = dx * sn + dy * c;
        dx = rx;
      }

      // calm zone: push out and slide along its edge, on the side the koi already faces
      // look ahead along the heading so the turn starts early and stays gentle
      const lax = k.x + Math.cos(k.heading) * L * 1.1;
      const lay = k.y + Math.sin(k.heading) * L * 1.1;
      const zdNow = zoneDist(k.x, k.y);
      const zdAhead = zoneDist(lax, lay);
      const useAhead = zdAhead < zdNow;
      const zd = Math.min(zdNow, zdAhead);
      if (zd < softMargin) {
        const [nx, ny] = useAhead ? zoneNormal(lax, lay, zone) : zoneNormal(k.x, k.y, zone);
        const w = Math.pow(1 - Math.max(0, zd - hardMargin) / (softMargin - hardMargin), 1.3);
        const into = dx * nx + dy * ny;
        if (into < 0) {
          const c = Math.min(1, w * 2.5);
          dx -= into * nx * c;
          dy -= into * ny * c;
        }
        const hx = Math.cos(k.heading);
        const hy = Math.sin(k.heading);
        const sgn = -hx * ny + hy * nx >= 0 ? 1 : -1;
        dx += nx * w * 1.6 - ny * sgn * w * 0.8;
        dy += ny * w * 1.6 + nx * sgn * w * 0.8;
      }

      // viewport edges
      const edge = L * 0.9;
      // probe the head, a point ahead, and the tail end, so the whole fish stays on screen
      const tlx = k.x - Math.cos(k.heading) * L * 0.85;
      const tly = k.y - Math.sin(k.heading) * L * 0.85;
      for (const [px, py] of [
        [k.x, k.y],
        [lax, lay],
        [tlx, tly],
      ] as const) {
        if (px > bx - edge) dx -= ((px - (bx - edge)) / edge) * 1.6;
        if (px < -bx + edge) dx += ((-bx + edge - px) / edge) * 1.6;
        if (py > by - edge) dy -= ((py - (by - edge)) / edge) * 1.6;
        if (py < -by + edge) dy += ((-by + edge - py) / edge) * 1.6;
      }

      // a little personal space
      const od = Math.hypot(other.x - k.x, other.y - k.y) || 1;
      if (od < L * 1.3) {
        const p = (1 - od / (L * 1.3)) * 2.5;
        dx -= ((other.x - k.x) / od) * p;
        dy -= ((other.y - k.y) / od) * p;
      }

      // turn toward the desired heading with a limited, smoothed turn rate
      const diff = wrapAngle(Math.atan2(dy, dx) - k.heading);
      const maxTurn = b.maxTurn * (reduced ? 0.35 : 1);
      const want = Math.max(-maxTurn, Math.min(maxTurn, diff * 1.1));
      k.turnRate += (want - k.turnRate) * Math.min(1, dt * 1.6);
      k.heading = wrapAngle(k.heading + k.turnRate * dt);

      // gentle speed changes: a slow breathing variation, slower in tight turns
      const wobble =
        0.84 + 0.16 * Math.sin(t * 0.21 + b.speedSeed) + 0.07 * Math.sin(t * 0.53 + b.speedSeed * 2.3);
      const turnSlow = 1 - 0.35 * Math.min(1, Math.abs(k.turnRate) / b.maxTurn);
      const target = b.baseSpeed * L * wobble * turnSlow * speedMul * (reduced ? 0.1 : 1);
      k.speed += (target - k.speed) * Math.min(1, dt * 0.8);

      k.x += Math.cos(k.heading) * k.speed * dt;
      k.y += Math.sin(k.heading) * k.speed * dt;

      // hard guarantees: never inside the calm zone (plus margin), never off-screen
      const zd2 = zoneDist(k.x, k.y);
      if (zd2 < hardMargin) {
        const [nx, ny] = zoneNormal(k.x, k.y, zone);
        k.x += nx * (hardMargin - zd2);
        k.y += ny * (hardMargin - zd2);
      }
      k.x = Math.max(-bx, Math.min(bx, k.x));
      k.y = Math.max(-by, Math.min(by, k.y));
      // keep the tail end on screen too (nudges the head inward if needed)
      const tx2 = k.x - Math.cos(k.heading) * L * 0.85;
      const ty2 = k.y - Math.sin(k.heading) * L * 0.85;
      const lim = 50 * aspect - L * 0.12;
      const limY = 50 - L * 0.12;
      if (tx2 > lim) k.x -= tx2 - lim;
      if (tx2 < -lim) k.x += -lim - tx2;
      if (ty2 > limY) k.y -= ty2 - limY;
      if (ty2 < -limY) k.y += -limY - ty2;

      // tail beat follows swim speed, with a slow idle beat even when nearly still
      const beatHz = reduced ? 0.3 : 0.5 + 1.5 * (k.speed / L);
      k.phase += dt * beatHz * 2 * Math.PI;

      pond.koi[i]?.set(k.x, k.y);
    }
  });

  return (
    <>
      {([0, 1] as const).map((i) => (
        <KoiFish
          key={i}
          state={sim.fish[i]}
          layer={i}
          geometry={res.geometry}
          body={res.bodies[i]}
          shadow={res.shadows[i]}
          amp={sim.amp}
        />
      ))}
    </>
  );
}
