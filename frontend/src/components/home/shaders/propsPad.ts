import { NOISE_GLSL } from "./propsCommon";

// Floating lily pads and tiny leaves (instanced). Defines: LEAF (almond leaf instead of a notched
// disc), SHADOW (soft offset shadow on the water instead of the pad itself).
// aXform = (x, y, rotation, radius); aMeta = (seed, phase, notchHalfWidth, drift).
export const PAD_VERT = /* glsl */ `
attribute vec4 aXform;
attribute vec4 aMeta;
uniform float uTime;
uniform float uMotion;
uniform vec2 uOffset;
varying vec2 vUv;
varying float vSeed;
varying float vNotch;
void main() {
  vUv = position.xy * 2.0;
  vSeed = aMeta.x;
  vNotch = aMeta.z;
  float ph = aMeta.y;
  // Very slow idle bob (breathing scale) and rotation.
  float rot = aXform.z + sin(uTime * 0.11 + ph) * 0.05 * uMotion;
  float scl = 1.0 + 0.012 * sin(uTime * 0.37 + ph * 1.7) * uMotion;
  vec2 p = position.xy * 2.0 * aXform.w * scl;
#ifdef LEAF
  p.y *= 0.46;
#endif
#ifdef SHADOW
  p *= 1.04;
#endif
  float c = cos(rot), s = sin(rot);
  p = vec2(c * p.x - s * p.y, s * p.x + c * p.y);
  // Tiny drift (leaves drift a little more than pads).
  p += vec2(sin(uTime * 0.07 + ph), cos(uTime * 0.053 + ph * 1.3)) * aMeta.w * uMotion;
  p += aXform.xy + uOffset;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 0.0, 1.0);
}
`;

export const PAD_FRAG = /* glsl */ `
${NOISE_GLSL}
uniform float uShadowAlpha;
varying vec2 vUv;
varying float vSeed;
varying float vNotch;
void main() {
  vec2 uv = vUv;
  float s = vSeed * 17.3;
  float a = atan(uv.y, uv.x);
#ifdef LEAF
  // Almond leaf: pointed at both ends along x.
  float hw = pow(max(0.0, 1.0 - uv.x * uv.x), 0.75);
  float d = max(abs(uv.y) / max(hw, 0.001), abs(uv.x));
  float shape = 1.0 - smoothstep(0.82, 1.0, d);
#else
  float rr = 0.9 + 0.025 * sin(a * 3.0 + s) + 0.02 * (pNoise(vec2(cos(a), sin(a)) * 2.0 + s) - 0.5);
  float d = length(uv) / rr;
  float shape = 1.0 - smoothstep(0.9, 1.0, d);
  // Pie-wedge notch cut to the centre (local +x), soft-edged.
  float notch = smoothstep(vNotch * 0.55, vNotch, abs(a)) ;
  shape *= mix(1.0, notch, smoothstep(0.02, 0.12, length(uv)));
#endif

#ifdef SHADOW
  float sh = shape * (1.0 - smoothstep(0.55, 1.0, d) * 0.6);
  if (sh <= 0.002) discard;
  gl_FragColor = vec4(0.16, 0.34, 0.38, sh * uShadowAlpha);
#else
  if (shape <= 0.002) discard;
  vec3 inner = vec3(0.725, 0.827, 0.561);   // #B9D38F
  vec3 outer = vec3(0.616, 0.749, 0.478);   // #9DBF7A
  vec3 rim   = vec3(0.56, 0.69, 0.44);
  vec3 col = mix(inner, outer, smoothstep(0.1, 0.9, d));
  col = mix(col, rim, smoothstep(0.78, 0.96, d) * 0.6);
  float mott = pFbm(uv * 2.2 + s);
  col *= 0.9 + 0.16 * mott;
  // Fade toward the water a touch, so pads stay soft rather than saturated.
  col = mix(col, vec3(dot(col, vec3(0.333))), 0.12);
#ifndef LEAF
  // Lighter centre and a thin pale highlight just inside the rim.
  col += vec3(0.035, 0.035, 0.025) * (1.0 - smoothstep(0.0, 0.5, d));
  col += vec3(0.05, 0.05, 0.04) * exp(-pow((d - 0.88) / 0.03, 2.0));
#endif
#ifdef LEAF
  float vein = 1.0 - smoothstep(0.0, 0.08, abs(uv.y));
  col += 0.06 * vein * (1.0 - abs(uv.x));
  col = mix(col, vec3(0.70, 0.74, 0.45), 0.25);
#else
  // Soft radial veins fanning from the centre.
  float va = a + 0.08 * (pNoise(uv * 3.0 + s) - 0.5);
  float vein = pow(abs(cos(va * 5.5)), 8.0);
  col += 0.018 * vein * smoothstep(0.08, 0.3, d) * (1.0 - smoothstep(0.75, 0.95, d));
  // Faint sheen toward the top-left.
  col += 0.05 * smoothstep(0.4, -0.8, dot(uv, vec2(0.7, -0.7)));
#endif
  col *= pGrain(uv * 1.5 + s);
  gl_FragColor = vec4(col, shape * 0.97);
#endif
}
`;
