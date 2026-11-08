// Full-viewport translucent water at Z.WATER: tint, sunlight, caustics, grain and ripple rings.
// Everything under it (bed, rocks, seaweed, koi) reads through, tinted.
import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo } from "react";
import { PlaneGeometry, ShaderMaterial, Vector2, Vector3 } from "three";
import { Z, pond } from "./pond";
import { createWaterUniforms, waterFragment, waterVertex } from "./shaders/water";

export default function WaterSurface() {
  const size = useThree((s) => s.size);
  const aspect = size.width / Math.max(1, size.height);
  const geometry = useMemo(() => new PlaneGeometry(1, 1), []);
  const uniforms = useMemo(() => createWaterUniforms(), []);
  const material = useMemo(
    () =>
      new ShaderMaterial({
        vertexShader: waterVertex,
        fragmentShader: waterFragment,
        uniforms,
        transparent: true,
        depthWrite: false,
      }),
    [uniforms],
  );

  useEffect(
    () => () => {
      geometry.dispose();
      material.dispose();
    },
    [geometry, material],
  );

  useFrame(() => {
    uniforms.uTime.value = pond.time;
    uniforms.uAspect.value = pond.aspect;
    const rip = uniforms.uRipples.value as Vector3[];
    for (let i = 0; i < rip.length; i++) {
      const src = pond.ripples[i];
      if (src) rip[i]!.copy(src);
    }
    const koi = uniforms.uKoi.value as Vector2[];
    for (let i = 0; i < 2; i++) {
      const src = pond.koi[i];
      if (src) koi[i]!.copy(src);
    }
  });

  // The plane exactly covers the viewport so vUv maps 0..1 to the screen (sun, shaft, edge tint).
  return (
    <mesh geometry={geometry} material={material} position={[0, 0, Z.WATER]} scale={[100 * aspect, 100, 1]} renderOrder={Z.WATER} />
  );
}
