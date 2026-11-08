// Mossy grey-green boulders on the pond bed (DESIGN.md §1, "Pond bed"). Static; the water overlay
// above (Z.WATER) tints them, so they stay mid-tone.
import { useMemo } from "react";
import { PlaneGeometry, ShaderMaterial } from "three";
import { Z, pctToWorld, widthPctToWorld } from "./pond";
import { makeInstanced, useAspect, useDisposable } from "./shaders/propsCommon";
import { ROCK_FRAG, ROCK_VERT } from "./shaders/propsRock";

// [x%, y%, diameter (% of viewport width), squashX, squashY, rotation (rad)]
const ROCKS: [number, number, number, number, number, number][] = [
  // bottom-left cluster (x 0-30%, y 60-100%): a few large boulders, the biggest ~14% of width
  [5, 77, 14, 1.12, 0.92, 0.4],
  [17, 92, 12, 1.2, 0.86, -0.3],
  [1, 97, 10, 1.0, 1.0, 0.9],
  [28, 98, 7, 1.3, 0.8, 0.2],
  [15, 64, 6, 1.15, 0.85, -0.6],
  [26, 81, 4.5, 1.0, 0.9, 1.2],
  // bottom-centre-right (x 45-55%, y 80-100%)
  [50, 95, 8, 1.3, 0.82, -0.15],
  [56, 100, 5, 1.1, 0.95, 0.7],
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
    // Sizes are % of width; on portrait screens fall back to a 0.75 aspect so rocks stay visible.
    const sizeAspect = Math.max(aspect, 0.75);
    const xf = new Float32Array(ROCKS.length * 4);
    const sh = new Float32Array(ROCKS.length * 4);
    ROCKS.forEach(([x, y, dia, sx, sy, rot], i) => {
      const [wx, wy] = pctToWorld(x, y, aspect);
      // The outline sits at ~0.82 of the quad's half-size.
      xf.set([wx, wy, rot, widthPctToWorld(dia, sizeAspect) / 2 / 0.82], i * 4);
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
