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

/** P(get the player) vs bid: one series (no legend), area wash, recommended-bid marker,
    market-median reference line, crosshair tooltip. */
export function BidCurve({ curve, bid, pGet, median, height = 180 }: {
  curve: [number, number][];
  bid: number;
  pGet: number;
  median: number;
  height?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const m = { t: 14, r: 16, b: 26, l: 40 };
  const iw = Math.max(width - m.l - m.r, 10);
  const ih = height - m.t - m.b;
  const maxX = Math.max(curve[curve.length - 1]?.[0] ?? 1, bid, 1);
  const sx = (v: number) => m.l + (v / maxX) * iw;
  const sy = (v: number) => m.t + (1 - v) * ih;
  const line = curve.map(([x, y], i) => `${i ? "L" : "M"}${sx(x)},${sy(y)}`).join(" ");
  const area = `${line} L${sx(curve[curve.length - 1][0])},${sy(0)} L${sx(0)},${sy(0)} Z`;
  const xticks = [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(f * maxX));
  const nearest = (px: number) => {
    const v = ((px - m.l) / iw) * maxX;
    let best = 0;
    curve.forEach(([x], i) => {
      if (Math.abs(x - v) < Math.abs(curve[best][0] - v)) best = i;
    });
    return best;
  };
  return (
    <div ref={ref} className="relative w-full">
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={`Chance to win the claim by bid; recommended $${bid}`}>
          {[0, 0.5, 1].map((t) => (
            <g key={t}>
              <line x1={m.l} x2={m.l + iw} y1={sy(t)} y2={sy(t)} stroke="var(--grid)" />
              <text x={m.l - 8} y={sy(t)} dy="0.32em" textAnchor="end" fontSize={11} fill="var(--muted)" className="tabular">
                {Math.round(t * 100)}%
              </text>
            </g>
          ))}
          {xticks.map((x) => (
            <text key={x} x={sx(x)} y={height - 6} textAnchor="middle" fontSize={11} fill="var(--muted)" className="tabular">
              ${x}
            </text>
          ))}
          <path d={area} fill="var(--accent)" opacity={0.1} />
          <path d={line} fill="none" stroke="var(--accent)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          {median > 0 && median <= maxX && (
            <g>
              <line x1={sx(median)} x2={sx(median)} y1={m.t} y2={m.t + ih} stroke="var(--axis)" />
              <text x={sx(median) + 4} y={m.t + 10} fontSize={11} fill="var(--muted)">market ~${Math.round(median)}</text>
            </g>
          )}
          <circle cx={sx(bid)} cy={sy(pGet)} r={5} fill="var(--accent)" stroke="var(--surface)" strokeWidth={2} />
          <text x={sx(bid)} y={sy(pGet) - 10} textAnchor={sx(bid) > width - 90 ? "end" : "middle"} fontSize={11} fontWeight={600} fill="var(--ink)">
            bid ${bid} · {Math.round(pGet * 100)}%
          </text>
          {hover != null && (
            <line x1={sx(curve[hover][0])} x2={sx(curve[hover][0])} y1={m.t} y2={m.t + ih} stroke="var(--axis)" />
          )}
          <rect
            x={m.l}
            y={m.t}
            width={iw}
            height={ih}
            fill="transparent"
            onPointerMove={(e) => setHover(nearest(e.clientX - e.currentTarget.getBoundingClientRect().left + m.l))}
            onPointerLeave={() => setHover(null)}
          />
        </svg>
      )}
      {hover != null && width > 0 && (
        <Tooltip x={Math.min(sx(curve[hover][0]) + 12, width - 170)} y={m.t}>
          <div className="text-xs">
            <span className="tabular font-semibold text-ink">{Math.round(curve[hover][1] * 100)}%</span>
            <span className="text-ink-2"> chance at ${curve[hover][0]}</span>
          </div>
        </Tooltip>
      )}
    </div>
  );
}

export type ChopRow = {
  key: string;
  label: ReactNode;
  icon?: ReactNode;
  highlight?: boolean;
  low: number;
  mid: number;
  high: number;
  now: number;
  right: ReactNode;
  detail: ReactNode;
};

/** Each team's likely final-score range on one shared scale, lowest first, with the band
    where the chopped (lowest) score is likely to land. */
export function ChopLine({ rows, line, onSelect }: {
  rows: ChopRow[];
  line: { p10: number; p50: number; p90: number };
  onSelect?: (key: string) => void;
}) {
  const [hover, setHover] = useState<string | null>(null);
  // Scale to the finish ranges only. Early in the week "points so far" sits far below them and
  // would squeeze every range to the right, so a tick below the axis is pinned to its left edge.
  const lo = Math.max(0, Math.min(line.p10, ...rows.map((r) => r.low)) - 5);
  const hi = Math.max(line.p90, ...rows.map((r) => r.high)) + 5;
  const x = (v: number) => `${((v - lo) / (hi - lo)) * 100}%`;
  const ticks = Array.from({ length: 5 }, (_, i) => Math.round(lo + ((hi - lo) * i) / 4));
  return (
    <div>
      <div className="grid grid-cols-[minmax(0,11rem)_1fr_5.5rem] items-end gap-3 pb-1 text-[11px] text-muted">
        <span />
        <div className="relative h-4">
          {ticks.map((t) => (
            <span key={t} className="absolute -translate-x-1/2 tabular" style={{ left: x(t) }}>{t}</span>
          ))}
        </div>
        <span className="text-right">chopped</span>
      </div>
      <div className="relative">
        {/* the chop band and line span every row */}
        <div className="pointer-events-none absolute inset-y-0 grid w-full grid-cols-[minmax(0,11rem)_1fr_5.5rem] gap-3">
          <span />
          <div className="relative">
            <div className="absolute inset-y-0 bg-critical-wash" style={{ left: x(line.p10), width: `calc(${x(line.p90)} - ${x(line.p10)})` }} />
            <div className="absolute inset-y-0 w-0.5 -translate-x-1/2 bg-critical" style={{ left: x(line.p50) }} />
          </div>
          <span />
        </div>
        <ul className="relative">
          {rows.map((r) => (
            <li key={r.key} className="relative">
              <button
                type="button"
                onClick={() => onSelect?.(r.key)}
                onPointerEnter={() => setHover(r.key)}
                onPointerLeave={() => setHover(null)}
                onFocus={() => setHover(r.key)}
                onBlur={() => setHover(null)}
                className={clsx(
                  "grid w-full grid-cols-[minmax(0,11rem)_1fr_5.5rem] items-center gap-3 rounded-lg py-2 text-left",
                  onSelect && "cursor-pointer hover:bg-surface-2/60",
                )}
              >
                <span className="flex min-w-0 items-center gap-2">
                  {r.icon}
                  <span className={clsx("truncate text-sm", r.highlight ? "font-semibold" : "text-ink-2")}>{r.label}</span>
                </span>
                <span className="relative h-5">
                  <span
                    className="absolute top-1/2 h-2 -translate-y-1/2 rounded-full"
                    style={{
                      left: x(r.low),
                      width: `calc(${x(r.high)} - ${x(r.low)})`,
                      background: r.highlight ? "var(--accent)" : "var(--neutral-mark)",
                      opacity: 0.35,
                    }}
                  />
                  {r.now > 0 && (
                    <span
                      className={clsx("absolute top-1/2 h-3.5 w-0.5 -translate-x-1/2 -translate-y-1/2 rounded bg-ink-2", r.now < lo && "opacity-40")}
                      style={{ left: x(Math.max(r.now, lo)) }}
                      title={`points so far: ${r.now.toFixed(1)}`}
                    />
                  )}
                  <span
                    className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-[var(--surface)]"
                    style={{ left: x(r.mid), background: r.highlight ? "var(--accent)" : "var(--neutral-mark)" }}
                  />
                </span>
                <span className="flex items-center justify-end gap-1.5 text-xs tabular text-ink-2">{r.right}</span>
              </button>
              {hover === r.key && (
                <div className="pointer-events-none absolute left-1/3 top-full z-20 -mt-1 rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow-lg">
                  {r.detail}
                </div>
              )}
            </li>
          ))}
        </ul>
      </div>
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-muted">
        <span className="inline-flex items-center gap-1.5"><span className="inline-block h-2 w-5 rounded-full bg-[var(--neutral-mark)] opacity-40" />likely final (80%)</span>
        <span className="inline-flex items-center gap-1.5"><span className="inline-block size-2.5 rounded-full bg-[var(--neutral-mark)]" />expected final</span>
        <span className="inline-flex items-center gap-1.5"><span className="inline-block h-3 w-0.5 rounded bg-ink-2" />points so far</span>
        <span className="inline-flex items-center gap-1.5"><span className="inline-block h-3 w-0.5 bg-critical" />chop line ≈ {Math.round(line.p50)}</span>
      </div>
    </div>
  );
}
