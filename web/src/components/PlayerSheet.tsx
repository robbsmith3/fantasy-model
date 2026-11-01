import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import clsx from "clsx";
import { type ReactNode, useEffect, useRef } from "react";
import { money, num, pct, q, type PlayerDetail } from "../api";
import { PlayerHead } from "./brand";
import { ErrorBox, InjuryBadge, Skeleton } from "./ui";

/** Opens the player panel on any page (via ?player=ID, so Back closes it). */
export function usePlayerSheet() {
  const navigate = useNavigate();
  return {
    open: (id: string) => navigate({ to: ".", search: (prev) => ({ ...prev, player: id }) }),
    close: () => navigate({ to: ".", search: (prev) => ({ ...prev, player: undefined }), replace: true }),
  };
}

export function PlayerLink({ id, children, className }: { id: string; children: ReactNode; className?: string }) {
  const { open } = usePlayerSheet();
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation(); // e.g. inside an expandable table row
        open(id);
      }}
      className={clsx("text-left hover:underline focus-visible:underline", className)}
    >
      {children}
    </button>
  );
}

const SOURCE_LABEL: Record<string, string> = {
  sleeper: "Sleeper",
  espn: "ESPN",
  fantasypros: "FantasyPros",
  model: "Our model",
};

/** Next game's range: thin 10-90% bar, thick 25-75% bar, median dot, mean tick. */
function OutcomeRange({ o }: { o: NonNullable<PlayerDetail["outcome"]> }) {
  const hi = Math.max(o.p90 * 1.1, 10);
  const x = (v: number) => `${Math.min(v / hi, 1) * 100}%`;
  return (
    <div>
      <div className="relative h-10">
        <div className="absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-accent opacity-25" style={{ left: x(o.p10), width: `calc(${x(o.p90)} - ${x(o.p10)})` }} />
        <div className="absolute top-1/2 h-3 -translate-y-1/2 rounded-full bg-accent opacity-50" style={{ left: x(o.p25), width: `calc(${x(o.p75)} - ${x(o.p25)})` }} />
        <div className="absolute top-1/2 size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent ring-2 ring-[var(--surface)]" style={{ left: x(o.p50) }} />
        <div className="absolute top-1/2 h-5 w-0.5 -translate-x-1/2 -translate-y-1/2 rounded bg-ink" style={{ left: x(o.mean) }} title="expected" />
      </div>
      <div className="flex justify-between text-xs text-ink-2 tabular">
        <span>bad week {num(o.p10, 0)}</span>
        <span>typical {num(o.p50, 0)}</span>
        <span>big week {num(o.p90, 0)}</span>
      </div>
      <p className="mt-2 text-xs text-muted">
        Week {o.week}: expected {num(o.mean)} pts; half his outcomes fall between {num(o.p25, 0)} and {num(o.p75, 0)}.
        {o.p_dnp >= 0.01 && <> {pct(o.p_dnp, 0)} chance he doesn’t play.</>}
      </p>
    </div>
  );
}

function Body({ d }: { d: PlayerDetail }) {
  const sources = Object.keys(d.by_source);
  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <PlayerHead id={d.player_id} name={d.name} size={64} />
        <div className="min-w-0">
          <h2 className="truncate text-xl font-semibold tracking-tight">
            {d.name}
            <InjuryBadge status={d.injury} />
          </h2>
          <div className="text-sm text-ink-2">
            {d.pos} · {d.nfl_team ?? "FA"} ·{" "}
            {d.is_mine ? <span className="font-medium text-accent">on your team</span> : d.owner ?? "free agent"}
          </div>
        </div>
      </div>

      {d.outcome && (
        <section>
          <h3 className="mb-2 text-sm font-semibold">Range of outcomes, next game</h3>
          <OutcomeRange o={d.outcome} />
        </section>
      )}

      <section>
        <h3 className="mb-2 text-sm font-semibold">Projections</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-muted">
                <th className="py-1 text-left font-normal">Source</th>
                {d.weeks.map((w) => <th key={w} className="py-1 text-right font-normal">Wk {w}</th>)}
              </tr>
            </thead>
            <tbody className="tabular">
              <tr className="font-semibold">
                <td className="py-1">Consensus</td>
                {d.weeks.map((w) => <td key={w} className="py-1 text-right">{num(d.consensus[String(w)])}</td>)}
              </tr>
              {sources.map((src) => (
                <tr key={src} className="text-ink-2">
                  <td className="py-1">{SOURCE_LABEL[src] ?? src}</td>
                  {d.weeks.map((w) => <td key={w} className="py-1 text-right">{num(d.by_source[src][String(w)])}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-1 text-xs text-muted">Consensus is injury-adjusted; a 0 is usually a bye.</p>
      </section>

      <section>
        <h3 className="mb-2 text-sm font-semibold">Bids on him in your league</h3>
        {d.bids.length === 0 ? (
          <p className="text-sm text-ink-2">No waiver claims on him this season.</p>
        ) : (
          <ul className="divide-y divide-line text-sm">
            {d.bids.map((b, i) => (
              <li key={i} className="flex items-center justify-between py-1.5">
                <span className="text-ink-2">Wk {b.week} · {b.team}</span>
                <span className={clsx("tabular", b.won ? "font-semibold text-ink" : "text-muted")}>
                  {money(b.bid)} {b.won ? "won" : "lost"}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/** Mounted once in the layout; renders when ?player= is set. */
export function PlayerSheet() {
  const { player } = useSearch({ strict: false }) as { player?: string };
  const { close } = usePlayerSheet();
  const panel = useRef<HTMLDivElement>(null);
  const data = useQuery({ ...q.player(player ?? ""), enabled: !!player });

  useEffect(() => {
    if (!player) return;
    panel.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && close();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [player]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!player) return null;
  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-label="Player details">
      <button type="button" aria-label="Close" onClick={close} className="absolute inset-0 bg-black/40 backdrop-blur-[2px]" />
      <div
        ref={panel}
        tabIndex={-1}
        className={clsx(
          "absolute overflow-y-auto border-line bg-surface p-6 shadow-2xl outline-none",
          // phone: bottom sheet; desktop: right drawer
          "inset-x-0 bottom-0 max-h-[85dvh] rounded-t-3xl border-t pb-[calc(1.5rem+env(safe-area-inset-bottom))]",
          "md:inset-y-0 md:left-auto md:right-0 md:max-h-none md:w-[440px] md:rounded-none md:border-l md:border-t-0",
          "enter",
        )}
      >
        <div className="mb-4 flex items-center justify-between">
          <span className="mx-auto h-1 w-10 rounded-full bg-line md:hidden" aria-hidden />
          <button type="button" onClick={close} className="ml-auto rounded-lg px-2 py-1 text-sm text-ink-2 hover:bg-surface-2 hover:text-ink">
            Close
          </button>
        </div>
        {data.isError && <ErrorBox error={data.error} />}
        {data.isPending && <Skeleton className="h-96 w-full" />}
        {data.data && <Body d={data.data} />}
      </div>
    </div>
  );
}
