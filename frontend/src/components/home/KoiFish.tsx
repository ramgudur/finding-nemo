// One koi seen from directly above: a soft drop shadow plus the painted, bending body strip.
// All motion comes from the mutable KoiState that KoiPair steers; this component only copies it
// into transforms and shader uniforms each frame (no React state per frame).
import { useFrame } from "@react-three/fiber";
import { useRef } from "react";
import { DoubleSide, PlaneGeometry, ShaderMaterial, type Mesh } from "three";
import { Z } from "./pond";
import { koiFragment, koiVertex } from "./shaders/koi";

export interface KoiState {
  x: number; // head position, world units
  y: number;
  heading: number; // radians, 0 = +x
  speed: number; // world units / s
  turnRate: number; // rad / s (+ = left)
  phase: number; // swim phase, radians
  length: number; // world units
}

/** Shared by both fish: a unit strip with many lengthwise segments for a smooth bend. */
export function createKoiGeometry(): PlaneGeometry {
  return new PlaneGeometry(1, 0.5, 72, 8);
}

export function createKoiMaterial(seed: number, shadow: boolean): ShaderMaterial {
  return new ShaderMaterial({
    vertexShader: koiVertex,
    fragmentShader: koiFragment,
    uniforms: {
      uTime: { value: 0 },
      uPhase: { value: 0 },
      uCurv: { value: 0 },
      uAmp: { value: 1 },
      uSeed: { value: seed },
      uShadow: { value: shadow ? 1 : 0 },
    },
    transparent: true,
    depthWrite: false,
    side: DoubleSide,
  });
}

// The strip's centre sits this far behind the head (in body lengths).
const CENTRE_BEHIND_HEAD = 0.46;

interface Props {
  state: KoiState;
  /** small z lift so the two koi always overlap in the same order */
  layer: number;
  geometry: PlaneGeometry;
  body: ShaderMaterial;
  shadow: ShaderMaterial;
  /** swim wave amplitude scale (reduced motion lowers it) */
  amp: { value: number };
}

export function KoiFish({ state, layer, geometry, body, shadow, amp }: Props) {
  const bodyRef = useRef<Mesh>(null);
  const shadowRef = useRef<Mesh>(null);

  useFrame(({ clock }) => {
    const b = bodyRef.current;
    const s = shadowRef.current;
    if (!b || !s) return;
    const L = state.length;
    const c = Math.cos(state.heading);
    const sn = Math.sin(state.heading);
    const cx = state.x - c * L * CENTRE_BEHIND_HEAD;
    const cy = state.y - sn * L * CENTRE_BEHIND_HEAD;
    // curvature of the path in body lengths; the body follows the arc it just swam
    const curv = (state.turnRate / Math.max(state.speed, L * 0.25)) * L;
    const curvC = Math.max(-1.8, Math.min(1.8, curv));
    for (const m of [body, shadow]) {
      const u = m.uniforms;
      u.uTime!.value = clock.elapsedTime;
      u.uPhase!.value = state.phase;
      u.uCurv!.value = curvC;
      u.uAmp!.value = amp.value;
    }
    b.position.set(cx, cy, Z.KOI + layer * 0.1);
    b.rotation.z = state.heading;
    b.scale.setScalar(L);
    // soft shadow: offset down-right as if lit from the upper left, drawn just below the fish
    s.position.set(cx + L * 0.07, cy - L * 0.1, Z.KOI - 0.3 + layer * 0.01);
    s.rotation.z = state.heading;
    s.scale.setScalar(L * 1.04);
  });

  return (
    <>
      <mesh ref={shadowRef} geometry={geometry} material={shadow} />
      <mesh ref={bodyRef} geometry={geometry} material={body} />
    </>
  );
}
