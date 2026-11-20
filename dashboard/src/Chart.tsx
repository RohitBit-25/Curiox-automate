import { useRef, useState } from "react";

type Point = [string, number | null];

/** Line/area chart, hand-rolled SVG. Gaps for nulls; hover shows the nearest point. */
export function LineChart({ points, height = 200, format = (n: number) => String(n), color = "var(--signal)" }: {
  points: Point[]; height?: number; format?: (n: number) => string; color?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const vals = points.map((p) => p[1]).filter((v): v is number => v != null);
  if (points.length < 2 || vals.length < 2) {
    return <p className="muted small" style={{ padding: "24px 0" }}>Not enough data yet. Charts fill in as daily snapshots arrive.</p>;
  }
  const W = 600, H = height, pad = 8;
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (lo === hi) { lo -= 1; hi += 1; }
  const x = (i: number) => (i / (points.length - 1)) * W;
  const y = (v: number) => pad + (1 - (v - lo) / (hi - lo)) * (H - pad * 2);
  const segs: string[] = [];
  let cur = "";
  points.forEach(([, v], i) => {
    if (v == null) { if (cur) segs.push(cur); cur = ""; return; }
    cur += `${cur ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
  });
  if (cur) segs.push(cur);
  const line = segs.join(" ");
  const firstIdx = points.findIndex((p) => p[1] != null);
  const lastIdx = points.length - 1 - [...points].reverse().findIndex((p) => p[1] != null);
  const area = segs.length === 1 ? `${line}L${x(lastIdx)},${H}L${x(firstIdx)},${H}Z` : "";
  const grid = [0, 1, 2, 3].map((k) => lo + ((hi - lo) * k) / 3);
  const id = `g${Math.round(lo * 1000 + hi)}`;

  const onMove = (e: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect();
    setHover(Math.round(((e.clientX - r.left) / r.width) * (points.length - 1)));
  };
  const h = hover != null ? points[Math.max(0, Math.min(points.length - 1, hover))] : null;
  return (
    <div className="chart" ref={ref} onPointerMove={onMove} onPointerLeave={() => setHover(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ height: H }} role="img"
        aria-label={`Trend from ${format(vals[0])} to ${format(vals[vals.length - 1])}`}>
        <defs><linearGradient id={id} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity=".22" /><stop offset="1" stopColor={color} stopOpacity="0" />
        </linearGradient></defs>
        {grid.map((g) => <line key={g} x1="0" x2={W} y1={y(g)} y2={y(g)} stroke="var(--line)" vectorEffect="non-scaling-stroke" />)}
        {area && <path d={area} fill={`url(#${id})`} />}
        <path d={line} fill="none" stroke={color} strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
        {h && hover != null && <line x1={x(hover)} x2={x(hover)} y1="0" y2={H} stroke="var(--line-2)" vectorEffect="non-scaling-stroke" />}
      </svg>
      <div className="row spread xs muted" style={{ marginTop: 4 }}>
        <span>{points[0][0]}</span><span className="tnum">{format(lo)} to {format(hi)}</span><span>{points[points.length - 1][0]}</span>
      </div>
      {h && hover != null && <div className="tip" style={{ left: `${(hover / (points.length - 1)) * 100}%` }}>{h[0]}: {h[1] == null ? "no data" : format(h[1])}</div>}
    </div>
  );
}

export function Bars({ data, format = (n: number) => String(n) }: { data: { label: string; value: number | null; color?: string }[]; format?: (n: number) => string }) {
  const max = Math.max(1, ...data.map((d) => d.value ?? 0));
  if (!data.length) return <p className="muted small">No data yet.</p>;
  return (
    <div className="bars">
      {data.map((d) => (
        <div key={d.label} style={{ display: "contents" }}>
          <span className="dim">{d.label}</span>
          <div className="track"><i style={{ width: `${((d.value ?? 0) / max) * 100}%`, background: d.color }} /></div>
          <span className="tnum small">{d.value == null ? "–" : format(d.value)}</span>
        </div>
      ))}
    </div>
  );
}

export function Sparkline({ values, width = 120, height = 32 }: { values: number[]; width?: number; height?: number }) {
  if (values.length < 2) return null;
  const lo = Math.min(...values), hi = Math.max(...values) || 1;
  const d = values.map((v, i) => `${i ? "L" : "M"}${(i / (values.length - 1)) * width},${height - ((v - lo) / (hi - lo || 1)) * height}`).join("");
  return <svg width={width} height={height} aria-hidden style={{ display: "block", marginTop: 8 }}><path d={d} fill="none" stroke="var(--live)" strokeWidth="1.5" /></svg>;
}
