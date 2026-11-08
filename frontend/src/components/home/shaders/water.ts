// Water surface: translucent turquoise tint, sunlit upper centre, diagonal light shaft from the upper
// left, drifting caustic web, watercolor grain, deeper tint toward the edges, and soft ripple rings
// (cursor ring buffer + around each koi). Painterly and restrained (DESIGN.md §1).
import { Vector2, Vector3 } from "three";
import { RIPPLE_COUNT } from "../pond";
import { noiseChunk, planeVertex } from "./common";

export const waterVertex = planeVertex;

export const waterFragment = /* glsl */ `
#define RIPPLES ${RIPPLE_COUNT}
uniform float uTime;
uniform float uAspect;
uniform vec3 uRipples[RIPPLES];
uniform vec2 uKoi[2];
uniform float uKoiRings;
varying vec2 vWorld;
varying vec2 vUv;
${noiseChunk}

// Caustic web: thin bright lines along animated voronoi borders, domain-warped so they meander.
float causticLayer(vec2 p, float t) {
  vec2 w = vec2(vnoise(p * 0.7 + t * 0.12), vnoise(p * 0.7 - t * 0.1 + 4.1)) - 0.5;
  p += w * 1.25;
  vec2 off;
  vec2 id;
  vec4 v = voronoi(p, t, 0.9, off, id);
  // Line width and brightness wander along the web so it reads hand-painted, not tiled.
  float wv = vnoise(p * 1.3 + 5.0);
  float line = (1.0 - smoothstep(0.0, mix(0.035, 0.085, wv), v.x)) * mix(0.55, 1.0, vnoise(p * 0.9 - 2.0));
  float glow = 1.0 - smoothstep(0.0, 0.22, v.x);
  return line * 0.7 + glow * glow * 0.3;
}

float ring(float d, float r, float w) {
  float x = (d - r) / w;
  return exp(-x * x);
}

void main() {
  float t = uTime;
  vec2 uv = vUv;
  vec2 p = vWorld;

  // ---- ripples: cursor ring buffer + faint rings around each koi ----
  float rip = 0.0;
  vec2 ripDir = vec2(0.0);
  for (int i = 0; i < RIPPLES; i++) {
    vec3 r = uRipples[i];
    float age = t - r.z;
    if (age > 0.0 && age < 3.2) {
      vec2 dv = p - r.xy;
      float d = length(dv);
      float life = 1.0 - age / 3.2;
      float k = life * life;
      float a = ring(d, 1.0 + age * 7.5, 0.6 + age * 0.45) * k;
      a += 0.5 * ring(d, age * 4.5, 0.7 + age * 0.4) * k;
      rip += a;
      ripDir += normalize(dv + 1e-4) * a;
    }
  }
  for (int i = 0; i < 2; i++) {
    vec2 kp = uKoi[i];
    if (uKoiRings > 0.5 && dot(kp, kp) > 0.01) {
      float d = length(p - kp);
      for (int j = 0; j < 3; j++) {
        float ph = fract(t * 0.16 + float(j) / 3.0 + float(i) * 0.37);
        rip += 0.55 * ring(d, 3.0 + ph * 11.0, 0.35 + ph * 0.5) * (1.0 - ph) * smoothstep(0.0, 0.12, ph);
      }
    }
  }

  // ---- base tint ----
  vec3 cDeep = vec3(0.600, 0.808, 0.851);   // edges/corners: deeper turquoise, still light
  vec3 cWater = vec3(0.663, 0.851, 0.886);  // #A9D9E2
  vec3 cSky = vec3(0.796, 0.910, 0.933);    // #CBE8EE
  vec3 cSun = vec3(0.905, 0.958, 0.955);    // sunlit highlight
  vec3 cCream = vec3(0.985, 0.975, 0.925);  // caustic lines

  vec2 q = (uv - 0.5) * vec2(uAspect, 1.0);
  // Sunlit upper centre.
  vec2 sd = (uv - vec2(0.5, 0.86)) * vec2(uAspect * 0.85, 1.35);
  float sun = exp(-dot(sd, sd) * 1.7);
  // Soft diagonal light shaft from the upper left, falling down and to the right.
  vec2 a0 = vec2(-0.5 * uAspect - 0.05, 0.62);
  vec2 dir = normalize(vec2(1.0, -0.78));
  vec2 rel = q - a0;
  float along = dot(rel, dir);
  float across = abs(rel.x * dir.y - rel.y * dir.x);
  float shaft = exp(-pow(across / (0.12 + along * 0.14), 2.0)) * smoothstep(-0.1, 0.3, along) * exp(-along * 0.7);
  shaft *= 0.75 + 0.25 * vnoise(vec2(along * 3.0 - t * 0.05, across * 6.0));
  // Edge/corner depth.
  vec2 e = (uv - 0.5) * 2.0;
  float edge = smoothstep(0.35, 1.25, length(e * vec2(0.85, 1.0)));

  // Calm, luminous centre behind the title and buttons.
  vec2 cd = (uv - vec2(0.5, 0.47)) * vec2(uAspect * 0.9, 1.6);
  float calm = exp(-dot(cd, cd) * 1.4);
  vec3 col = mix(cWater, cSky, clamp(0.25 + uv.y * 0.35 + sun * 0.55 + calm * 0.3, 0.0, 1.0));
  col = mix(col, cDeep, edge * 0.75);
  col = mix(col, cSun, clamp(sun * 0.65 + shaft * 0.7, 0.0, 1.0));

  // ---- caustics: two drifting layers, refracted a little by ripples ----
  vec2 cp = p + ripDir * 0.9;
  float cs = causticLayer(cp / 13.0 + vec2(t * 0.014, t * 0.009), t * 0.36);
  cs = cs * 0.8 + 0.22 * causticLayer(cp / 7.5 + vec2(-t * 0.012, t * 0.016) + 9.1, t * 0.48 + 2.0);
  // Patchy strength so the web breathes instead of tiling evenly.
  float breath = 0.55 + 0.6 * fbm(p * 0.035 + vec2(t * 0.01, -t * 0.008));
  cs *= breath * (0.75 + 0.35 * sun + 0.3 * shaft) * (1.0 - edge * 0.35);

  // ---- grain (watercolor paper) ----
  float grain = fbm(p * 1.4) - 0.5;
  float fine = hash12(floor(gl_FragCoord.xy)) - 0.5;

  float alpha = 0.44 + edge * 0.18 + sun * 0.22 + shaft * 0.2 + calm * 0.12;
  vec3 outCol = col;
  float c = clamp(cs * 0.6 + rip * 1.0, 0.0, 1.0);
  outCol = mix(outCol, cCream, c);
  alpha = mix(alpha, 0.92, c);
  outCol += grain * 0.035 + fine * 0.012;

  gl_FragColor = vec4(outCol, clamp(alpha, 0.0, 1.0));
}
`;

export function createWaterUniforms() {
  return {
    uTime: { value: 0 },
    uAspect: { value: 16 / 9 },
    uRipples: { value: Array.from({ length: RIPPLE_COUNT }, () => new Vector3(0, 0, -1000)) },
    uKoi: { value: [new Vector2(), new Vector2()] },
    uKoiRings: { value: 1 },
  };
}
