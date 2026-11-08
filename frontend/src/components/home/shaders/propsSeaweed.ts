import { NOISE_GLSL } from "./propsCommon";

// One tapered ribbon per instance, built along local +y (base at 0, tip at 1).
// aXform = (baseX, baseY, rotation, length); aBlade = (width, curl, phase, twist);
// aMove = (speed, amplitude, tone, alpha).
export const SEAWEED_VERT = /* glsl */ `
attribute vec4 aXform;
attribute vec4 aBlade;
attribute vec4 aMove;
uniform float uTime;
uniform float uMotion;
varying vec2 vUv;
varying float vT;
varying float vTone;
varying float vAlpha;
varying float vFace;
void main() {
  float t = position.y;            // 0 base .. 1 tip
  float side = position.x * 2.0;   // -1 .. 1 across the ribbon
  float len = aXform.w;
  float phase = aBlade.z;
  float time = uTime * aMove.x;

  // Sideways sway: travelling wave, the tip moves most.
  float sway = (sin(time + phase - t * 2.4) + 0.35 * sin(time * 1.7 + phase * 2.1 - t * 4.0))
             * aMove.y * pow(t, 1.5) * uMotion;
  // Static curl plus a slow breathing of the curl.
  float curl = aBlade.y * (1.0 + 0.15 * sin(time * 0.5 + phase) * uMotion);
  // Second, phase-seeded wiggle gives each ribbon a gentle S-curve.
  float wig = 0.13 * sin(phase * 3.7) * sin(t * 3.14159 * 1.3 + phase);
  float cx = len * (curl * t * t + wig + sway);
  // Tangent of the centreline (for the ribbon's perpendicular).
  float dx = len * (2.0 * curl * t + 0.13 * sin(phase * 3.7) * 4.08 * cos(t * 4.08 + phase) + aMove.y * 1.5 * pow(max(t, 0.001), 0.5) *
             sin(time + phase - t * 2.4) * uMotion);
  vec2 tang = normalize(vec2(dx, len));
  vec2 perp = vec2(tang.y, -tang.x);

  // Taper: narrow at the root, widest in the lower third, pointed tip; a slow twist makes the
  // ribbon flip edge-on now and then, which reads as curling seen from above.
  float w = aBlade.x * (0.45 + 0.55 * sin(min(t * 1.25, 1.0) * 3.14159 * 0.9))
          * (1.0 - smoothstep(0.7, 1.0, t) * 0.9);
  float face = abs(cos(t * aBlade.w + phase + uTime * 0.25 * uMotion));
  w *= 0.4 + 0.6 * face;

  vec2 p = vec2(cx, t * len) + perp * side * w * 0.5;
  float c = cos(aXform.z), s = sin(aXform.z);
  p = vec2(c * p.x - s * p.y, s * p.x + c * p.y) + aXform.xy;

  vUv = vec2(side, t);
  vT = t;
  vTone = aMove.z;
  vAlpha = aMove.w;
  vFace = face;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 0.0, 1.0);
}
`;

export const SEAWEED_FRAG = /* glsl */ `
${NOISE_GLSL}
varying vec2 vUv;
varying float vT;
varying float vTone;
varying float vAlpha;
varying float vFace;
void main() {
  float across = abs(vUv.x);
  float edge = 1.0 - smoothstep(0.35, 1.0, across);         // soft ribbon edges
  float tip = 1.0 - smoothstep(0.82, 1.0, vT);
  float root = smoothstep(0.0, 0.06, vT);
  // More opaque at the base, fading toward the tips.
  float alpha = edge * tip * root * vAlpha * mix(1.0, 0.4, smoothstep(0.1, 1.0, vT));
  if (alpha <= 0.002) discard;

  vec3 yellow = vec3(0.639, 0.741, 0.494);  // #A3BD7E
  vec3 olive  = vec3(0.498, 0.651, 0.478);  // #7FA67A
  vec3 blue   = vec3(0.498, 0.651, 0.627);  // #7FA6A0
  vec3 sage   = vec3(0.663, 0.737, 0.647);  // #A9BCA5
  // Each blade gets its own green along yellow -> olive -> blue, paling toward the tip.
  float h = vTone * 2.0;
  vec3 hue = h < 1.0 ? mix(yellow, olive, h) : mix(olive, blue, h - 1.0);
  float n = pFbm(vec2(vUv.x * 1.5 + vTone * 30.0, vT * 6.0));
  vec3 col = mix(hue, sage, clamp(vT * 0.55 + (n - 0.5) * 0.5, 0.0, 1.0));
  // Pale midrib and brighter faces where the ribbon is flat to the light.
  col += 0.05 * (1.0 - smoothstep(0.0, 0.25, across));
  col *= 0.9 + 0.12 * vFace;
  col *= pGrain(vec2(vUv.x * 0.6, vT * 4.0) + vTone * 10.0);
  gl_FragColor = vec4(col, alpha * (0.75 + 0.25 * vFace));
}
`;
