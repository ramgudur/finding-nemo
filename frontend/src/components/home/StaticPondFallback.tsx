// No-WebGL (and pre-load) approximation of the hero pond: layered CSS gradients for the water and
// light, an SVG pebble pattern for the bed, and soft shapes for rocks, seaweed, pads and the log.
// Static on purpose: it is also the Suspense fallback, so it must paint instantly.

const pads: Array<[number, number, number, number]> = [
  // cx%, cy%, r (in 0..1000 viewBox units of width), rotation
  [6, 9, 26, 20],
  [14, 6, 20, 140],
  [12, 19, 23, 260],
  [3, 21, 16, 80],
  [83, 27, 21, 200],
  [9, 87, 24, 300],
  [21, 94, 20, 40],
  [91, 82, 32, 120],
  [98, 94, 28, 220],
  [86, 96, 22, 330],
];

const boulders: Array<[number, number, number, number]> = [
  [6, 72, 70, 50],
  [16, 84, 85, 60],
  [4, 94, 70, 48],
  [25, 96, 60, 40],
  [50, 92, 60, 40],
];

export default function StaticPondFallback() {
  return (
    <div
      className="absolute inset-0 overflow-hidden"
      aria-hidden="true"
      style={{
        background: [
          "radial-gradient(60% 45% at 50% 10%, rgba(240,248,246,0.85), rgba(240,248,246,0) 70%)",
          "linear-gradient(140deg, rgba(255,252,240,0.35) 0%, rgba(255,252,240,0) 38%)",
          "radial-gradient(120% 110% at 50% 45%, #C4E5EB 0%, #B2DDE5 55%, #9CCFDA 100%)",
        ].join(","),
      }}
    >
      <svg className="absolute inset-0 h-full w-full" viewBox="0 0 1000 560" preserveAspectRatio="xMidYMid slice">
        <defs>
          <pattern id="fb-pebbles" width="90" height="80" patternUnits="userSpaceOnUse" patternTransform="rotate(17) scale(0.8)">
            <ellipse cx="22" cy="20" rx="18" ry="14" fill="#8FB3BF" opacity="0.2" />
            <ellipse cx="62" cy="16" rx="15" ry="12" fill="#9CBCC3" opacity="0.18" />
            <ellipse cx="44" cy="50" rx="20" ry="15" fill="#88AEBB" opacity="0.2" />
            <ellipse cx="80" cy="58" rx="12" ry="10" fill="#A3C1C4" opacity="0.17" />
            <ellipse cx="10" cy="62" rx="11" ry="9" fill="#93B6C0" opacity="0.17" />
          </pattern>
          <filter id="fb-soft" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="3" />
          </filter>
          <filter id="fb-softer" x="-20%" y="-20%" width="140%" height="140%">
            <feGaussianBlur stdDeviation="8" />
          </filter>
        </defs>
        <rect width="1000" height="560" fill="url(#fb-pebbles)" filter="url(#fb-soft)" />
        <g filter="url(#fb-softer)" opacity="0.45">
          {boulders.map(([x, y, rx, ry], i) => (
            <ellipse key={i} cx={x * 10} cy={y * 5.6} rx={rx} ry={ry} fill="#9DB5A6" />
          ))}
        </g>
        <g filter="url(#fb-softer)" opacity="0.5" fill="none" stroke="#8FB08A" strokeWidth="14" strokeLinecap="round">
          <path d="M20 560 C 40 420, 0 330, 40 170" />
          <path d="M70 560 C 110 450, 50 380, 90 260" />
          <path d="M360 560 C 380 500, 340 470, 370 420" />
          <path d="M980 560 C 940 400, 990 250, 950 40" />
          <path d="M930 560 C 900 450, 950 380, 915 280" />
        </g>
        {/* Log, upper right, running off-screen. */}
        <g filter="url(#fb-soft)">
          <path d="M640 30 L 1040 210" stroke="#8E8576" strokeWidth="42" strokeLinecap="round" opacity="0.85" />
          <ellipse cx="760" cy="80" rx="40" ry="14" fill="#8FA97A" opacity="0.7" transform="rotate(24 760 80)" />
          <ellipse cx="900" cy="146" rx="34" ry="12" fill="#98B080" opacity="0.6" transform="rotate(24 900 146)" />
        </g>
        <g>
          {pads.map(([x, y, r, rot], i) => (
            <g key={i} transform={`translate(${x * 10} ${y * 5.6}) rotate(${rot})`}>
              <circle r={r} fill="#5F8F86" opacity="0.1" transform="translate(3 4)" />
              <path
                d={`M0 0 L ${r * 0.97} ${-r * 0.25} A ${r} ${r} 0 1 0 ${r * 0.97} ${r * 0.25} Z`}
                fill="#AFCB8A"
                opacity="0.92"
              />
            </g>
          ))}
        </g>
      </svg>
    </div>
  );
}
