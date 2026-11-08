// Pond floor: rounded cobblestones and pebbles, softly mottled, gently refracted by the water above.
import { noiseChunk, planeVertex } from "./common";

export const bedVertex = planeVertex;

export const bedFragment = /* glsl */ `
uniform float uTime;
varying vec2 vWorld;
varying vec2 vUv;
${noiseChunk}

// One cobble layer: returns rgb in .rgb and stone coverage in .a.
vec4 cobbles(vec2 p, float seed) {
  vec2 off;
  vec2 id;
  vec4 v = voronoi(p + seed, 0.0, 0.62, off, id);
  float h = v.z;
  float h2 = hash12(id + 17.3 + seed);
  // Rounded stone: soft shoulder from the border inward, varied per stone so sizes read mixed.
  // Round the polygonal cell: a soft disc around the stone centre, clipped by the cell border.
  float radius = mix(0.40, 0.56, h2);
  vec2 sq = -off * mat2(1.0, 0.0, 0.0, mix(0.85, 1.15, h)); // slight ovals
  float disc = 1.0 - smoothstep(radius * 0.5, radius * 1.02, length(sq));
  float stone = disc * smoothstep(0.02, 0.22, v.x);
  // Dome light from the upper left: brighter on the side facing the light, soft occlusion at the rim.
  vec2 local = -off; // p relative to stone centre
  float dome = clamp(1.0 - dot(local, local) / (radius * radius), 0.0, 1.0);
  float lit = clamp(0.5 + 0.55 * dot(normalize(local + 1e-4), normalize(vec2(-0.7, 0.7))) * (1.0 - dome), 0.0, 1.0);
  vec3 blueStone = vec3(0.55, 0.75, 0.83);
  vec3 greyStone = vec3(0.62, 0.78, 0.84);
  vec3 sandStone = vec3(0.70, 0.79, 0.80);
  vec3 col = mix(blueStone, greyStone, h);
  col = mix(col, sandStone, smoothstep(0.78, 1.0, h2) * 0.6);
  // Painterly mottling inside each stone.
  float m = fbm(p * 2.6 + id * 3.1);
  col *= 0.95 + 0.10 * m;
  col *= 0.88 + 0.18 * lit + 0.06 * dome;
  return vec4(col, stone);
}

void main() {
  float t = uTime;
  vec2 p = vWorld / 8.6;
  // Refraction: slow, low-amplitude UV wobble as if seen through moving water.
  vec2 warp = vec2(
    vnoise(p * 0.55 + vec2(t * 0.16, t * 0.05)),
    vnoise(p * 0.55 + vec2(-t * 0.07, t * 0.14) + 7.3)
  ) - 0.5;
  p += warp * 0.32;
  p += 0.03 * vec2(sin(p.y * 2.1 + t * 0.9), cos(p.x * 1.9 + t * 0.8));

  // Gaps between stones: silty blue, a little deeper than the stones but never dark.
  vec3 grout = vec3(0.43, 0.62, 0.72);
  grout *= 0.92 + 0.12 * fbm(p * 1.7);

  // Big cobbles with a finer pebble layer filling the gaps.
  vec4 pebbles = cobbles(p * 2.1, 31.0);
  vec3 col = mix(grout, pebbles.rgb * 0.97, pebbles.a * 0.35);
  vec4 big = cobbles(p, 0.0);
  col = mix(col, big.rgb, big.a);

  // Large soft wash so the floor is not a uniform tile.
  float wash = fbm(vWorld * 0.018 + 3.0);
  col *= 0.92 + 0.14 * wash;

  gl_FragColor = vec4(col, 1.0);
}
`;

export function createBedUniforms() {
  return { uTime: { value: 0 } };
}
