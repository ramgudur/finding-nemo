// DEMO_MODE: distance from the platform's estimates to the simulator's true source over time, with a
// marker per applied weight/trust Change so the Critic's effect is visible. Plain SVG, one y-axis (meters).
import { useMemo, useState } from "react";
import type { ErrorSeries } from "../../api/types";

const W = 640;
const H = 220;
const PAD = { top: 12, right: 16, bottom: 28, left: 44 };
const SERIES = [
  { key: "hotspot_error_m", label: "Top hotspot", color: "#2a78d6" },
  { key: "point_error_m", label: "Point estimate", color: "#eb6834" },
] as const;
const INK = { primary: "#0b0b0b", secondary: "#52514e", muted: "#8a8984", grid: "#e6e5e1", marker: "#8a8984" };

type Key = (typeof SERIES)[number]["key"];

function niceMax(v: number): number {
  if (v <= 0) return 10;
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

export function ErrorChart({ data }: { data: ErrorSeries | null }) {
  const [hover, setHover] = useState<number | null>(null);

  const geo = useMemo(() => {
    if (!data || data.series.length === 0) return null;
    const ts = data.series.map((p) => Date.parse(p.t));
    const t0 = Math.min(...ts);
    const t1 = Math.max(t0 + 1000, ...ts);
    const vals = data.series.flatMap((p) => [p.hotspot_error_m, p.point_error_m]).filter((v): v is number => v !== null);
    const yMax = niceMax(Math.max(0, ...vals));
    const x = (t: number) => PAD.left + ((t - t0) / (t1 - t0)) * (W - PAD.left - PAD.right);
    const y = (v: number) => H - PAD.bottom - (v / yMax) * (H - PAD.top - PAD.bottom);
    const paths = SERIES.map((s) => {
      let d = "";
      let pen = false;
      data.series.forEach((p, i) => {
        const v = p[s.key as Key];
        if (v === null) {
          pen = false;
          return;
        }
        d += `${pen ? "L" : "M"}${x(ts[i]!).toFixed(1)},${y(v).toFixed(1)}`;
        pen = true;
      });
      return { ...s, d };
    });
    const markers = data.changes
      .map((c) => ({ ...c, x: x(Date.parse(c.t)) }))
      .filter((m) => m.x >= PAD.left && m.x <= W - PAD.right);
    return { ts, t0, t1, yMax, x, y, paths, markers };
  }, [data]);

  if (!data) return null;
  if (!geo) {
    return (
      <p style={{ color: INK.secondary, margin: 0 }}>
        {data.truth_file ? "No ticks recorded inside the ground-truth window yet." : "No ground truth for this app yet (run the simulator)."}
      </p>
    );
  }

  const ticksY = [0, 0.25, 0.5, 0.75, 1].map((f) => f * geo.yMax);
  const span = (geo.t1 - geo.t0) / 1000;
  const ticksX = [0, 0.25, 0.5, 0.75, 1].map((f) => geo.t0 + f * (geo.t1 - geo.t0));
  const hp = hover === null ? null : data.series[hover];

  const onMove = (e: React.MouseEvent<SVGRectElement>) => {
    const box = e.currentTarget.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * (W - PAD.left - PAD.right) + PAD.left;
    let best = 0;
    geo.ts.forEach((t, i) => {
      if (Math.abs(geo.x(t) - px) < Math.abs(geo.x(geo.ts[best]!) - px)) best = i;
    });
    setHover(best);
  };

  return (
    <figure style={{ margin: 0 }}>
      <figcaption style={{ color: INK.primary, fontWeight: 600, marginBottom: 4 }}>
        Error vs ground truth (m){" "}
        <span style={{ color: INK.muted, fontWeight: 400, fontSize: 13 }}>demo only · {data.truth_file}</span>
      </figcaption>
      <div style={{ display: "flex", gap: 16, fontSize: 13, color: INK.secondary, marginBottom: 4, flexWrap: "wrap" }}>
        {SERIES.map((s) => (
          <span key={s.key} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <svg width="16" height="4" aria-hidden="true">
              <line x1="0" y1="2" x2="16" y2="2" stroke={s.color} strokeWidth="2" strokeLinecap="round" />
            </svg>
            {s.label}
          </span>
        ))}
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <svg width="4" height="12" aria-hidden="true">
            <line x1="2" y1="0" x2="2" y2="12" stroke={INK.marker} strokeWidth="1" strokeDasharray="2 2" />
          </svg>
          Applied change
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", maxWidth: W, height: "auto", display: "block" }} role="img"
        aria-label={`Error over ${Math.round(span)} seconds, ${geo.markers.length} applied changes`}>
        {ticksY.map((v) => (
          <g key={v}>
            <line x1={PAD.left} x2={W - PAD.right} y1={geo.y(v)} y2={geo.y(v)} stroke={INK.grid} strokeWidth="1" />
            <text x={PAD.left - 6} y={geo.y(v) + 4} textAnchor="end" fontSize="11" fill={INK.muted}>
              {Math.round(v)}
            </text>
          </g>
        ))}
        {ticksX.map((t, i) => (
          <text key={t} x={geo.x(t)} y={H - 8} textAnchor={i === 0 ? "start" : i === ticksX.length - 1 ? "end" : "middle"} fontSize="11" fill={INK.muted}>
            {new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
          </text>
        ))}
        {geo.markers.map((m) => (
          <g key={m.id}>
            <line x1={m.x} x2={m.x} y1={PAD.top} y2={H - PAD.bottom} stroke={INK.marker} strokeWidth="1" strokeDasharray="3 3" />
            <title>{`${m.kind} ${m.target} by ${m.actor}: ${m.reason}`}</title>
          </g>
        ))}
        {geo.paths.map((p) => (
          <path key={p.key} d={p.d} fill="none" stroke={p.color} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
        ))}
        {hp && hover !== null && (
          <g pointerEvents="none">
            <line x1={geo.x(geo.ts[hover]!)} x2={geo.x(geo.ts[hover]!)} y1={PAD.top} y2={H - PAD.bottom} stroke={INK.secondary} strokeWidth="1" />
            {SERIES.map((s) => {
              const v = hp[s.key as Key];
              return v === null ? null : (
                <circle key={s.key} cx={geo.x(geo.ts[hover]!)} cy={geo.y(v)} r="4" fill={s.color} stroke="#fcfcfb" strokeWidth="2" />
              );
            })}
          </g>
        )}
        <rect x={PAD.left} y={PAD.top} width={W - PAD.left - PAD.right} height={H - PAD.top - PAD.bottom}
          fill="transparent" onMouseMove={onMove} onMouseLeave={() => setHover(null)} />
      </svg>
      <div style={{ minHeight: 20, fontSize: 13, color: INK.secondary }}>
        {hp
          ? `${new Date(hp.t).toLocaleTimeString()} · hotspot ${hp.hotspot_error_m ?? "-"} m · point ${hp.point_error_m ?? "-"} m`
          : `${geo.markers.length} applied change${geo.markers.length === 1 ? "" : "s"} in view`}
      </div>
    </figure>
  );
}
