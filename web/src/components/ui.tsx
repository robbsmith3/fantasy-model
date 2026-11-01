import clsx from "clsx";
import { type ReactNode, useEffect, useState } from "react";

export function PageHeader({ title, sub, right }: { title: ReactNode; sub?: ReactNode; right?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-[28px] font-semibold tracking-[-0.02em]">{title}</h1>
        {sub && <p className="mt-1 text-sm text-ink-2">{sub}</p>}
      </div>
      {right}
    </header>
  );
}

export function Card({ title, sub, right, children, className, pad = true, glow = false }: {
  title?: ReactNode;
  sub?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  pad?: boolean;
  glow?: boolean;
}) {
  return (
    <section className={clsx("card enter rounded-2xl border border-line bg-surface", glow && "glow", className)}>
      {(title || right) && (
        <div className="flex items-start justify-between gap-3 px-5 pt-4">
          <div>
            {title && <h2 className="text-[15px] font-semibold tracking-tight">{title}</h2>}
            {sub && <p className="mt-0.5 text-xs text-muted">{sub}</p>}
          </div>
          {right}
        </div>
      )}
      <div className={clsx(pad && "p-5", !pad && "pt-3")}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, hint, hero = false }: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  hero?: boolean;
}) {
  return (
    <div className="min-w-0">
      <div className="text-xs text-ink-2">{label}</div>
      <div className={clsx("mt-1 font-semibold", hero ? "text-6xl tracking-[-0.04em]" : "text-2xl tracking-tight")}>{value}</div>
      {hint && <div className="mt-1 text-xs text-muted">{hint}</div>}
    </div>
  );
}

const INJURY_TONE: Record<string, string> = {
  Questionable: "text-ink-2 border-line",
  Doubtful: "text-critical border-critical/40",
  Out: "text-critical border-critical/40",
  IR: "text-critical border-critical/40",
  PUP: "text-critical border-critical/40",
};
export function InjuryBadge({ status }: { status: string | null }) {
  if (!status) return null;
  const short = status === "Questionable" ? "Q" : status === "Doubtful" ? "D" : status;
  return (
    <span
      title={status}
      className={clsx(
        "ml-1.5 inline-flex items-center rounded border px-1 text-[10px] font-semibold leading-4",
        INJURY_TONE[status] ?? "text-ink-2 border-line",
      )}
    >
      {short}
    </span>
  );
}

export function Pos({ pos }: { pos: string }) {
  return <span className="inline-block w-9 text-xs font-medium text-muted">{pos}</span>;
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("skeleton", className)} aria-hidden />;
}

export function ErrorBox({ error }: { error: unknown }) {
  return (
    <div role="alert" className="rounded-lg border border-critical/40 bg-surface p-4 text-sm">
      <div className="font-medium text-critical">Couldn’t load this</div>
      <pre className="mt-2 whitespace-pre-wrap text-xs text-ink-2">{String((error as Error)?.message ?? error)}</pre>
    </div>
  );
}

/** Shown while a background computation runs server-side; counts up so it never feels stuck. */
export function Computing({ what, since, typical }: { what: string; since?: number; typical: string }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now() / 1000), 500);
    return () => clearInterval(id);
  }, []);
  const secs = since ? Math.max(0, Math.round(now - since)) : 0;
  return (
    <div className="flex items-center gap-3 rounded-lg border border-line bg-surface px-4 py-3 text-sm" aria-live="polite">
      <span className="relative flex size-2.5">
        <span className="absolute inline-flex size-full animate-ping rounded-full bg-accent opacity-60" />
        <span className="relative inline-flex size-2.5 rounded-full bg-accent" />
      </span>
      <span>
        Computing {what}… <span className="text-muted">{secs}s (usually {typical})</span>
      </span>
    </div>
  );
}

export function Th({ children, right = false, className }: { children?: ReactNode; right?: boolean; className?: string }) {
  return (
    <th
      scope="col"
      className={clsx(
        "sticky top-0 z-10 bg-surface px-3 py-2 text-xs font-medium text-muted",
        right ? "text-right" : "text-left",
        className,
      )}
    >
      {children}
    </th>
  );
}
export function Td({ children, right = false, className }: { children?: ReactNode; right?: boolean; className?: string }) {
  return (
    <td className={clsx("px-3 py-2 text-sm", right && "text-right tabular", className)}>{children}</td>
  );
}

export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-lg border border-line bg-surface p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          role="radio"
          aria-checked={o.value === value}
          onClick={() => onChange(o.value)}
          className={clsx(
            "rounded-md px-3 py-1 text-sm transition-colors",
            o.value === value ? "bg-surface-2 font-medium text-ink" : "text-ink-2 hover:text-ink",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
