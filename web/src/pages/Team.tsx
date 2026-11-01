import { useSuspenseQuery } from "@tanstack/react-query";
import { Link, useParams } from "@tanstack/react-router";
import clsx from "clsx";
import { money, num, pct, q } from "../api";
import { Card, InjuryBadge, PageHeader, Pos, Stat, Td, Th } from "../components/ui";

export default function Team() {
  const { teamId } = useParams({ strict: false }) as { teamId: string };
  const { data: T } = useSuspenseQuery(q.team(teamId));
  const { data: L } = useSuspenseQuery(q.league());
  const t = L.teams.find((x) => x.team_id === teamId);

  return (
    <>
      <PageHeader
        title={T.name}
        sub={<Link to="/league" className="hover:underline">← League</Link>}
      />
      {t && (
        <Card className="mb-4">
          <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
            <Stat label="Chance to win" value={pct(t.p_win)} />
            <Stat label="Chop next" value={pct(t.p_chop_next)} />
            <Stat label="FAAB" value={money(t.faab)} />
            <Stat label="Expected chops survived" value={num(t.exp_weeks)} />
          </div>
        </Card>
      )}
      <Card title="Roster" sub="Projected points (injury-adjusted); bold = projected starter that week" pad={false}>
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-line">
                <Th>Player</Th>
                {T.weeks.map((w) => <Th key={w} right>Wk {w}</Th>)}
                <Th right>Total</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {T.players.map((p) => (
                <tr key={p.player_id} className="hover:bg-surface-2">
                  <Td><Pos pos={p.pos} />{p.name}<InjuryBadge status={p.injury} /><span className="ml-1.5 text-xs text-muted">{p.nfl_team}</span></Td>
                  {T.weeks.map((w) => (
                    <Td key={w} right className={clsx(p.starts.includes(w) ? "font-semibold text-ink" : "text-muted")}>
                      {num(p.pts[String(w)])}
                    </Td>
                  ))}
                  <Td right className="text-ink-2">{num(p.ros)}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </>
  );
}
