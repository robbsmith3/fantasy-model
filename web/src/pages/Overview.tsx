import { useQuery, useSuspenseQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { money, num, pct, q } from "../api";
import { ActionCenter } from "../components/ActionCenter";
import { AnimatedNumber, PlayerHead, RiskBadge, TeamAvatar } from "../components/brand";
import { BarList, LineChart } from "../components/charts";
import { PlayerLink } from "../components/PlayerSheet";
import { Card, Computing, ErrorBox, PageHeader, Stat } from "../components/ui";

export default function Overview() {
  const { data: L } = useSuspenseQuery(q.league());
  const rec = useQuery(q.recommend());
  const navigate = useNavigate();
  const me = L.teams.find((t) => t.is_me)!;
  const alive = L.teams.filter((t) => t.alive);
  const firstWeek = L.chop_pending ? L.week : L.week + 1;
  const xs = me.survival.map((_, i) => firstWeek + i);
  const rivals = alive.filter((t) => !t.is_me);
  const median = me.survival.map((_, i) => {
    const v = rivals.map((t) => t.survival[i]).sort((a, b) => a - b);
    return v.length ? v[Math.floor(v.length / 2)] : 0;
  });
  const risk = [...alive].sort((a, b) => (b.p_chop_next ?? 0) - (a.p_chop_next ?? 0)).slice(0, 6);
  const rank = alive.findIndex((t) => t.is_me) + 1;

  return (
    <>
      <PageHeader
        title={
          <span className="flex items-center gap-3">
            <TeamAvatar src={me.avatar} name={me.name} size={40} />
            {me.name}
          </span>
        }
        sub={`Week ${L.week} · ${alive.length} teams alive · ${L.chops_left} chops left${L.chop_pending ? " · this week’s chop still to come" : ""}`}
      />

      <Card title="Up next" className="mb-4">
        <ActionCenter />
      </Card>

      <div className="grid gap-4 md:grid-cols-[1.1fr_1fr]">
        <Card glow>
          <div className="grid grid-cols-2 gap-6 sm:grid-cols-[1.4fr_1fr_1fr]">
            <div className="col-span-2 sm:col-span-1">
              <Stat
                hero
                label="Chance to win the league"
                value={<AnimatedNumber value={me.p_win} format={(v) => pct(v)} />}
                hint={`#${rank} of ${alive.length} by odds · simulated to the last chop, incl. rivals’ pickups`}
              />
            </div>
            <Stat
              label={L.chop_pending ? "Chopped this week" : "Chopped next week"}
              value={<AnimatedNumber value={me.p_chop_next ?? 0} format={(v) => pct(v)} />}
              hint={<RiskBadge p={me.p_chop_next} />}
            />
            <Stat label="FAAB left" value={<AnimatedNumber value={me.faab ?? 0} format={(v) => money(v)} />} />
            <Stat label="Expected chops survived" value={num(me.exp_weeks, 1)} hint={`of ${L.chops_left}`} />
          </div>
        </Card>

        <Card title="Still alive after each chop" sub="You vs. the median rival">
          <LineChart
            x={xs}
            series={[
              { name: "Median rival", values: median, tone: "neutral" },
              { name: "You", values: me.survival, tone: "accent" },
            ]}
            fmt={(v) => pct(v, 0)}
            xLabel={(w) => `Wk ${w}`}
          />
          <details className="mt-3 text-xs text-ink-2">
            <summary className="cursor-pointer select-none text-muted">Table</summary>
            <table className="mt-2 w-full tabular">
              <thead>
                <tr className="text-muted"><th className="text-left font-normal">Week</th><th className="text-right font-normal">You</th><th className="text-right font-normal">Median rival</th></tr>
              </thead>
              <tbody>
                {xs.map((w, i) => (
                  <tr key={w}><td>Wk {w}</td><td className="text-right">{pct(me.survival[i])}</td><td className="text-right">{pct(median[i])}</td></tr>
                ))}
              </tbody>
            </table>
          </details>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <Card title={L.chop_pending ? "Closest to this week’s chop" : "Closest to next week’s chop"} sub="Chance of the lowest score">
          <BarList
            rows={risk.map((t) => ({
              key: t.team_id,
              label: t.name,
              value: t.p_chop_next ?? 0,
              highlight: t.is_me,
              icon: <TeamAvatar src={t.avatar} name={t.name} size={24} />,
              badge: <RiskBadge p={t.p_chop_next} compact />,
              detail: <>FAAB {money(t.faab)} · wins league {pct(t.p_win)}</>,
            }))}
            fmt={(v) => pct(v)}
            onSelect={(id) => navigate({ to: "/teams/$teamId", params: { teamId: id } })}
          />
        </Card>

        <Card
          title="This week’s claim plan"
          right={<Link to="/waivers" className="text-xs font-medium text-accent hover:underline">Full sheet →</Link>}
        >
          {rec.isError && <ErrorBox error={rec.error} />}
          {rec.data?.status === "computing" && <Computing what="bids" since={rec.data.since} typical="~20s" />}
          {rec.data?.status === "error" && <ErrorBox error={rec.data.error} />}
          {rec.data?.status === "ready" &&
            (rec.data.data.plan.length === 0 ? (
              <p className="text-sm text-ink-2">No claim worth placing right now.</p>
            ) : (
              <ol className="divide-y divide-line">
                {rec.data.data.plan.map((p) => (
                  <li key={p.player_id} className="flex items-center justify-between gap-3 py-2.5">
                    <div className="flex min-w-0 items-center gap-3">
                      <PlayerHead id={p.player_id} name={p.player} size={36} />
                      <div className="min-w-0">
                        <div className="truncate text-sm font-medium">
                          <PlayerLink id={p.player_id}>{p.player}</PlayerLink> <span className="text-xs font-normal text-muted">{p.pos}</span>
                        </div>
                        <div className="text-xs text-muted">#{p.order} · drop {p.drop ?? "—"}{p.backup_for && ` · only if ${p.backup_for} misses`}</div>
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-base font-semibold tabular">{money(p.bid)}</div>
                      <div className="text-xs text-muted tabular">{pct(p.p_get, 0)} to get</div>
                    </div>
                  </li>
                ))}
              </ol>
            ))}
          {!rec.data && !rec.isError && <Computing what="bids" typical="~20s" />}
        </Card>
      </div>
    </>
  );
}
