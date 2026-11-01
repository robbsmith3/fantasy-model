import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import clsx from "clsx";
import { num, pct, q } from "../api";
import { RiskBadge, TeamAvatar } from "../components/brand";
import { BarList } from "../components/charts";
import { Card, ErrorBox, PageHeader, Skeleton, Stat, Td, Th } from "../components/ui";

export default function Live() {
  const live = useQuery(q.live());
  const navigate = useNavigate();
  const d = live.data;
  const me = d?.teams.find((t) => t.is_me);
  const updated = d ? new Date(d.fetched_at * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : null;

  return (
    <>
      <PageHeader
        title="Live chop odds"
        sub="Points so far plus a simulated finish for every unplayed minute. Refreshes every minute."
        right={
          <button
            onClick={() => live.refetch()}
            disabled={live.isFetching}
            className="rounded-lg border border-line bg-surface px-3 py-1.5 text-sm text-ink-2 hover:text-ink disabled:opacity-50"
          >
            {live.isFetching ? "Updating…" : `Updated ${updated ?? "–"}`}
          </button>
        }
      />
      {live.isError && <ErrorBox error={live.error} />}
      {!d && !live.isError && <Skeleton className="h-72 w-full" />}
      {d && me && (
        <div className={clsx("space-y-4 transition-opacity", live.isFetching && "opacity-70")}>
          <Card>
            <div className="grid grid-cols-2 gap-6 sm:grid-cols-4">
              <Stat hero label="Your chance of the lowest score" value={pct(me.p_chop)} hint={<RiskBadge p={me.p_chop} />} />
              <Stat label="Your points so far" value={num(me.current)} />
              <Stat label="Projected final" value={num(me.final_mean)} hint={`80% range ${num(me.final_p10, 0)}–${num(me.final_p90, 0)}`} />
              <Stat label={`Week ${d.week} games`} value={`${d.games.final}/${d.games.final + d.games.live + d.games.pre}`} hint={`${d.games.live} live · ${d.games.pre} to play`} />
            </div>
          </Card>

          <Card title="Chance of the lowest score" sub="Tap a team for its roster">
            <BarList
              rows={d.teams.map((t) => ({
                key: t.team_id,
                label: t.name,
                value: t.p_chop,
                highlight: t.is_me,
                icon: <TeamAvatar src={t.avatar} name={t.name} size={24} />,
                badge: <RiskBadge p={t.p_chop} compact />,
                detail: <>{num(t.current)} now · ~{num(t.final_mean, 0)} final · {pct(t.share_left, 0)} of games left</>,
              }))}
              fmt={(v) => pct(v)}
              onSelect={(id) => navigate({ to: "/teams/$teamId", params: { teamId: id } })}
            />
          </Card>

          <Card title="Scores" pad={false}>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-line">
                    <Th>Team</Th><Th right>Now</Th><Th right>Projected final</Th><Th right>80% range</Th><Th right>Games left</Th><Th right>Lowest score</Th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {d.teams.map((t) => (
                    <tr key={t.team_id} className={clsx(t.is_me && "bg-accent-wash")}>
                      <Td className={clsx(t.is_me && "font-semibold")}><span className="flex items-center gap-2.5"><TeamAvatar src={t.avatar} name={t.name} size={24} />{t.name}</span></Td>
                      <Td right>{num(t.current)}</Td>
                      <Td right>{num(t.final_mean)}</Td>
                      <Td right className="text-ink-2">{num(t.final_p10, 0)}–{num(t.final_p90, 0)}</Td>
                      <Td right className="text-ink-2">{pct(t.share_left, 0)}</Td>
                      <Td right className="font-semibold">{pct(t.p_chop)}</Td>
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
