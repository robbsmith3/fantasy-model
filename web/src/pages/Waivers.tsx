import { keepPreviousData, useQuery, useSuspenseQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import clsx from "clsx";
import { money, num, pct, q } from "../api";
import { Card, Computing, ErrorBox, InjuryBadge, PageHeader, Pos, Stat, Td, Th } from "../components/ui";

export default function Waivers() {
  const { chop } = useSearch({ strict: false }) as { chop?: string };
  const navigate = useNavigate();
  const { data: L } = useSuspenseQuery(q.league());
  // Keep the last sheet on screen (dimmed) while another scenario computes: no layout jump.
  const rec = useQuery({ ...q.recommend(chop), placeholderData: keepPreviousData });
  const ready = rec.data?.status === "ready" ? rec.data.data : null;
  const stale = rec.isPlaceholderData || rec.data?.status === "computing";
  const candidates = L.teams
    .filter((t) => t.alive && !t.is_me)
    .sort((a, b) => (b.p_chop_next ?? 0) - (a.p_chop_next ?? 0));

  return (
    <>
      <PageHeader
        title="Waivers"
        sub="Bids that trade win probability against FAAB. Each claim in the plan is valued given the ones above it may win."
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

      {rec.isError && <ErrorBox error={rec.error} />}
      {rec.data?.status === "error" && <ErrorBox error={rec.data.error} />}
      {rec.data?.status === "computing" && (
        <div className="mb-4"><Computing what="the bid sheet" since={rec.data.since} typical="~20s" /></div>
      )}
      {!rec.data && !rec.isError && <Computing what="the bid sheet" typical="~20s" />}

      {ready && (
        <div className={clsx("transition-opacity", stale && "opacity-50")}>
          <Card className="mb-4">
            <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
              <Stat label="Chance to win" value={pct(ready.summary.p_win)} />
              <Stat label="FAAB" value={money(ready.summary.faab)} />
              <Stat label="$100 of FAAB is worth" value={pct(ready.summary.lambda_per_100, 2)} hint="of win probability" />
              <Stat label="A saved $ is worth" value={pct(ready.summary.faab_discount, 0)} hint="of a $ spent now" />
            </div>
          </Card>

          <Card title="Claim plan" sub="Submit in this order. Distinct drops; total stays within your FAAB." pad={false} className="mb-4">
            {ready.plan.length === 0 ? (
              <p className="px-5 pb-5 text-sm text-ink-2">No claim worth placing.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead><tr className="border-b border-line"><Th>#</Th><Th>Player</Th><Th right>Bid</Th><Th right>Chance to get</Th><Th right>Win prob added</Th><Th>Drop</Th></tr></thead>
                  <tbody className="divide-y divide-line">
                    {ready.plan.map((p) => (
                      <tr key={p.player_id}>
                        <Td className="text-muted">{p.order}</Td>
                        <Td><Pos pos={p.pos} />{p.player}</Td>
                        <Td right className="font-semibold">{money(p.bid)}</Td>
                        <Td right>{pct(p.p_get, 0)}</Td>
                        <Td right>{pct(p.d_p_win, 2)}</Td>
                        <Td className="text-ink-2">{p.drop ?? "—"}</Td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot><tr className="border-t border-line"><Td className="text-muted" /><Td className="text-muted">Total if every claim wins</Td><Td right className="font-semibold">{money(ready.plan.reduce((s, p) => s + p.bid, 0))}</Td><Td /><Td /><Td /></tr></tfoot>
                </table>
              </div>
            )}
          </Card>

          <Card title="Every candidate on its own" sub="Lineup points added (next 4 weeks), win probability added, and blocking value (what the rival who’d otherwise win him gains)." pad={false}>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-line">
                    <Th>Player</Th><Th right>+pts</Th><Th right>+win</Th><Th right>Block</Th><Th right>Bid</Th><Th right>Chance</Th><Th right>Market median</Th><Th>Drop</Th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {ready.sheet.map((r) => (
                    <tr key={r.player_id} className="hover:bg-surface-2">
                      <Td><Pos pos={r.pos} />{r.player}<InjuryBadge status={r.injury} /></Td>
                      <Td right>{num(r.pts_gain)}</Td>
                      <Td right>{pct(r.d_p_win, 2)}</Td>
                      <Td right className="text-ink-2">{pct(r.deny, 2)}</Td>
                      <Td right className="font-semibold">{money(r.bid)}</Td>
                      <Td right>{pct(r.p_win_claim, 0)}</Td>
                      <Td right className="text-ink-2">{money(r.market_median)}</Td>
                      <Td className="text-ink-2">{r.drop}</Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      )}
    </>
  );
}
