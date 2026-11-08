// Full-viewport pond floor at Z.BED: procedural cobblestones seen through clear, moving water.
import { useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import { PlaneGeometry, ShaderMaterial } from "three";
import { Z, pond } from "./pond";
import { bedFragment, bedVertex, createBedUniforms } from "./shaders/bed";

export default function PondBed() {
  const size = useThree((s) => s.size);
  const aspect = size.width / Math.max(1, size.height);
  const geometry = useMemo(() => new PlaneGeometry(1, 1), []);
  const material = useMemo(
    () =>
      new ShaderMaterial({
        vertexShader: bedVertex,
        fragmentShader: bedFragment,
        uniforms: createBedUniforms(),
        depthWrite: false,
      }),
    [],
  );
  const uTime = useRef(material.uniforms.uTime!);

  useEffect(
    () => () => {
      geometry.dispose();
      material.dispose();
    },
    [geometry, material],
  );

  useFrame(() => {
    uTime.current.value = pond.time;
  });

  // Slightly oversized so the refraction wobble never shows an edge.
  return (
    <mesh geometry={geometry} material={material} position={[0, 0, Z.BED]} scale={[104 * aspect, 104, 1]} renderOrder={0} />
  );
}
