import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import clsx from "clsx";
import type { ReactNode } from "react";
import { money, q } from "../api";
import { Countdown } from "./brand";

const when = (iso: string) =>
  new Date(iso).toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });
const clock = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

function Item({ to, icon, title, detail, deadline, tone = "default" }: {
  to: "/waivers" | "/lineup" | "/live";
  icon: string;
  title: ReactNode;
  detail: ReactNode;
  deadline?: { label: string; at: string } | null;
  tone?: "default" | "accent";
}) {
  return (
    <Link
      to={to}
      className={clsx(
        "group flex items-center gap-4 rounded-xl border px-4 py-3 transition-colors",
        tone === "accent" ? "border-accent/30 bg-accent-wash hover:border-accent/60" : "border-line hover:bg-surface-2",
      )}
    >
      <svg viewBox="0 0 24 24" className="size-5 shrink-0 text-accent" fill="none" stroke="currentColor"
        strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d={icon} />
      </svg>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-semibold">{title}</div>
        <div className="truncate text-xs text-ink-2">{detail}</div>
      </div>
      {deadline && (
        <div className="shrink-0 text-right">
          <div className="text-sm font-semibold"><Countdown to={deadline.at} /></div>
          <div className="text-[11px] text-muted">{deadline.label} {when(deadline.at)}</div>
        </div>
      )}
      <span className="text-muted transition-transform group-hover:translate-x-0.5" aria-hidden>›</span>
    </Link>
  );
}

/** What to do next, ordered by deadline, with live countdowns. */
export function ActionCenter() {
  const tl = useQuery(q.timeline()).data;
  const rec = useQuery(q.recommend()).data;
  const lu = useQuery(q.lineup()).data;
  const plan = rec?.status === "ready" ? rec.data.plan : null;
  const gamesOn = tl?.sunday_main ? Date.now() >= new Date(tl.sunday_main).getTime() - 15 * 60_000 : false;

  const items: { at: number; node: ReactNode }[] = [];
  if (tl?.waiver_processing && tl.waiver_processed) {
    // Past the cutoff: claims are locked and Sleeper is running them.
    items.push({
      at: new Date(tl.waiver_processed).getTime(),
      node: (
        <Item
          key="w"
          to="/waivers"
          tone="accent"
          icon="M12 3v18M7 8h7.5a3 3 0 010 6H8.5a3 3 0 000 6H17"
          title="Waivers are processing"
          detail="Claims are locked; results should post in a few minutes"
          deadline={{ label: "results ~", at: tl.waiver_processed }}
        />
      ),
    });
  } else if (tl?.waiver_cutoff) {
    const results = tl.waiver_processed ? ` · results ~${clock(tl.waiver_processed)}` : "";
    items.push({
      at: new Date(tl.waiver_cutoff).getTime(),
      node: (
        <Item
          key="w"
          to="/waivers"
          tone="accent"
          icon="M12 3v18M7 8h7.5a3 3 0 010 6H8.5a3 3 0 000 6H17"
          title="Submit your claims"
          detail={
            (plan == null
              ? "Bid plan is computing…"
              : plan.length === 0
                ? "No claim worth placing right now"
                : `${plan.length} claim${plan.length > 1 ? "s" : ""} · ${money(plan.reduce((s, p) => s + p.bid, 0))} if all win · top: ${plan[0].player}`) + results
          }
          deadline={{ label: "claims due", at: tl.waiver_cutoff }}
        />
      ),
    });
  }
  if (tl?.next_kickoff) {
    items.push({
      at: new Date(tl.next_kickoff).getTime(),
      node: (
        <Item
          key="l"
          to="/lineup"
          icon="M4 6h16M4 12h16M4 18h10"
          title="Set your lineup"
          detail={
            lu == null
              ? "Checking your safest lineup…"
              : lu.swaps.length
                ? `${lu.swaps.length} suggested swap${lu.swaps.length > 1 ? "s" : ""}: start ${lu.swaps[0].in}`
                : "Your best lineup is already your safest"
          }
          deadline={{ label: "kickoff", at: tl.next_kickoff }}
        />
      ),
    });
  }
  items.push({
    at: tl?.sunday_main ? new Date(tl.sunday_main).getTime() : Infinity,
    node: (
      <Item
        key="v"
        to="/live"
        icon="M3 12h4l3-8 4 16 3-8h4"
        title={gamesOn ? "Games are on: watch the chop line" : "Live chop odds"}
        detail={gamesOn ? "Updates every minute from live scores" : "Turn on during games to see who’s sinking"}
        deadline={!gamesOn && tl?.sunday_main ? { label: "Sunday", at: tl.sunday_main } : null}
      />
    ),
  });
  items.sort((a, b) => a.at - b.at);
  return <div className="space-y-2">{items.map((i) => i.node)}</div>;
}
