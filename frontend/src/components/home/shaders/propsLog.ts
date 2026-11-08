import { NOISE_GLSL } from "./propsCommon";

// Floating log seen from above. Local x runs along the log from its left end (0) to uLen; local
// y across it (-uWid .. uWid). Define SHADOW for the soft shadow it casts on the water.
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
uniform float uShadowAlpha;
varying vec2 vP;
void main() {
  vec2 p = vP;
  // Slightly irregular girth along the length.
  float girth = uWid * (0.92 + 0.08 * pNoise(vec2(p.x * 0.05, 3.1)));
  float cap = girth * 0.75;
  float d;
  if (p.x < cap) d = length(vec2((cap - p.x) / cap, p.y / girth));
  else d = abs(p.y) / girth;
  float shape = 1.0 - smoothstep(0.86, 1.0, d);
  if (shape <= 0.002) discard;

#ifdef SHADOW
  gl_FragColor = vec4(0.16, 0.34, 0.38, shape * (1.0 - 0.5 * smoothstep(0.5, 1.0, d)) * uShadowAlpha);
#else
  float yn = p.y / girth;                       // -1 .. 1 across
  // Cylinder shading; local +y faces the screen's upper-left for this placement.
  float h = sqrt(max(0.0, 1.0 - yn * yn));
  float lit = clamp(0.35 + 0.55 * h + 0.35 * yn, 0.0, 1.2);

  vec3 bark = vec3(0.55, 0.50, 0.44);
  vec3 barkLight = vec3(0.70, 0.65, 0.57);
  vec3 barkDark = vec3(0.40, 0.36, 0.32);
  vec3 moss = vec3(0.52, 0.62, 0.40);
  vec3 mossLight = vec3(0.66, 0.73, 0.50);

  // Bark streaks: noise stretched along the log.
  float warp = pNoise(vec2(p.x * 0.04, yn * 1.5)) * 1.5;
  float streak = pNoise(vec2(p.x * 0.06, yn * 6.0 + warp));
  float fine = pNoise(vec2(p.x * 0.2, yn * 14.0 + warp * 2.0));
  vec3 col = mix(barkDark, bark, smoothstep(0.2, 0.55, streak));
  col = mix(col, barkLight, smoothstep(0.62, 0.85, fine) * 0.6);
  col = mix(col, barkDark, smoothstep(0.7, 0.9, 1.0 - fine) * 0.35);

  // Moss patches, mostly on the upper (lit) side.
  float mossN = pFbm(vec2(p.x * 0.07, yn * 1.2) + 7.0) + 0.2 * yn;
  float mossMask = smoothstep(0.52, 0.66, mossN);
  col = mix(col, mix(moss, mossLight, pNoise(p * 0.4)), mossMask * 0.9);

  // Weathered, lighter left end.
  col = mix(col, barkLight, (1.0 - smoothstep(0.0, cap * 1.6, p.x)) * 0.25);

  col *= 0.7 + 0.38 * lit;
  col *= 1.0 - 0.08 * smoothstep(0.65, 0.95, d);  // soft pigment pooling at the edge
  col *= pGrain(p * 0.25);
  gl_FragColor = vec4(col, shape);
#endif
}
`;
