import clsx from "clsx";
import { type ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";

/* Specs (dataviz): 2px lines, >=8px end markers with a 2px surface ring, hairline grid, labels in
   text tokens (never the series color), legend for >=2 series, crosshair + one tooltip on lines,
   per-mark tooltips on bars, bars <=24px with a 4px rounded data end. */

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setW(Math.round(el.getBoundingClientRect().width)); // measure now; don't wait on the observer
    const ro = new ResizeObserver(([e]) => setW(Math.round(e.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

export type Series = { name: string; values: number[]; tone: "accent" | "neutral" };
const TONE = { accent: "var(--accent)", neutral: "var(--neutral-mark)" };

export function LineChart({ x, series, height = 220, fmt, xLabel }: {
  x: (string | number)[];
  series: Series[];
  height?: number;
  fmt: (v: number) => string;
  xLabel: (v: string | number) => string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const m = { t: 12, r: 96, b: 28, l: 40 };
  const iw = Math.max(width - m.l - m.r, 10);
  const ih = height - m.t - m.b;
  const sx = (i: number) => m.l + (x.length > 1 ? (i / (x.length - 1)) * iw : iw / 2);
  const sy = (v: number) => m.t + (1 - v) * ih; // 0..1 domain
  const ticks = [0, 0.25, 0.5, 0.75, 1];

  const onMove = (e: React.PointerEvent<SVGRectElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - r.left;
    const i = Math.round((px / r.width) * (x.length - 1));
    setHover(Math.min(Math.max(i, 0), x.length - 1));
  };

  return (
    <div ref={ref} className="relative w-full">
      {series.length > 1 && (
        <div className="mb-2 flex flex-wrap gap-4 text-xs text-ink-2" aria-hidden>
          {series.map((s) => (
            <span key={s.name} className="inline-flex items-center gap-1.5">
              <span className="inline-block h-0.5 w-4 rounded" style={{ background: TONE[s.tone] }} />
              {s.name}
            </span>
          ))}
        </div>
      )}
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={series.map((s) => s.name).join(" vs ")}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={m.l} x2={m.l + iw} y1={sy(t)} y2={sy(t)} stroke="var(--grid)" strokeWidth={1} />
              <text x={m.l - 8} y={sy(t)} dy="0.32em" textAnchor="end" className="tabular" fontSize={11} fill="var(--muted)">
                {fmt(t)}
              </text>
            </g>
          ))}
          {x.map((v, i) =>
            // Thin the labels so neighbours keep >= 48px apart at any width.
            i % Math.max(1, Math.ceil(x.length / Math.max(2, Math.floor(iw / 48)))) === 0 ? (
              <text key={i} x={sx(i)} y={height - 8} textAnchor="middle" fontSize={11} fill="var(--muted)">
                {xLabel(v)}
              </text>
            ) : null,
          )}
          {series
            .slice()
            .sort((a) => (a.tone === "accent" ? 1 : -1)) // accent drawn on top
            .map((s) => (
              <g key={s.name}>
                <path
                  d={s.values.map((v, i) => `${i ? "L" : "M"}${sx(i)},${sy(v)}`).join(" ")}
                  fill="none"
                  stroke={TONE[s.tone]}
                  strokeWidth={2}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
                <circle
                  cx={sx(s.values.length - 1)}
                  cy={sy(s.values[s.values.length - 1])}
                  r={4}
                  fill={TONE[s.tone]}
                  stroke="var(--surface)"
                  strokeWidth={2}
                />
                <text
                  x={sx(s.values.length - 1) + 10}
                  y={sy(s.values[s.values.length - 1])}
                  dy="0.32em"
                  fontSize={11}
                  fill="var(--ink-2)"
                >
                  {fmt(s.values[s.values.length - 1])} {s.name.split(" ")[0]}
                </text>
              </g>
            ))}
          {hover != null && (
            <line x1={sx(hover)} x2={sx(hover)} y1={m.t} y2={m.t + ih} stroke="var(--axis)" strokeWidth={1} />
          )}
          <rect
            x={m.l}
            y={m.t}
            width={iw}
            height={ih}
            fill="transparent"
            onPointerMove={onMove}
            onPointerLeave={() => setHover(null)}
          />
        </svg>
      )}
      {hover != null && width > 0 && (
        <Tooltip x={Math.min(sx(hover) + 12, width - 170)} y={m.t + 24}>
          <div className="mb-1 text-xs text-muted">{xLabel(x[hover])}</div>
          {series.map((s) => (
            <div key={s.name} className="flex items-center gap-2 text-xs">
              <span className="inline-block h-0.5 w-3 rounded" style={{ background: TONE[s.tone] }} />
              <span className="tabular font-semibold text-ink">{fmt(s.values[hover])}</span>
              <span className="text-ink-2">{s.name}</span>
            </div>
          ))}
        </Tooltip>
      )}
    </div>
  );
}

function Tooltip({ x, y, children }: { x: number; y: number; children: ReactNode }) {
  return (
    <div
      role="tooltip"
      className="pointer-events-none absolute z-20 min-w-40 rounded-lg border border-line bg-surface px-3 py-2 shadow-lg"
      style={{ left: x, top: y }}
    >
      {children}
    </div>
  );
}

export type BarRow = {
  key: string;
  label: ReactNode;
  value: number;
  highlight?: boolean;
  detail?: ReactNode;
  icon?: ReactNode;
  badge?: ReactNode;
};

/** Horizontal bars, one series. Highlighted rows use the accent; the rest recede to neutral. */
export function BarList({ rows, max, fmt, onSelect }: {
  rows: BarRow[];
  max?: number;
  fmt: (v: number) => string;
  onSelect?: (key: string) => void;
}) {
  const top = max ?? Math.max(...rows.map((r) => r.value), 1e-9);
  const [hover, setHover] = useState<string | null>(null);
  // Bars grow in from the baseline on first paint (CSS transition; off under reduced motion).
  const [grown, setGrown] = useState(false);
  useEffect(() => {
    const id = requestAnimationFrame(() => setGrown(true));
    return () => cancelAnimationFrame(id);
  }, []);
  return (
    <ul className="space-y-1.5">
      {rows.map((r) => {
        const w = grown ? Math.max((r.value / top) * 100, r.value > 0 ? 1 : 0) : 0;
        return (
          <li key={r.key} className="relative">
            <button
              type="button"
              onClick={() => onSelect?.(r.key)}
              onPointerEnter={() => setHover(r.key)}
              onPointerLeave={() => setHover(null)}
              onFocus={() => setHover(r.key)}
              onBlur={() => setHover(null)}
              className={clsx(
                "grid w-full grid-cols-[minmax(0,12rem)_1fr] items-center gap-3 rounded-lg px-1.5 py-1.5 text-left",
                onSelect && "cursor-pointer hover:bg-surface-2",
              )}
            >
              <span className="flex min-w-0 items-center gap-2">
                {r.icon}
                <span className={clsx("truncate text-sm", r.highlight ? "font-semibold" : "text-ink-2")}>{r.label}</span>
              </span>
              <span className="flex items-center gap-2">
                <span className="relative h-4 flex-1">
                  <span
                    className="absolute inset-y-0.5 left-0 rounded-r-[4px] transition-[width] duration-700 ease-out"
                    style={{ width: `${w}%`, background: r.highlight ? "var(--accent)" : "var(--neutral-mark)" }}
                  />
                </span>
                <span className="w-14 text-right text-xs tabular text-ink-2">{fmt(r.value)}</span>
                {r.badge}
              </span>
            </button>
            {hover === r.key && r.detail && (
              <div className="pointer-events-none absolute right-0 top-full z-20 mt-1 rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow-lg">
                {r.detail}
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}
