import { useSuspenseQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import clsx from "clsx";
import { money, num, pct, q } from "../api";
import { BarList } from "../components/charts";
import { Card, PageHeader, Td, Th } from "../components/ui";

export default function League() {
  const { data: L } = useSuspenseQuery(q.league());
  const navigate = useNavigate();
  const alive = L.teams.filter((t) => t.alive);
  const out = L.teams.filter((t) => !t.alive).sort((a, b) => (b.chopped_week ?? 0) - (a.chopped_week ?? 0));
  const next = String(L.weeks[L.chop_pending ? 1 : 0] ?? L.weeks[0]);

  return (
    <>
      <PageHeader title="League" sub={`${alive.length} alive · ${out.length} chopped · week ${L.week}`} />
      <div className="grid gap-4 md:grid-cols-[1fr_1.4fr]">
        <Card title="Chance to win the league" sub="Includes each rival’s future FAAB pickups">
          <BarList
            rows={alive.map((t) => ({
              key: t.team_id,
              label: t.name,
              value: t.p_win,
              highlight: t.is_me,
              detail: <>FAAB {money(t.faab)} · chop next {pct(t.p_chop_next)}</>,
            }))}
            fmt={(v) => pct(v)}
            onSelect={(id) => navigate({ to: "/teams/$teamId", params: { teamId: id } })}
          />
        </Card>

        <Card title="Standings by odds" pad={false}>
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-line">
                  <Th>Team</Th><Th right>FAAB</Th><Th right>Proj wk {next}</Th><Th right>Chop next</Th><Th right>Chops survived</Th><Th right>Win</Th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {alive.map((t) => (
                  <tr key={t.team_id} className={clsx("hover:bg-surface-2", t.is_me && "bg-accent-wash")}>
                    <Td>
                      <Link to="/teams/$teamId" params={{ teamId: t.team_id }} className={clsx("hover:underline", t.is_me && "font-semibold")}>
                        {t.name}
                      </Link>
                    </Td>
                    <Td right>{money(t.faab)}</Td>
                    <Td right>{num(t.proj[next], 0)}</Td>
                    <Td right>{pct(t.p_chop_next)}</Td>
                    <Td right>{num(t.exp_weeks)}</Td>
                    <Td right className="font-semibold">{pct(t.p_win)}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>

      {out.length > 0 && (
        <Card title="Chopped" className="mt-4">
          <ul className="flex flex-wrap gap-2 text-sm">
            {out.map((t) => (
              <li key={t.team_id} className="rounded-md border border-line px-2.5 py-1 text-ink-2">
                {t.name} <span className="text-muted">· wk {t.chopped_week ?? "?"}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}
