// Faint ripple rings around the log's near end (DESIGN.md §1, "Log"): 2-3 thin pale concentric
// ellipses that slowly expand and fade. Mounted inside FloatingLog's group, so it follows the log.
import { useFrame } from "@react-three/fiber";
import { PlaneGeometry, ShaderMaterial } from "three";
import { Z, pond } from "./pond";
import { useDisposable } from "./shaders/propsCommon";

const VERT = /* glsl */ `
varying vec2 vUv;
void main() {
  vUv = position.xy * 2.0;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`;

const FRAG = /* glsl */ `
uniform float uTime;
varying vec2 vUv;
void main() {
  float d = length(vUv);
  float a = 0.0;
  for (int i = 0; i < 3; i++) {
    float ph = fract(uTime * 0.06 + float(i) / 3.0);
    float r = mix(0.3, 1.0, ph);
    float fade = (1.0 - ph) * smoothstep(0.0, 0.12, ph);
    a += exp(-pow((d - r) / 0.018, 2.0)) * fade;
  }
  a *= 1.0 - smoothstep(0.9, 1.0, d);
  if (a <= 0.003) discard;
  gl_FragColor = vec4(1.0, 0.99, 0.94, a * 0.3);
}
`;

const geo = new PlaneGeometry(1, 1);

/** x: centre along the log (local units); rx/ry: ellipse radii in world units. */
export default function LogRipples({ x, rx, ry }: { x: number; rx: number; ry: number }) {
  const material = useDisposable(
    () =>
      new ShaderMaterial({
        vertexShader: VERT,
        fragmentShader: FRAG,
        uniforms: { uTime: { value: 4 } },
        transparent: true,
        depthWrite: false,
      }),
    [],
  );
  // Time only advances while motion is allowed; reduced motion leaves the rings still.
  useFrame((_, dt) => {
    if (!pond.reducedMotion) material.uniforms.uTime!.value += Math.min(dt, 0.1);
  });
  return (
    <mesh
      geometry={geo}
      material={material}
      position={[x, 0, Z.RIPPLES]}
      scale={[rx * 2, ry * 2, 1]}
      frustumCulled={false}
      renderOrder={Z.RIPPLES}
    />
  );
}
