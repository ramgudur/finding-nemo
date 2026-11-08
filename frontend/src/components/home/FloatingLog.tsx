// Weathered floating log (DESIGN.md §1, "Log"): lies diagonally in the upper right from about
// (64%, 5%) and runs off the right edge past (100%, 35%). Very subtle bob and slow rotation.
import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { type Group, PlaneGeometry, ShaderMaterial } from "three";
import { Z, pctToWorld, pond } from "./pond";
import { narrowScale, useAspect, useDisposable } from "./shaders/propsCommon";
import { LOG_FRAG, LOG_VERT } from "./shaders/propsLog";

const START: [number, number] = [64, 5];
const THROUGH: [number, number] = [100, 35];
// On portrait screens the same % points would stand the log almost upright; keep it diagonal.
const START_NARROW: [number, number] = [45, 4];
const THROUGH_NARROW: [number, number] = [100, 20];
const SHADOW_OFFSET: [number, number] = [1.4, -1.8];

// x in [0, 1] along the log, y in [-1, 1] across.
const baseGeo = new PlaneGeometry(1, 2, 48, 1).translate(0.5, 0, 0);

export default function FloatingLog() {
  const aspect = useAspect();
  const group = useRef<Group>(null);

  const layout = useMemo(() => {
    const narrow = aspect < 1;
    const a = narrow ? START_NARROW : START;
    const b = narrow ? THROUGH_NARROW : THROUGH;
    const [x0, y0] = pctToWorld(a[0], a[1], aspect);
    const [x1, y1] = pctToWorld(b[0], b[1], aspect);
    const dx = x1 - x0;
    const dy = y1 - y0;
    const dist = Math.hypot(dx, dy);
    const angle = Math.atan2(dy, dx);
    // The shadow offset is fixed in screen space; express it in the log's rotated frame.
    const c = Math.cos(-angle);
    const s = Math.sin(-angle);
    const shadow: [number, number] = [
      c * SHADOW_OFFSET[0] - s * SHADOW_OFFSET[1],
      s * SHADOW_OFFSET[0] + c * SHADOW_OFFSET[1],
    ];
    return {
      x0,
      y0,
      angle,
      shadow,
      len: dist * 1.35, // extends well past the viewport edge
      wid: 4.8 * narrowScale(aspect), // half-width, world units
    };
  }, [aspect]);

  const make = (shadow: boolean) =>
    new ShaderMaterial({
      vertexShader: LOG_VERT,
      fragmentShader: LOG_FRAG,
      defines: shadow ? { SHADOW: "" } : {},
      uniforms: { uLen: { value: 1 }, uWid: { value: 1 }, uShadowAlpha: { value: 0.18 } },
      transparent: true,
      depthWrite: false,
    });
  const mat = useDisposable(() => make(false), []);
  const shadowMat = useDisposable(() => make(true), []);
  for (const m of [mat, shadowMat]) {
    m.uniforms.uLen!.value = layout.len;
    m.uniforms.uWid!.value = layout.wid;
  }

  const t = useRef(0);
  useFrame((_, dt) => {
    const g = group.current;
    if (!g) return;
    if (!pond.reducedMotion) t.current += Math.min(dt, 0.1);
    const s = t.current;
    g.rotation.z = layout.angle + Math.sin(s * 0.09) * 0.012;
    g.position.x = layout.x0 + Math.sin(s * 0.21 + 1.3) * 0.25;
    g.position.y = layout.y0 + Math.sin(s * 0.17) * 0.3;
    g.scale.setScalar(1 + Math.sin(s * 0.5) * 0.004); // bob reads as a tiny swell
  });

  return (
    <group ref={group} position={[layout.x0, layout.y0, 0]} rotation={[0, 0, layout.angle]}>
      <mesh
        geometry={baseGeo}
        material={shadowMat}
        position={[layout.shadow[0], layout.shadow[1], Z.LOG - 0.05]}
        frustumCulled={false}
        renderOrder={Z.LOG - 0.05}
      />
      <mesh geometry={baseGeo} material={mat} position={[0, 0, Z.LOG]} frustumCulled={false} renderOrder={Z.LOG} />
    </group>
  );
}
