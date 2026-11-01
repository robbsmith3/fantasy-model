import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { money, num, pct, q } from "../api";
import { PlayerHead } from "./brand";
import { PlayerLink } from "./PlayerSheet";
import { Card, Computing, ErrorBox, Stat, Td, Th } from "./ui";

/** Weekly report card: each waiver run graded against what the model predicted beforehand. */
export function ReportCard() {
  const rv = useQuery(q.review());
  if (rv.isError) return <ErrorBox error={rv.error} />;
  if (rv.data?.status === "error") return <ErrorBox error={rv.data.error} />;
  if (rv.data?.status !== "ready")
    return <Computing what="the report card" since={rv.data?.status === "computing" ? rv.data.since : undefined} typical="~10s" />;
  const r = rv.data.data;
  const s = r.summary;
  const cal = r.calibration;
  const chopRanks = r.chop.weeks.map((w) => w.rank_player);
  const avgRank = chopRanks.length ? chopRanks.reduce((a, b) => a + b, 0) / chopRanks.length : null;
  const recentAll = r.my_claims.filter((c) => r.latest_leg == null || c.week >= r.latest_leg - 1);
  const recent = recentAll.slice(0, 12);

  return (
    <div className="space-y-4">
      <Card title="Weekly report card" sub="Every waiver run since week 3, graded against what the model predicted beforehand (trained only on earlier weeks).">
        <div className="grid grid-cols-2 gap-5 sm:grid-cols-4">
          <Stat label="Auctions graded" value={s.auctions} hint="winning bid $20+" />
          <Stat label="Price inside predicted range" value={pct(s.coverage, 0)} hint="target 80%" />
          <Stat label="Typical miss (median)" value={money(s.median_bias)} hint={s.median_bias < 0 ? "prices ran higher than predicted" : "prices ran lower than predicted"} />
          <Stat label="Named the winner" value={pct(s.winner_hit, 0)} hint={`in top 3: ${pct(s.winner_top3, 0)}`} />
        </div>
        {avgRank != null && (
          <p className="mt-4 text-sm text-ink-2">
            Chop odds: the team that got chopped was, on average, our <b className="text-ink">#{num(avgRank, 1)}</b> most
            likely of ~{Math.round(r.chop.weeks.reduce((a, w) => a + w.teams, 0) / r.chop.weeks.length)} teams.
          </p>
        )}
      </Card>

      {r.latest_leg != null && (
        <Card title={`Latest waiver run: week ${r.latest_leg}`} sub="Predicted price range vs what actually won" pad={false}>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-line">
                  <Th sticky>Player</Th><Th right>Won for</Th><Th right>Predicted</Th><Th right>80% range</Th><Th>Result</Th><Th>Won by</Th><Th>We expected</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {r.latest.map((a) => {
                  const hit = a.pred_p10 <= a.price && a.price <= a.pred_p90;
                  return (
                    <tr key={a.player_id} className="bg-surface">
                      <Td sticky>
                        <span className="flex items-center gap-2.5">
                          <PlayerHead id={a.player_id} name={a.player} size={26} />
                          <PlayerLink id={a.player_id}>{a.player}</PlayerLink>
                        </span>
                      </Td>
                      <Td right className="font-semibold">{money(a.price)}</Td>
                      <Td right>{money(a.pred_median)}</Td>
                      <Td right className="text-ink-2">{money(a.pred_p10)}–{money(a.pred_p90)}</Td>
                      <Td>
                        <span className={clsx("rounded-full px-2 py-0.5 text-xs font-medium", hit ? "bg-good-wash text-good" : "bg-critical-wash text-critical")}>
                          {hit ? "✓ in range" : a.price > a.pred_p90 ? "✗ above" : "✗ below"}
                        </span>
                      </Td>
                      <Td>{a.winner}</Td>
                      <Td className={clsx(a.pred_winner === a.winner ? "font-semibold text-good" : "text-ink-2")}>{a.pred_winner}</Td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <Card title="Your recent claims" sub={recentAll.length > recent.length ? `Latest ${recent.length} of ${recentAll.length} from the last two runs` : undefined} pad={false}>
          {recent.length === 0 ? (
            <p className="px-5 pb-5 text-sm text-ink-2">No claims in the last two runs.</p>
          ) : (
            <table className="w-full">
              <thead><tr className="border-b border-line"><Th>Week</Th><Th>Player</Th><Th right>Your bid</Th><Th right>Winning bid</Th></tr></thead>
              <tbody className="divide-y divide-line">
                {recent.map((c, i) => (
                  <tr key={i}>
                    <Td className="text-muted">{c.week}</Td>
                    <Td>{c.player}</Td>
                    <Td right className={clsx(c.won ? "font-semibold text-good" : "text-ink-2")}>{money(c.bid)}{c.won ? " ✓" : ""}</Td>
                    <Td right className="text-ink-2">{c.winning_bid != null ? money(c.winning_bid) : "—"}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>

        <Card title="Corrections in use" sub={`Refit after each waiver run · ${new Date(cal.computed_at).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`}>
          <ul className="space-y-2 text-sm text-ink-2">
            <li>
              Price-range width ×<b className="text-ink">{cal.spread_k.toFixed(2)}</b>, level ×<b className="text-ink">{cal.level_s.toFixed(2)}</b>:
              chosen because they score best on past auctions (CRPS {num(cal.crps_after, 1)}; uncorrected {num(cal.crps_before, 1)}).
              {cal.spread_k === 1 && cal.level_s === 1 && " Widening the ranges to catch the rare blowout bids made predictions worse overall, so none is applied."}
            </li>
            {cal.pay_scale != null && (
              <li>
                Rivals’ future pickups priced at ×<b className="text-ink">{cal.pay_scale.toFixed(2)}</b> the bid model’s typical bid, matching what winners actually paid.
              </li>
            )}
          </ul>
        </Card>
      </div>
    </div>
  );
}
