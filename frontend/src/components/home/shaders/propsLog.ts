import { NOISE_GLSL } from "./propsCommon";

// Floating log seen from above. Local x runs along the log from its broken near end (0) to uLen;
// local y across it (-uWid .. uWid). The round body starts at x = uStub; two split stubs reach
// back toward x = 0. Define SHADOW for the soft shadow it casts on the water.
export const LOG_VERT = /* glsl */ `
uniform float uLen;
uniform float uWid;
varying vec2 vP;
void main() {
  vec2 p = vec2(position.x * uLen, position.y * uWid);  // geometry: x in [0,1], y in [-1,1]
#ifdef SHADOW
  p.y *= 1.08;
#endif
  vP = p;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 0.0, 1.0);
}
`;

export const LOG_FRAG = /* glsl */ `
${NOISE_GLSL}
uniform float uLen;
uniform float uWid;
uniform float uStub;
uniform float uShadowAlpha;
varying vec2 vP;
void main() {
  vec2 p = vP;
  // Irregular girth along the length, slightly different on each side.
  float sideSeed = p.y > 0.0 ? 3.1 : 8.7;
  float girth = uWid * (0.9 + 0.14 * pNoise(vec2(p.x * 0.04, sideSeed))
                            + 0.05 * pNoise(vec2(p.x * 0.25, sideSeed + 2.0)));
  float yn = p.y / girth;
  float side = 1.0 - smoothstep(0.86, 1.0, abs(yn));

  // Broken, split near end: a rounded end pulled back into two stubs, with a jagged edge.
  float roundEnd = uWid * 0.35 * (1.0 - sqrt(max(0.0, 1.0 - min(yn * yn, 1.0))));
  float stubs = 0.9 * exp(-pow((yn - 0.4) / 0.27, 2.0)) + 0.55 * exp(-pow((yn + 0.4) / 0.25, 2.0));
  float xEnd = uStub + roundEnd - uStub * stubs + uWid * 0.14 * (pNoise(vec2(yn * 9.0, 1.7)) - 0.5);
  float endMask = smoothstep(xEnd - 0.3, xEnd + 0.3, p.x);
  // Far end fades out (it is well past the viewport edge anyway).
  float farMask = 1.0 - smoothstep(uLen - uWid, uLen, p.x);
  float shape = side * endMask * farMask;
  if (shape <= 0.002) discard;

#ifdef SHADOW
  gl_FragColor = vec4(0.16, 0.34, 0.38, shape * (1.0 - 0.5 * smoothstep(0.5, 1.0, abs(yn))) * uShadowAlpha);
#else
  // Cylinder shading; local +y faces up the screen for this placement (the lit, upper side).
  float h = sqrt(max(0.0, 1.0 - yn * yn));
  float lit = clamp(0.35 + 0.55 * h + 0.35 * yn, 0.0, 1.2);

  vec3 bark = vec3(0.64, 0.59, 0.52);
  vec3 barkLight = vec3(0.79, 0.75, 0.67);
  vec3 barkDark = vec3(0.50, 0.46, 0.41);
  vec3 grainPale = vec3(0.80, 0.74, 0.63);
  vec3 moss = vec3(0.663, 0.749, 0.431);       // #A9BF6E
  vec3 mossDeep = vec3(0.58, 0.66, 0.40);

  // Long bark grain: noise stretched hard along the log.
  float warp = pNoise(vec2(p.x * 0.03, yn * 1.5)) * 1.6;
  float streak = pNoise(vec2(p.x * 0.025, yn * 7.0 + warp));
  float fine = pNoise(vec2(p.x * 0.09, yn * 18.0 + warp * 2.0));
  vec3 col = mix(barkDark, bark, smoothstep(0.2, 0.55, streak));
  col = mix(col, barkLight, smoothstep(0.6, 0.85, fine) * 0.6);
  col = mix(col, barkDark, smoothstep(0.72, 0.92, 1.0 - fine) * 0.3);

  // Pale split wood at the broken end.
  float nearEnd = 1.0 - smoothstep(0.0, uWid * 0.5, p.x - xEnd);
  col = mix(col, grainPale * (0.92 + 0.12 * pNoise(vec2(p.x * 0.8, yn * 12.0))), nearEnd * 0.7);

  // Soft moss clumps, mostly on the upper side.
  float mossN = pFbm(vec2(p.x * 0.11, yn * 2.2) + 7.0) + 0.2 * yn;
  float mossMask = smoothstep(0.5, 0.68, mossN) * (1.0 - nearEnd * 0.6);
  col = mix(col, mix(mossDeep, moss, pFbm(p * 0.35)), mossMask * 0.85);

  col *= 0.78 + 0.3 * lit;
  // Soft top highlight along the upper side.
  col += 0.06 * exp(-pow((yn - 0.45) / 0.25, 2.0));
  col *= 1.0 - 0.07 * smoothstep(0.65, 0.95, abs(yn));  // soft pigment pooling at the edge
  col *= pGrain(p * 0.25);
  gl_FragColor = vec4(col, shape);
#endif
}
`;
