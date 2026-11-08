// Lily pads and tiny floating leaves on the water surface (DESIGN.md §1, "Lily pads"), each with a
// faint soft shadow offset onto the water. Very slow idle bob and rotation.
import { useMemo } from "react";
import { PlaneGeometry, ShaderMaterial } from "three";
import { Z, pctToWorld, pond } from "./pond";
import { makeInstanced, narrowScale, useAspect, useDisposable, useDriveUniforms } from "./shaders/propsCommon";
import { PAD_FRAG, PAD_VERT } from "./shaders/propsPad";

// [x%, y%, radius (world units = % of height), rotation (rad)]
const PADS: [number, number, number, number][] = [
  // top-left cluster (x 0-22%, y 5-25%), the first partly off-screen
  [1, 9, 8, 2.4],
  [11, 5, 5.5, -0.9],
  [16, 14, 4.6, 0.7],
  [6, 21, 5.2, -2.6],
  // beside the log
  [83, 27, 5, 1.9],
  // bottom-left
  [9, 88, 6, -1.4],
  [23, 83, 4.4, 2.9],
  // bottom-right, larger, partly off-screen
  [93, 80, 8.5, 0.3],
  [101, 95, 10, -2.1],
  [85, 97, 7, 1.2],
];

// Tiny floating leaves: [x%, y%, length, rotation]
const LEAVES: [number, number, number, number][] = [
  [21, 52, 1.7, 0.6],
  [88, 49, 1.5, -0.8],
  [79, 88, 1.6, 2.2],
  [62, 73, 1.3, -2.4],
];

const SHADOW_OFFSET: [number, number] = [1.1, -1.4];
const baseGeo = new PlaneGeometry(1, 1);

function makeMaterial(defines: Record<string, string>, shadow: boolean) {
  return new ShaderMaterial({
    vertexShader: PAD_VERT,
    fragmentShader: PAD_FRAG,
    defines: shadow ? { ...defines, SHADOW: "" } : defines,
    uniforms: {
      uTime: { value: 0 },
      uMotion: { value: pond.reducedMotion ? 0.04 : 1 },
      uOffset: { value: shadow ? SHADOW_OFFSET : [0, 0] },
      uShadowAlpha: { value: 0.16 },
    },
    transparent: true,
    depthWrite: false,
  });
}

export default function LilyPads() {
  const aspect = useAspect();
  const padMat = useDisposable(() => makeMaterial({}, false), []);
  const padShadowMat = useDisposable(() => makeMaterial({}, true), []);
  const leafMat = useDisposable(() => makeMaterial({ LEAF: "" }, false), []);
  const leafShadowMat = useDisposable(() => makeMaterial({ LEAF: "" }, true), []);
  const materials = useMemo(
    () => [padMat, padShadowMat, leafMat, leafShadowMat],
    [padMat, padShadowMat, leafMat, leafShadowMat],
  );
  useDriveUniforms(materials);

  const padGeo = useDisposable(() => {
    const k = narrowScale(aspect);
    const xf = new Float32Array(PADS.length * 4);
    const meta = new Float32Array(PADS.length * 4);
    PADS.forEach(([x, y, r, rot], i) => {
      const [wx, wy] = pctToWorld(x, y, aspect);
      xf.set([wx, wy, rot, r * k], i * 4);
      meta.set([(i * 0.381 + 0.2) % 1, i * 1.7, 0.32 + 0.06 * ((i * 7) % 3), 0.35], i * 4);
    });
    return makeInstanced(baseGeo, PADS.length, { aXform: [4, xf], aMeta: [4, meta] });
  }, [aspect]);

  const leafGeo = useDisposable(() => {
    const k = narrowScale(aspect);
    const xf = new Float32Array(LEAVES.length * 4);
    const meta = new Float32Array(LEAVES.length * 4);
    LEAVES.forEach(([x, y, r, rot], i) => {
      const [wx, wy] = pctToWorld(x, y, aspect);
      xf.set([wx, wy, rot, r * k], i * 4);
      meta.set([(i * 0.53 + 0.4) % 1, i * 2.3 + 1, 0, 1.2], i * 4);
    });
    return makeInstanced(baseGeo, LEAVES.length, { aXform: [4, xf], aMeta: [4, meta] });
  }, [aspect]);

  const zs = Z.PADS - 0.05;
  return (
    <group>
      <mesh geometry={padGeo} material={padShadowMat} position={[0, 0, zs]} frustumCulled={false} renderOrder={zs} />
      <mesh geometry={leafGeo} material={leafShadowMat} position={[0, 0, zs]} frustumCulled={false} renderOrder={zs} />
      <mesh geometry={padGeo} material={padMat} position={[0, 0, Z.PADS]} frustumCulled={false} renderOrder={Z.PADS} />
      <mesh geometry={leafGeo} material={leafMat} position={[0, 0, Z.PADS]} frustumCulled={false} renderOrder={Z.PADS} />
    </group>
  );
}
