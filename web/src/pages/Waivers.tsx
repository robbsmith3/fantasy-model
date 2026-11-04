import { keepPreviousData, useQuery, useSuspenseQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import clsx from "clsx";
import { Fragment, useState } from "react";
import { money, num, pct, planSpend, q, type PlanRow, type SheetRow } from "../api";
import { BidCurve } from "../components/charts";
import { PlayerLink } from "../components/PlayerSheet";
import { Countdown, PlayerHead } from "../components/brand";
import { Card, Computing, ErrorBox, InjuryBadge, PageHeader, Pos, Stat, Td, Th } from "../components/ui";

export default function Waivers() {
  const { chop } = useSearch({ strict: false }) as { chop?: string };
  const navigate = useNavigate();
  const { data: L } = useSuspenseQuery(q.league());
  const tl = useQuery(q.timeline()).data;
  // Keep the last sheet on screen (dimmed) while another scenario computes: no layout jump.
  const rec = useQuery({ ...q.recommend(chop), placeholderData: keepPreviousData });
  const ready = rec.data?.status === "ready" ? rec.data.data : null;
  const stale = rec.isPlaceholderData || rec.data?.status === "computing";
  // undefined = default (top candidate explained), null = user closed everything.
  const [openId, setOpen] = useState<string | null | undefined>(undefined);
  const candidates = L.teams
    .filter((t) => t.alive && !t.is_me)
    .sort((a, b) => (b.p_chop_next ?? 0) - (a.p_chop_next ?? 0));

  return (
    <>
      <PageHeader
        title="Waivers"
        sub="Bids that trade win probability against what your FAAB could buy later. Each claim in the plan is valued given the ones above it may win."
        right={
          <label className="flex items-center gap-2 text-sm">
            <span className="text-ink-2">Preview</span>
            <select
              value={chop ?? ""}
              onChange={(e) =>
                navigate({ to: "/waivers", search: { chop: e.target.value || undefined }, replace: true })
              }
              className="rounded-lg border border-line bg-surface px-3 py-1.5 text-sm"
            >
              <option value="">{L.chop_pending ? "Current pool (no chop yet)" : "Current pool"}</option>
              {candidates.map((t) => (
                <option key={t.team_id} value={t.team_id}>
                  If {t.name} is chopped ({pct(t.p_chop_next, 0)})
                </option>
              ))}
            </select>
          </label>
        }
      />

      {tl?.waiver_cutoff && <Deadline cutoff={tl.waiver_cutoff} processed={tl.waiver_processed} processing={tl.waiver_processing} />}
      {rec.isError && <ErrorBox error={rec.error} />}
      {rec.data?.status === "error" && <ErrorBox error={rec.data.error} />}
      {rec.data?.status === "computing" && (
        <div className="mb-4"><Computing what="the bid sheet" since={rec.data.since} typical="~1 min" /></div>
      )}
      {!rec.data && !rec.isError && <Computing what="the bid sheet" typical="~1 min" />}

      {ready && (
        <div className={clsx("transition-opacity", stale && "opacity-50")}>
          <Card className="mb-4">
            <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
              <Stat label="Chance to win" value={pct(ready.summary.p_win)} />
              <Stat label="FAAB" value={money(ready.summary.faab)} />
              <Stat label="Spending $100 costs" value={pct(ready.summary.cost_100, 2)} hint="of win probability" />
              <Stat label="Spending half costs" value={pct(ready.summary.cost_half, 2)} hint="the more you spend, the more each $ costs" />
            </div>
          </Card>

          <Card
            title="Claim plan"
            sub="Claims sharing a drop are backups: once one lands, Sleeper fails the rest at no charge. The most this can spend stays within your FAAB."
            right={ready.plan.length > 0 && <ClaimActions plan={ready.plan} leagueId={L.league_id} />}
            pad={false}
            className="mb-4"
          >
            {ready.plan.length === 0 ? (
              <p className="px-5 pb-5 text-sm text-ink-2">No claim worth placing.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead><tr className="border-b border-line"><Th sticky>Player</Th><Th right>Bid</Th><Th right>Chance to get</Th><Th right>Win prob added</Th><Th right>Walk away</Th><Th>Drop</Th></tr></thead>
                  <tbody className="divide-y divide-line">
                    {ready.plan.map((p) => (
                      <tr key={p.player_id} className="bg-surface">
                        <Td sticky><span className="flex items-center gap-2.5"><span className="w-4 text-xs text-muted">{p.order}</span><PlayerHead id={p.player_id} name={p.player} size={30} /><span><PlayerLink id={p.player_id} className="font-medium">{p.player}</PlayerLink> <span className="text-xs text-muted">{p.pos}</span></span></span></Td>
                        <Td right className="font-semibold">{money(p.bid)}</Td>
                        <Td right>{pct(p.p_get, 0)}</Td>
                        <Td right>{pct(p.d_p_win, 2)}</Td>
                        <Td right className="text-ink-2">{p.walk_away == null ? "—" : money(p.walk_away)}</Td>
                        <Td className="text-ink-2">{p.drop ?? "—"}{p.backup_for && <span className="block text-xs text-muted">only if {p.backup_for} misses</span>}</Td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot><tr className="border-t border-line bg-surface"><Td sticky className="text-muted">Most this can spend</Td><Td right className="font-semibold">{money(planSpend(ready.plan))}</Td><Td /><Td /><Td /><Td /></tr></tfoot>
                </table>
              </div>
            )}
          </Card>

          <Card title="Every candidate on its own" sub="Tap a row for why: lineup points added (next 4 weeks), win probability added, and blocking value. Walk away: the most he’s worth once you count what the money could buy later." pad={false}>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-line">
                    <Th sticky>Player</Th><Th right>+pts</Th><Th right>+win</Th><Th right>Block</Th><Th right>Bid</Th><Th right>Chance</Th><Th right>Market</Th><Th right>Walk away</Th><Th>Drop</Th>
                  </tr>
                </thead>
                <tbody>
                  {ready.sheet.map((r) => {
                    const open = openId === undefined ? ready.sheet[0]?.player_id : openId;
                    const isOpen = open === r.player_id;
                    return (
                      <Fragment key={r.player_id}>
                        <tr
                          className={clsx("cursor-pointer border-t border-line", isOpen ? "bg-surface-2" : "bg-surface hover:bg-surface-2")}
                          onClick={() => setOpen(isOpen ? null : r.player_id)}
                        >
                          <Td sticky><span className="flex items-center gap-2.5">
                            <button
                              type="button"
                              aria-expanded={isOpen}
                              aria-label={`Why ${money(r.bid)} for ${r.player}?`}
                              className={clsx("w-3 text-muted transition-transform", isOpen && "rotate-90")}
                            >
                              ›
                            </button>
                            <PlayerHead id={r.player_id} name={r.player} size={28} /><span><Pos pos={r.pos} /><PlayerLink id={r.player_id}>{r.player}</PlayerLink><InjuryBadge status={r.injury} /></span></span></Td>
                          <Td right>{num(r.pts_gain)}</Td>
                          <Td right>{pct(r.d_p_win, 2)}</Td>
                          <Td right className="text-ink-2">{pct(r.deny, 2)}</Td>
                          <Td right className="font-semibold">{money(r.bid)}</Td>
                          <Td right>{pct(r.p_win_claim, 0)}</Td>
                          <Td right className="text-ink-2">{money(r.market_median)}</Td>
                          <Td right className="text-ink-2">{money(r.walk_away)}</Td>
                          <Td className="text-ink-2">{r.drop}</Td>
                        </tr>
                        {isOpen && (
                          <tr className="bg-surface-2/50">
                            <td colSpan={9} className="px-4 pb-5 pt-2">
                              <WhyBid r={r} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>

          <Card
            title="Projected bids: most likely claimed"
            sub="The RBs, WRs and TEs rivals are likeliest to claim, and what it would take to win if they do. Comes from 2025 and 2026 bidding, which doesn't account for injuries or sudden role changes."
            pad={false}
            className="mt-4"
          >
            {ready.market.length === 0 ? (
              <p className="px-5 pb-5 text-sm text-ink-2">No rival bids projected.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead><tr className="border-b border-line"><Th sticky>Player</Th><Th right>Chance claimed</Th><Th right>Projected bid</Th><Th right>80% range</Th><Th>Likeliest winner</Th></tr></thead>
                  <tbody className="divide-y divide-line">
                    {ready.market.map((m) => (
                      <tr key={m.player_id} className="bg-surface">
                        <Td sticky><span className="flex items-center gap-2.5"><PlayerHead id={m.player_id} name={m.player} size={28} /><span><Pos pos={m.pos} /><PlayerLink id={m.player_id}>{m.player}</PlayerLink><InjuryBadge status={m.injury} /></span></span></Td>
                        <Td right>{pct(m.p_claimed, 0)}</Td>
                        <Td right className="font-semibold">{money(m.bid)}</Td>
                        <Td right className="text-ink-2">{money(m.bid_lo)}–{money(m.bid_hi)}</Td>
                        <Td className="text-ink-2">{m.rival} <span className="text-xs text-muted">{pct(m.rival_p, 0)}</span></Td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      )}
    </>
  );
}

function WhyBid({ r }: { r: SheetRow }) {
  const reasons = [
    <>Adds <b>{num(r.pts_gain)} points</b> to your starting lineup over the next 4 weeks.</>,
    <>That’s <b>+{pct(r.d_p_win, 2)}</b> on your chance to win the league.</>,
    r.deny > 0.0005 ? (
      <>
        Winning him also keeps him away from{" "}
        {r.rival ? <b>{r.rival}</b> : "a rival"}
        {r.rival_p != null && <> (most likely to land him otherwise, {pct(r.rival_p, 0)})</>}: worth another{" "}
        <b>{pct(r.deny, 2)}</b>.
      </>
    ) : null,
    r.market_median >= 1 ? (
      <>Rivals’ top bid is most likely around <b>{money(r.market_median)}</b>.</>
    ) : (
      <>Rivals rarely bid on him, so a small bid usually wins.</>
    ),
    <>
      At <b>{money(r.bid)}</b> you get him <b>{pct(r.p_win_claim, 0)}</b> of the time. Bidding more raises that, but every
      extra dollar is win chance you could spend on a later dump; {money(r.bid)} costs about {pct(r.money_cost, 2)} of it.
      Above <b>{money(r.walk_away)}</b> he isn’t worth the money.
    </>,
  ].filter(Boolean);
  return (
    <div className="grid gap-5 md:grid-cols-[1fr_1.1fr]">
      <ul className="space-y-2 text-sm text-ink-2 [&_b]:font-semibold [&_b]:text-ink">
        {reasons.map((x, i) => (
          <li key={i} className="flex gap-2">
            <span className="mt-2 size-1.5 shrink-0 rounded-full bg-accent" />
            <span>{x}</span>
          </li>
        ))}
      </ul>
      <div>
        <div className="mb-1 text-xs text-muted">Chance you win the claim, by bid</div>
        <BidCurve curve={r.curve} bid={r.bid} pGet={r.p_win_claim} median={r.market_median} />
      </div>
    </div>
  );
}

/** Copy the plan as text for entering claims in Sleeper, and a link to the league. */
function ClaimActions({ plan, leagueId }: { plan: PlanRow[]; leagueId: string }) {
  const [state, setState] = useState<"idle" | "copied" | "manual">("idle");
  const text = [
    "Waiver claims (FAAB), submit in this order:",
    ...plan.map((p) => `${p.order}. ${p.player} (${p.pos}): bid $${p.bid}, drop ${p.drop ?? "nobody"}${p.backup_for ? ` (only if ${p.backup_for} misses)` : ""}`),
    `Most this can spend: $${planSpend(plan)}`,
  ].join("\n");
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
      setTimeout(() => setState("idle"), 2000);
    } catch {
      setState("manual"); // clipboard blocked: show the text to copy by hand
    }
  };
  return (
    <div className="flex flex-col items-end gap-2">
      <div className="flex gap-2">
        <button
          type="button"
          onClick={copy}
          className="rounded-lg border border-line bg-surface px-3 py-1.5 text-sm font-medium hover:bg-surface-2"
        >
          {state === "copied" ? "Copied ✓" : "Copy claims"}
        </button>
        <a
          href={`https://sleeper.com/leagues/${leagueId}`}
          target="_blank"
          rel="noreferrer"
          className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
        >
          Open Sleeper ↗
        </a>
      </div>
      {state === "manual" && (
        <textarea
          readOnly
          value={text}
          rows={plan.length + 2}
          onFocus={(e) => e.currentTarget.select()}
          className="w-72 rounded-lg border border-line bg-surface p-2 text-xs"
        />
      )}
    </div>
  );
}

const at = (iso: string, opts: Intl.DateTimeFormatOptions) => new Date(iso).toLocaleString([], opts);

/** Claims lock at the cutoff; Sleeper posts results a few minutes later. */
function Deadline({ cutoff, processed, processing }: { cutoff: string; processed: string | null; processing: boolean }) {
  const time = { hour: "numeric", minute: "2-digit" } as const;
  return (
    <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-line bg-surface px-4 py-2.5 text-sm">
      {processing ? (
        <>
          <span className="font-semibold">Claims are locked: waivers are processing</span>
          {processed && <span className="text-ink-2">results ~{at(processed, time)} (<Countdown to={processed} />)</span>}
        </>
      ) : (
        <>
          <span>
            Claims due <span className="font-semibold">{at(cutoff, { weekday: "short", ...time })}</span>
          </span>
          <span className="rounded-full bg-accent-wash px-2 py-0.5 text-xs font-semibold text-accent">
            <Countdown to={cutoff} /> left
          </span>
          {processed && <span className="text-ink-2">results ~{at(processed, time)}</span>}
        </>
      )}
    </div>
  );
}
