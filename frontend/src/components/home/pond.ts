// Shared conventions for the Home pond scene. Every pond component uses these, so pieces built
// separately line up with each other and with frontend/DESIGN.md §1.
//
// Camera: orthographic, looking straight down at the pond. World units are viewport HEIGHT = 100:
// x runs from -50*aspect (left edge) to +50*aspect (right edge), y from -50 (bottom) to +50 (top),
// origin at the viewport centre. Use pctToWorld() to place things at DESIGN.md's % positions.
import { Vector2, Vector3 } from "three";

/** Size of the cursor-ripple ring buffer the water shader reads (keep in sync with shaders/water.ts). */
export const RIPPLE_COUNT = 8;

/** Map DESIGN.md percentages (x% of width from the left, y% of height from the top) to world x/y. */
export function pctToWorld(xPct: number, yPct: number, aspect: number): [number, number] {
  return [(xPct / 100 - 0.5) * 100 * aspect, (0.5 - yPct / 100) * 100];
}

/** Size as a % of viewport width -> world units. */
export function widthPctToWorld(wPct: number, aspect: number): number {
  return (wPct / 100) * 100 * aspect;
}

/** Draw order (z). Everything under WATER is seen through the water overlay, which tints it. */
export const Z = {
  BED: 0, // pond floor: pebbles (drawn by the bed plane)
  ROCKS: 1,
  SEAWEED: 2,
  KOI: 3,
  WATER: 5, // translucent overlay: tint, caustics, light shaft, refraction feel
  RIPPLES: 6,
  PADS: 7, // floating on the surface: lily pads, leaves
  LOG: 8,
} as const;

/** The calm centre the koi must avoid (title + buttons), in % of the viewport. */
export const CALM_ZONE = { x0: 30, x1: 70, y0: 25, y1: 65 } as const;

/**
 * Mutable scene-wide state, written by PondScene and read inside useFrame (never React state).
 * `koi` holds each koi's head position in world units so the water/ripple layer can ring them.
 */
export const pond = {
  pointer: new Vector2(0, 0), // world units
  pointerActive: false,
  reducedMotion: false,
  mobile: false,
  koi: [new Vector2(), new Vector2()],
  /** Viewport width / height, kept current by PondScene's camera rig. */
  aspect: 16 / 9,
  /** Scene clock in seconds, advanced by PondScene each frame (slowed when reducedMotion). */
  time: 0,
  /** Recent cursor ripples as (world x, world y, pond.time at birth); z < -100 means unused. */
  ripples: Array.from({ length: RIPPLE_COUNT }, () => new Vector3(0, 0, -1000)),
  rippleHead: 0,
};
