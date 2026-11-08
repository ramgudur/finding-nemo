// Koi shaders. The fish is one segmented strip in unit space: x in [-0.5, 0.5] (head at +x),
// y in [-0.25, 0.25]; the mesh is scaled to the koi length in world units. The vertex shader bends
// the strip (travelling swim wave + path curvature); the fragment shader paints the body, fins,
// kohaku patches and eyes procedurally, so there are no hard outlines or textures to load.

export const koiVertex = /* glsl */ `
uniform float uPhase;   // swim phase (radians), advanced by the path speed
uniform float uCurv;    // path curvature in 1/length units (+ = turning left)
uniform float uAmp;     // swim wave amplitude scale
varying vec2 vP;

void main() {
  vec3 pos = position;
  vP = pos.xy;
  float t = 0.5 - pos.x;              // 0 at the snout, 1 at the tail tip
  float s = max(t - 0.12, 0.0);       // pivot just behind the head
  float k = 6.2831 * 0.85;
  float amp = uAmp * (0.006 + 0.11 * s * s);
  float off = amp * sin(k * s - uPhase) + 0.5 * uCurv * s * s;
  // slope of the centreline w.r.t. t, used to keep the cross-section perpendicular
  float slope = amp * k * cos(k * s - uPhase) + uCurv * s;
  float y0 = pos.y;
  float n = inversesqrt(1.0 + slope * slope);
  pos.y = off + y0 * n;
  pos.x = pos.x + y0 * slope * n;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(pos, 1.0);
}
`;

export const koiFragment = /* glsl */ `
uniform float uTime;
uniform float uPhase;
uniform float uSeed;
uniform float uShadow;  // 1 = draw the soft drop shadow silhouette instead of the fish
varying vec2 vP;

float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p) {
  vec2 i = floor(p); vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x),
             mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);
}
float fbm(vec2 p) {
  float v = 0.0; float a = 0.5;
  for (int i = 0; i < 4; i++) { v += a * noise(p); p = p * 2.03 + 7.1; a *= 0.5; }
  return v;
}

float bodyHalfW(float t) {
  float u = clamp(t / 0.68, 0.0, 1.0);
  float nose = pow(1.0 - pow(1.0 - min(u / 0.32, 1.0), 2.0), 0.62);  // rounded, slightly pointed snout
  float taper = 1.0 - 0.78 * smoothstep(0.30, 1.0, u);
  return 0.102 * nose * taper;
}

// soft ellipse fin anchored at base b, pointing along angle a (in t/y space)
float fin(vec2 q, vec2 b, float a, float len, float wid, float soft) {
  vec2 d = vec2(cos(a), sin(a));
  vec2 r = q - b;
  vec2 l = vec2(dot(r, d) - len * 0.5, dot(r, vec2(-d.y, d.x)));
  // slightly teardrop: wider toward the outer end
  float w = wid * (0.75 + 0.5 * clamp(dot(r, d) / len, 0.0, 1.0));
  float e = length(vec2(l.x / (len * 0.5), l.y / w));
  float ray = 0.82 + 0.18 * sin(atan(l.y, dot(r, d) + 0.01) * 16.0);
  return smoothstep(1.0, 1.0 - soft * 8.0, e) * ray * (0.75 + 0.25 * smoothstep(-0.5, 0.6, l.x / (len * 0.5)));
}

void main() {
  float t = 0.5 - vP.x;
  float y = vP.y;
  float soft = mix(0.010, 0.045, uShadow);

  // ---- body
  float hw = bodyHalfW(t);
  float bm = smoothstep(hw + soft * 0.6, hw - soft, abs(y))
           * smoothstep(-0.004, 0.012 + soft, t) * smoothstep(0.72, 0.66, t);

  // ---- tail fin: long, flowing, translucent, softly forked
  float tt = (t - 0.60) / 0.40;
  float tm = 0.0; float rays = 1.0;
  if (tt > 0.0) {
    float ttc = clamp(tt, 0.0, 1.0);
    float yw = y + 0.018 * sin(ttc * 7.0 - uPhase * 1.0 - uTime * 0.6) * ttc;
    float tw = 0.014 + 0.16 * pow(ttc, 0.8);
    float side = clamp(abs(yw) / tw, 0.0, 1.0);
    float tend = 0.80 + 0.20 * smoothstep(0.1, 0.9, side);   // lobes longer than the centre
    tm = smoothstep(tw + soft, tw - soft * 2.5, abs(yw)) * smoothstep(tend, tend - 0.22, tt);
    rays = (0.8 + 0.2 * sin(yw / (0.02 + ttc * 0.16) * 11.0)) * (1.0 - 0.35 * ttc);
  }

  // ---- pectoral and pelvic fins (paddle with phase offsets)
  float fl = 0.0;
  for (int i = 0; i < 2; i++) {
    float sg = i == 0 ? 1.0 : -1.0;
    float fp = sin(uPhase * 0.5 + sg * 1.3);
    float aP = 0.95 + 0.30 * fp;
    vec2 q = vec2(t, y * sg);
    fl = max(fl, fin(q, vec2(0.20, 0.06), aP, 0.13, 0.036, soft));
    fl = max(fl, 0.8 * fin(q, vec2(0.45, 0.035), 0.8 + 0.15 * fp, 0.07, 0.02, soft));
  }

  float finA = max(tm * 0.68 * rays, fl * 0.62);

  if (uShadow > 0.5) {
    float sil = max(bm, finA * 0.45);
    gl_FragColor = vec4(vec3(0.14, 0.31, 0.43), sil * 0.20);
    return;
  }

  // ---- paint
  vec3 white = vec3(0.985, 0.975, 0.95);
  vec3 finCol = vec3(0.97, 0.98, 0.98);
  float sh = clamp(abs(y) / max(hw, 0.001), 0.0, 1.0);
  vec3 col = mix(white, vec3(0.80, 0.87, 0.90), 0.45 * sh * sh);
  // faint spine highlight (dorsal ridge seen from above)
  col += 0.02 * smoothstep(0.02, 0.0, abs(y)) * smoothstep(0.2, 0.3, t) * smoothstep(0.6, 0.45, t);

  vec2 np = vec2(t * 3.6, y * 6.0) + vec2(uSeed * 13.7, uSeed * 5.3);
  float n = fbm(np);
  float head = 0.12 * exp(-pow((t - (0.12 + 0.04 * fract(uSeed * 3.1))) / 0.07, 2.0));
  float mid = 0.10 * exp(-pow((t - (0.36 + 0.08 * fract(uSeed * 5.7))) / 0.09, 2.0));
  float pv = n + head + mid + 0.07 * sin(uSeed * 9.0 + t * 17.0) + 0.03;
  float mask = smoothstep(0.035, 0.09, t) * smoothstep(0.62, 0.50, t) * smoothstep(1.05, 0.55, sh);
  float patchA = smoothstep(0.53, 0.60, pv) * mask;
  float edge = (smoothstep(0.52, 0.56, pv) - smoothstep(0.56, 0.64, pv)) * mask;
  float pig = 0.82 + 0.36 * fbm(vP * 38.0 + uSeed * 3.0);
  vec3 coral = vec3(0.91, 0.53, 0.37);
  col = mix(col, coral * mix(1.08, 0.96, pig - 0.82), clamp(patchA * 0.76 * pig, 0.0, 1.0));
  col = mix(col, coral * 0.9, edge * 0.22);
  // watercolor grain
  col *= 0.975 + 0.05 * noise(vP * 260.0);

  // eyes
  float ey = hw * 0.62;
  vec2 ep = vec2(t - 0.07, abs(y) - ey);
  float eye = smoothstep(0.0095, 0.004, length(ep));
  col = mix(col, vec3(0.16, 0.22, 0.27), eye * 0.9);

  vec3 fc = finCol * (0.95 + 0.05 * rays);
  vec3 outC = mix(fc, col, bm);
  float a = max(finA, bm * 0.96);
  gl_FragColor = vec4(outC, a);
}
`;
