import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { type Metrics, money, num, pct, q } from "../api";
import { Card, Computing, ErrorBox, PageHeader, Stat, Td, Th } from "../components/ui";

const SOURCES = [
  ["sleeper", "Sleeper"],
  ["espn", "ESPN"],
  ["model_wf", "Our model"],
  ["avg3", "Consensus"],
] as const;

function ProjectionTable({ rows }: { rows: Metrics[] }) {
  const best = (r: Metrics) =>
    Math.min(...SOURCES.map(([k]) => Number(r[`mae_${k}`] ?? Infinity)));
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead>
          <tr className="border-b border-line">
            <Th>Position</Th><Th right>Player-weeks</Th>
            {SOURCES.map(([k, label]) => <Th key={k} right>{label}</Th>)}
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {rows.map((r) => (
            <tr key={String(r.position)}>
              <Td className={clsx(r.position === "ALL" && "font-semibold")}>{String(r.position)}</Td>
              <Td right className="text-ink-2">{String(r.n)}</Td>
              {SOURCES.map(([k]) => {
                const v = Number(r[`mae_${k}`]);
                return (
                  <Td key={k} right className={clsx(v === best(r) && "font-semibold text-ink", v !== best(r) && "text-ink-2")}>
                    {num(v, 2)}
                  </Td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function Model() {
  const rep = useQuery(q.model());
  const meta = useQuery(q.meta());
  const d = rep.data?.status === "ready" ? rep.data.data : null;

  return (
    <>
      <PageHeader title="Model" sub="How well each part has predicted past weeks, trained only on earlier data each time." />
      {rep.isError && <ErrorBox error={rep.error} />}
      {rep.data?.status === "error" && <ErrorBox error={rep.data.error} />}
      {(rep.data?.status === "computing" || (!rep.data && !rep.isError)) && (
        <div className="mb-4">
          <Computing what="backtests" since={rep.data?.status === "computing" ? rep.data.since : undefined} typical="~30s" />
        </div>
      )}
      {d && (
        <div className="space-y-4">
          <Card title="Projections: average miss (points per player-week)" sub={`Weeks ${d.weeks[0]}–${d.weeks[d.weeks.length - 1]}. Lower is better; best in bold.`} pad={false}>
            <ProjectionTable rows={d.projections} />
          </Card>

          <div className="grid gap-4 md:grid-cols-2">
            <Card title="Chop predictions" sub="Team scores and who got chopped, past weeks">
              <div className="grid grid-cols-2 gap-5">
                <Stat label="80% ranges that held" value={pct(Number(d.survival.summary.cover80), 0)} hint="target 80%" />
                <Stat label="Spread vs reality" value={num(Number(d.survival.summary.z_sd), 2)} hint="1.00 = right width" />
                <Stat label="Log score, player sim" value={num(Number(d.survival.summary.loglik_player), 2)} hint="higher is better" />
                <Stat label="Log score, team model" value={num(Number(d.survival.summary.loglik_team), 2)} hint="used in production" />
              </div>
              <table className="mt-4 w-full text-xs">
                <thead><tr className="text-muted"><th className="text-left font-normal">Week</th><th className="text-right font-normal">Teams</th><th className="text-right font-normal">P(chopped) we gave</th><th className="text-right font-normal">Its rank</th></tr></thead>
                <tbody className="tabular">
                  {d.survival.weeks.map((w) => (
                    <tr key={String(w.week)}>
                      <td>Wk {String(w.week)}</td>
                      <td className="text-right">{String(w.teams)}</td>
                      <td className="text-right">{pct(Number(w.p_player))}</td>
                      <td className="text-right">#{String(w.rank_player)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>

            <Card title="Winning-bid predictions" sub={`${d.prices.n} contested auctions`}>
              <div className="grid grid-cols-2 gap-5">
                <Stat label="80% ranges that held" value={pct(Number(d.prices.cover80), 0)} hint="target 80%" />
                <Stat label="Median bias" value={money(Number(d.prices.bias_median))} hint="predicted − actual" />
                <Stat label="Range width" value={money(Number(d.prices.width80))} hint="80% interval" />
                <Stat label="CRPS" value={num(Number(d.prices.crps), 1)} hint="lower is better" />
              </div>
            </Card>
          </div>
        </div>
      )}

      <Card title="Recent jobs" className="mt-4" pad={false}>
        <table className="w-full">
          <thead><tr className="border-b border-line"><Th>Job</Th><Th>Started</Th><Th right>Result</Th></tr></thead>
          <tbody className="divide-y divide-line">
            {meta.data?.runs.map((r) => (
              <tr key={r.id}>
                <Td>{r.job.replace("_", " ")}</Td>
                <Td className="text-ink-2 tabular">{r.started.slice(0, 16)}</Td>
                <Td right className={clsx(r.ok === false ? "text-critical" : "text-good")}>
                  {r.ok === false ? "failed" : r.ok ? "ok" : "running"}
                </Td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </>
  );
}
