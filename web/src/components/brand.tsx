import clsx from "clsx";
import { useEffect, useRef, useState } from "react";

/** Blade mark: a slanted guillotine blade over a bar. */
export function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={clsx("size-7", className)} aria-hidden>
      <rect x="2" y="2" width="28" height="28" rx="8" fill="var(--accent)" />
      <path d="M9 9h14l-14 7z" fill="white" opacity="0.95" />
      <rect x="9" y="20" width="14" height="3" rx="1.5" fill="white" opacity="0.7" />
    </svg>
  );
}

function initials(name: string) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("");
}

function Fallback({ name, size }: { name: string; size: number }) {
  return (
    <span
      aria-hidden
      className="inline-flex shrink-0 items-center justify-center rounded-full bg-surface-2 font-semibold text-ink-2"
      style={{ width: size, height: size, fontSize: size * 0.38 }}
    >
      {initials(name)}
    </span>
  );
}

const DEF_IDS = /^[A-Z]{2,3}$/; // Sleeper DEF player ids are team abbreviations

/** Sleeper headshot, or the team logo for a DEF; initials if the image fails. */
export function PlayerHead({ id, name, size = 28 }: { id: string; name: string; size?: number }) {
  const [failed, setFailed] = useState(false);
  if (failed) return <Fallback name={name} size={size} />;
  const src = DEF_IDS.test(id)
    ? `https://sleepercdn.com/images/team_logos/nfl/${id.toLowerCase()}.png`
    : `https://sleepercdn.com/content/nfl/players/thumb/${id}.jpg`;
  return (
    <img
      src={src}
      alt=""
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
      width={size}
      height={size}
      className={clsx("shrink-0 rounded-full bg-surface-2", DEF_IDS.test(id) ? "object-contain p-0.5" : "object-cover object-top")}
      style={{ width: size, height: size }}
    />
  );
}

export function TeamAvatar({ src, name, size = 28 }: { src: string | null; name: string; size?: number }) {
  const [failed, setFailed] = useState(false);
  if (!src || failed) return <Fallback name={name} size={size} />;
  return (
    <img
      src={src}
      alt=""
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
      width={size}
      height={size}
      className="shrink-0 rounded-full bg-surface-2 object-cover"
      style={{ width: size, height: size }}
    />
  );
}

/* Weekly chop risk. Status colors are reserved for state and always ship with an icon and a
   label, never color alone (dataviz rule). Thresholds: an 11-team field averages ~9%/week. */
export type Risk = "safe" | "watch" | "danger";
export const riskOf = (p: number | null | undefined): Risk =>
  p == null || p < 0.05 ? "safe" : p < 0.15 ? "watch" : "danger";

const RISK = {
  safe: { label: "Safe", cls: "bg-good-wash text-good", d: "M5 12l4 4 10-10" },
  watch: { label: "Watch", cls: "bg-warning-wash text-warning-text", d: "M12 8v5M12 16.5v.5M12 3l9.5 17h-19z" },
  danger: { label: "Danger", cls: "bg-critical-wash text-critical", d: "M12 7v6M12 16.5v.5M12 3a9 9 0 110 18 9 9 0 010-18z" },
} as const;

export function RiskBadge({ p, compact = false }: { p: number | null | undefined; compact?: boolean }) {
  const r = RISK[riskOf(p)];
  return (
    <span className={clsx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium", r.cls)}>
      <svg viewBox="0 0 24 24" className="size-3.5" fill="none" stroke="currentColor" strokeWidth={2.2}
        strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d={r.d} />
      </svg>
      {!compact && r.label}
      {compact && <span className="sr-only">{r.label}</span>}
    </span>
  );
}

const reduceMotion = () =>
  typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/** Counts up from the previous value (0 on first render) in ~600 ms; instant if reduced motion. */
export function AnimatedNumber({ value, format }: { value: number; format: (v: number) => string }) {
  const [shown, setShown] = useState(reduceMotion() ? value : 0);
  const from = useRef(shown);
  useEffect(() => {
    if (reduceMotion()) {
      setShown(value);
      return;
    }
    const start = performance.now();
    const a = from.current;
    let raf = 0;
    const tick = (t: number) => {
      // rAF timestamps can precede `start`; clamp so the easing never goes negative.
      const k = Math.min(Math.max((t - start) / 600, 0), 1);
      const eased = 1 - (1 - k) ** 3;
      setShown(a + (value - a) * eased);
      if (k < 1) raf = requestAnimationFrame(tick);
      else from.current = value;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value]);
  return <span className="tabular-nums">{format(shown)}</span>;
}

/** "2d 6h", "5h 12m", "12m 30s"; ticks every second when under an hour, else every minute. */
export function Countdown({ to }: { to: string }) {
  const target = new Date(to).getTime();
  const [now, setNow] = useState(Date.now());
  const left = Math.max(0, target - now);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), left < 3_600_000 ? 1000 : 30_000);
    return () => clearInterval(id);
  }, [left < 3_600_000]); // eslint-disable-line react-hooks/exhaustive-deps
  const s = Math.floor(left / 1000);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const text = left === 0 ? "now" : d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m ${s % 60}s`;
  return <span className="tabular-nums">{text}</span>;
}
