// Mossy grey-green boulders on the pond bed (DESIGN.md §1, "Pond bed"). Static; the water overlay
// above (Z.WATER) tints them, so they stay mid-tone.
import { useMemo } from "react";
import { PlaneGeometry, ShaderMaterial } from "three";
import { Z, pctToWorld } from "./pond";
import { makeInstanced, narrowScale, useAspect, useDisposable } from "./shaders/propsCommon";
import { ROCK_FRAG, ROCK_VERT } from "./shaders/propsRock";

// [x%, y%, radius (world units = % of height), squashX, squashY, rotation (rad)]
const ROCKS: [number, number, number, number, number, number][] = [
  // bottom-left cluster (x 0-30%, y 60-100%)
  [4, 74, 13, 1.15, 0.9, 0.4],
  [15, 90, 12, 1.2, 0.85, -0.3],
  [2, 96, 11, 1.0, 0.95, 0.9],
  [26, 97, 8.5, 1.25, 0.8, 0.2],
  [13, 64, 7.5, 1.1, 0.85, -0.6],
  [23, 79, 6, 1.0, 0.9, 1.2],
  // bottom-centre-right (x 45-55%, y 80-100%)
  [50, 95, 9, 1.3, 0.8, -0.15],
  [56, 100, 6, 1.1, 0.9, 0.7],
];

const baseGeo = new PlaneGeometry(1, 1);

export default function Rocks() {
  const aspect = useAspect();
  const material = useDisposable(
    () =>
      new ShaderMaterial({
        vertexShader: ROCK_VERT,
        fragmentShader: ROCK_FRAG,
        transparent: true,
        depthWrite: false,
      }),
    [],
  );

  const attrs = useMemo(() => {
    const k = narrowScale(aspect);
    const xf = new Float32Array(ROCKS.length * 4);
    const sh = new Float32Array(ROCKS.length * 4);
    ROCKS.forEach(([x, y, r, sx, sy, rot], i) => {
      const [wx, wy] = pctToWorld(x, y, aspect);
      xf.set([wx, wy, rot, r * k], i * 4);
      sh.set([sx, sy, (i * 0.618 + 0.13) % 1, 0], i * 4);
    });
    return { xf, sh };
  }, [aspect]);

  const geometry = useDisposable(
    () => makeInstanced(baseGeo, ROCKS.length, { aXform: [4, attrs.xf], aShape: [4, attrs.sh] }),
    [attrs],
  );

  return (
    <mesh
      geometry={geometry}
      material={material}
      position={[0, 0, Z.ROCKS]}
      frustumCulled={false}
      renderOrder={Z.ROCKS}
    />
  );
}
