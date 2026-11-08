// Shared helpers for the pond props (rocks, seaweed, lily pads, log): GLSL noise, a seeded RNG,
// instanced-geometry construction and a per-frame uniform driver. Painterly look: everything is
// soft-edged and procedurally mottled; nothing here draws hard outlines.
import { useEffect, useMemo, useRef, useState } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import {
  BufferGeometry,
  InstancedBufferAttribute,
  InstancedBufferGeometry,
  type ShaderMaterial,
} from "three";
import { pond } from "../pond";

/** Value noise + fbm (2D). Cheap and smooth; good for watercolor mottling. */
export const NOISE_GLSL = /* glsl */ `
float pHash(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}
float pNoise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  float a = pHash(i);
  float b = pHash(i + vec2(1.0, 0.0));
  float c = pHash(i + vec2(0.0, 1.0));
  float d = pHash(i + vec2(1.0, 1.0));
  return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}
float pFbm(vec2 p) {
  float v = 0.0;
  float a = 0.5;
  for (int i = 0; i < 4; i++) {
    v += a * pNoise(p);
    p = p * 2.03 + vec2(17.1, 9.7);
    a *= 0.5;
  }
  return v;
}
// Watercolor paper granulation: a subtle high-frequency value multiplier around 1.0.
float pGrain(vec2 p) {
  return 0.94 + 0.12 * pFbm(p * 9.0);
}
`;

/** Deterministic RNG (mulberry32) so placements are stable across reloads. */
export function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/**
 * Build an InstancedBufferGeometry sharing `base`'s vertex data, with one per-instance attribute
 * per entry of `attrs` (name -> [itemSize, data]).
 */
export function makeInstanced(
  base: BufferGeometry,
  count: number,
  attrs: Record<string, [number, Float32Array]>,
): InstancedBufferGeometry {
  const g = new InstancedBufferGeometry();
  g.index = base.index;
  for (const [name, attr] of Object.entries(base.attributes)) g.setAttribute(name, attr);
  for (const [name, [size, data]] of Object.entries(attrs)) {
    g.setAttribute(name, new InstancedBufferAttribute(data, size));
  }
  g.instanceCount = count;
  return g;
}

/** Viewport aspect (width / height), the one input every placement depends on. */
export function useAspect(): number {
  const size = useThree((s) => s.size);
  return size.height > 0 ? size.width / size.height : 1;
}

/**
 * pond.mobile is mutable (not React state), so poll it each frame and re-render when it flips.
 * Placements that depend on it recompute then.
 */
export function useMobileFlag(): boolean {
  const [mobile, setMobile] = useState(pond.mobile);
  const ref = useRef(mobile);
  useFrame(() => {
    if (pond.mobile !== ref.current) {
      ref.current = pond.mobile;
      setMobile(pond.mobile);
    }
  });
  return mobile;
}

/**
 * Advance uTime and ease uMotion toward 1 (or near-still under reduced motion) on each material.
 * `still` is the uMotion level used when pond.reducedMotion is set.
 */
export function useDriveUniforms(materials: ShaderMaterial[], still = 0.04): void {
  useFrame((_, dt) => {
    const d = Math.min(dt, 0.1);
    const target = pond.reducedMotion ? still : 1;
    for (const m of materials) {
      const u = m.uniforms;
      if (u.uTime) u.uTime.value += d;
      if (u.uMotion) u.uMotion.value += (target - u.uMotion.value) * Math.min(1, d * 2);
    }
  });
}

/** Dispose a resource when it is replaced or the component unmounts. */
export function useDisposable<T extends { dispose: () => void }>(make: () => T, deps: unknown[]): T {
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const obj = useMemo(make, deps);
  useEffect(() => () => obj.dispose(), [obj]);
  return obj;
}

/** Size multiplier for narrow (portrait) viewports, so props don't swallow a phone screen. */
export function narrowScale(aspect: number): number {
  return Math.min(1, Math.max(0.55, Math.sqrt(aspect / 1.78)));
}
