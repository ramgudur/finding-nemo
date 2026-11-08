import { NOISE_GLSL } from "./propsCommon";

// Instanced boulder: aXform = (x, y, rotation, radius), aShape = (squashX, squashY, seed, unused).
export const ROCK_VERT = /* glsl */ `
attribute vec4 aXform;
attribute vec4 aShape;
varying vec2 vUv;
varying float vSeed;
varying float vRot;
void main() {
  vUv = position.xy * 2.0;           // plane is 1x1 -> uv in [-1, 1]
  vSeed = aShape.z;
  vRot = aXform.z;
  vec2 p = position.xy * 2.0 * aXform.w * aShape.xy;
  float c = cos(aXform.z), s = sin(aXform.z);
  p = vec2(c * p.x - s * p.y, s * p.x + c * p.y) + aXform.xy;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 0.0, 1.0);
}
`;

export const ROCK_FRAG = /* glsl */ `
${NOISE_GLSL}
varying vec2 vUv;
varying float vSeed;
varying float vRot;
void main() {
  vec2 uv = vUv;
  float a = atan(uv.y, uv.x);
  float s = vSeed * 13.7;
  // Lumpy rounded outline.
  // Irregular rounded outline: a few seeded harmonics plus noise, so no two rocks are ovals.
  float rr = 0.80 + 0.10 * sin(a * 2.0 + s) + 0.08 * sin(a * 3.0 + s * 1.7)
           + 0.04 * sin(a * 5.0 + s * 2.3)
           + 0.16 * (pNoise(vec2(cos(a), sin(a)) * 1.1 + s) - 0.5);
  float d = length(uv) / rr;
  float alpha = 1.0 - smoothstep(0.86, 1.0, d);
  if (alpha <= 0.001) discard;

  // Dome normal; light from the screen's top-left (undo the instance rotation).
  float c = cos(-vRot), sn = sin(-vRot);
  vec2 L2 = vec2(c * -0.6 - sn * 0.65, sn * -0.6 + c * 0.65);
  float h = sqrt(max(0.0, 1.0 - d * d));
  vec3 n = normalize(vec3(uv * 0.85, h + 0.25));
  float lit = clamp(dot(n, normalize(vec3(L2, 0.7))), 0.0, 1.0);

  vec3 stone = vec3(0.624, 0.686, 0.651);      // #9FAFA6
  vec3 stoneDark = vec3(0.44, 0.52, 0.49);     // a little below #7F918A for contrast
  vec3 moss = vec3(0.55, 0.63, 0.46);
  vec3 mossLight = vec3(0.64, 0.71, 0.52);

  float m1 = pFbm(uv * 1.8 + s);
  vec3 col = mix(stoneDark, stone, smoothstep(0.25, 0.75, m1));
  // Moss patches, favoured on the lit upper side.
  float mossN = pFbm(uv * 2.6 + s * 2.3 + 4.0) + 0.18 * dot(uv, L2);
  float mossMask = smoothstep(0.50, 0.66, mossN);
  col = mix(col, mix(moss, mossLight, pNoise(uv * 6.0 + s)), mossMask * 0.8);

  col *= 0.64 + 0.56 * lit;
  // Pigment pooling at the edge (watercolor), soft, never an outline.
  col *= 1.0 - 0.10 * smoothstep(0.62, 0.95, d);
  col *= pGrain(uv * 2.0 + s);
  gl_FragColor = vec4(col, alpha);
}
`;
