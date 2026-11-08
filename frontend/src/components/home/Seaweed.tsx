// Seaweed blades: one instanced draw of translucent, tapered ribbons with a vertex-shader sway.
// Placement lives in SeaweedBed.tsx (the component PondScene mounts); this file only renders.
import { useMemo } from "react";
import { PlaneGeometry, ShaderMaterial } from "three";
import { Z } from "./pond";
import { makeInstanced, useDisposable, useDriveUniforms } from "./shaders/propsCommon";
import { SEAWEED_FRAG, SEAWEED_VERT } from "./shaders/propsSeaweed";
import { pond } from "./pond";

export interface Blade {
  x: number; // base, world units
  y: number;
  rot: number; // radians; 0 = growing toward +y
  len: number; // world units
  width: number;
  curl: number; // signed sideways bend, as a fraction of length
  phase: number;
  twist: number;
  speed: number;
  amp: number; // sway amplitude, fraction of length
  tone: number; // 0..1 colour offset
  alpha: number;
}

const baseGeo = new PlaneGeometry(1, 1, 1, 28).translate(0, 0.5, 0);

export default function Seaweed({ blades }: { blades: Blade[] }) {
  const material = useDisposable(
    () =>
      new ShaderMaterial({
        vertexShader: SEAWEED_VERT,
        fragmentShader: SEAWEED_FRAG,
        uniforms: { uTime: { value: 0 }, uMotion: { value: pond.reducedMotion ? 0.04 : 1 } },
        transparent: true,
        depthWrite: false,
      }),
    [],
  );
  const materials = useMemo(() => [material], [material]);
  useDriveUniforms(materials);

  const geometry = useDisposable(() => {
    const n = blades.length;
    const xf = new Float32Array(n * 4);
    const bl = new Float32Array(n * 4);
    const mv = new Float32Array(n * 4);
    blades.forEach((b, i) => {
      xf.set([b.x, b.y, b.rot, b.len], i * 4);
      bl.set([b.width, b.curl, b.phase, b.twist], i * 4);
      mv.set([b.speed, b.amp, b.tone, b.alpha], i * 4);
    });
    return makeInstanced(baseGeo, n, { aXform: [4, xf], aBlade: [4, bl], aMove: [4, mv] });
  }, [blades]);

  return (
    <mesh
      geometry={geometry}
      material={material}
      position={[0, 0, Z.SEAWEED]}
      frustumCulled={false}
      renderOrder={Z.SEAWEED}
    />
  );
}
